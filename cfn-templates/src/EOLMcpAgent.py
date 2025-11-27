from mcp import StdioServerParameters, stdio_client
from strands import Agent, tool
from strands.tools.mcp import MCPClient
from strands.models import BedrockModel

import json
import re
import logging
import os
import sys
import tempfile
import boto3
from botocore.config import Config
from datetime import datetime
from typing import List, Dict, Optional
import jsonschema
import socket

# Increase socket timeout for VPC
socket.setdefaulttimeout(600)

# Configure boto3 with increased timeout
boto3_config = Config(
    retries={
        'max_attempts': 10,
        'mode': 'standard'
    },
    connect_timeout=10,
    read_timeout=10
)
os.environ['AWS_METADATA_SERVICE_TIMEOUT'] = '10'
os.environ['AWS_METADATA_SERVICE_NUM_ATTEMPTS'] = '3'

# Bedrock - disable streaming to avoid timeout issues in VPC
bedrock_model = BedrockModel(
  model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
  temperature=0.1,
  streaming=True
)

# Configure logging (Lambda-friendly - no file handler)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def validate_eol_schema(data):
    """Validate EOL data against expected schema.
    
    Args:
        data: Dictionary containing EOL data to validate
        
    Returns:
        Tuple of (is_valid: bool, errors: List[str])
    """
    schema = {
        "type": "object",
        "required": ["service", "cycle", "lts", "releaseDate", "supportEndDate", "eol", "latest", "link", "lastUpdated"],
        "properties": {
            "service": {
                "type": "string", 
                "minLength": 1,
                "description": "AWS service name"
            },
            "cycle": {
                "type": ["string", "null"],
                "description": "Version or release cycle identifier"
            },
            "lts": {
                "type": "boolean",
                "description": "Long-term support status"
            },
            "releaseDate": {
                "type": ["string", "null"],
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                "description": "Release date in YYYY-MM-DD format"
            },
            "supportEndDate": {
                "type": ["string", "null"],
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                "description": "Support end date in YYYY-MM-DD format"
            },
            "eol": {
                "type": ["string", "null"],
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                "description": "End of life date in YYYY-MM-DD format"
            },
            "latest": {
                "type": ["string", "null"],
                "description": "Latest version in this cycle"
            },
            "link": {
                "type": ["string", "null"],
                "format": "uri",
                "description": "URL to official documentation"
            },
            "lastUpdated": {
                "type": "string",
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                "description": "Date this entry was last updated"
            }
        },
        "additionalProperties": True  # Allow validation metadata
    }
    
    errors = []
    
    try:
        # Basic schema validation
        jsonschema.validate(data, schema)
        
        # Additional custom validations
        service_name = data.get('service', '')
        if service_name and len(service_name.strip()) == 0:
            errors.append("Service name cannot be empty or whitespace only")
        
        # Validate date formats more strictly
        date_fields = ['releaseDate', 'supportEndDate', 'eol']
        for field in date_fields:
            value = data.get(field)
            if value and value != "null":
                try:
                    datetime.strptime(value, '%Y-%m-%d')
                except ValueError:
                    errors.append(f"Invalid date format in {field}: {value}. Expected YYYY-MM-DD")
        
        # Validate URL format if present
        link = data.get('link')
        if link and link != "null" and not link.startswith(('http://', 'https://')):
            errors.append(f"Invalid URL format in link: {link}")
        
        # Validate boolean field
        lts = data.get('lts')
        if lts is not None and not isinstance(lts, bool):
            errors.append(f"Field 'lts' must be boolean, got: {type(lts).__name__}")
            
        return len(errors) == 0, errors
        
    except jsonschema.ValidationError as e:
        errors.append(f"Schema validation failed: {e.message}")
        return False, errors
    except Exception as e:
        errors.append(f"Unexpected validation error: {str(e)}")
        return False, errors

def validate_date_logic(data):
    """Validate that dates follow logical order and are reasonable.
    
    Args:
        data: Dictionary containing EOL data with date fields
        
    Returns:
        Tuple of (is_valid: bool, errors: List[str])
    """
    errors = []
    
    try:
        # Parse dates, handling null values
        def parse_date_safe(date_str, field_name):
            if not date_str or date_str == "null":
                return None
            try:
                return datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                errors.append(f"Invalid date format in {field_name}: {date_str}")
                return None
        
        release_date = parse_date_safe(data.get('releaseDate'), 'releaseDate')
        support_end = parse_date_safe(data.get('supportEndDate'), 'supportEndDate')
        eol_date = parse_date_safe(data.get('eol'), 'eol')
        
        current_date = datetime.now()
        
        # Date logic validations
        if release_date and support_end:
            if release_date > support_end:
                errors.append("Release date cannot be after support end date")
        
        if support_end and eol_date:
            if support_end > eol_date:
                errors.append("Support end date cannot be after EOL date")
        
        if release_date and eol_date:
            if release_date > eol_date:
                errors.append("Release date cannot be after EOL date")
        
        # Reasonable date range validations
        earliest_reasonable = datetime(2000, 1, 1)  # AWS started in 2006, but allow some buffer
        latest_reasonable = datetime(current_date.year + 15, 12, 31)  # 15 years in future max
        
        date_checks = [
            (release_date, 'releaseDate'),
            (support_end, 'supportEndDate'),
            (eol_date, 'eol')
        ]
        
        for date_obj, field_name in date_checks:
            if date_obj:
                if date_obj < earliest_reasonable:
                    errors.append(f"{field_name} is unreasonably early: {date_obj.strftime('%Y-%m-%d')}")
                elif date_obj > latest_reasonable:
                    errors.append(f"{field_name} is unreasonably far in the future: {date_obj.strftime('%Y-%m-%d')}")
        

        
        # Service lifecycle logic
        if release_date and eol_date:
            lifecycle_duration = (eol_date - release_date).days
            if lifecycle_duration < 365:  # Less than 1 year
                errors.append(f"Service lifecycle seems too short: {lifecycle_duration} days")
            elif lifecycle_duration > 365 * 20:  # More than 20 years
                errors.append(f"Service lifecycle seems too long: {lifecycle_duration} days")
        
        return len(errors) == 0, errors
        
    except Exception as e:
        errors.append(f"Unexpected error in date validation: {str(e)}")
        return False, errors

def clean_json_response(response: str) -> str:
    """Clean and validate JSON response from the agent.
    
    Args:
        response: Raw response from the agent
        
    Returns:
        Clean JSON string
    """
    try:
        # Look for JSON array or object
        start_array = response.find("[")
        start_object = response.find("{")
        
        # Determine which comes first (or if only one exists)
        if start_array == -1 and start_object == -1:
            raise ValueError("No JSON array or object found in response")
        
        # Choose the first JSON structure found
        if start_array == -1:
            start_pos = start_object
            start_char = "{"
            end_char = "}"
        elif start_object == -1:
            start_pos = start_array
            start_char = "["
            end_char = "]"
        else:
            # Both exist, choose the first one
            if start_array < start_object:
                start_pos = start_array
                start_char = "["
                end_char = "]"
            else:
                start_pos = start_object
                start_char = "{"
                end_char = "}"
        
        # Discard everything before the JSON starts (including headers and markdown)
        response = response[start_pos:]
        
        # Find the matching closing bracket/brace
        bracket_count = 1  # We already found the opening bracket/brace
        end_pos = -1
        
        for i in range(1, len(response)):
            if response[i] == start_char:
                bracket_count += 1
            elif response[i] == end_char:
                bracket_count -= 1
                if bracket_count == 0:
                    end_pos = i
                    break
        
        if end_pos == -1:
            raise ValueError(f"No matching closing {end_char} found")
        
        # Extract JSON content - keep the full structure with brackets/braces
        json_content = response[:end_pos + 1]
        
        # Remove any trailing markdown formatting (like closing ```)
        if "```" in json_content:
            json_content = json_content[:json_content.find("```")].strip()
        
        # Validate JSON
        parsed = json.loads(json_content)
        
        # Return formatted JSON
        return json.dumps(parsed, indent=2)
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse JSON response: {e}")
        logger.warning(f"Raw response: {response[:500]}...")
        
        # Return error JSON if parsing fails
        error_response = {
            "error": "Failed to parse agent response as JSON",
            "raw_response": response[:1000],  # Truncate for readability
            "extraction_date": datetime.now().strftime("%Y-%m-%d")
        }
        return json.dumps(error_response, indent=2)

def load_service_config(config_file: str = "cfg/aws_services.json") -> List[Dict]:
    """Load service configuration from JSON file.
    
    Args:
        config_file: Path to the configuration file
        
    Returns:
        List of service configurations
    """
    try:
        config_file = os.path.join(os.path.dirname(__file__), 'cfg', 'aws_services.json')
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
            # Handle both list format and object format
            if isinstance(config, list):
                return config
            elif isinstance(config, dict):
                return config.get('services', [])
            else:
                logger.error("Configuration file must contain either a list or an object with 'services' key")
                return []
                
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        return []

@tool
def aws_eol_extractor(service_url: str, service_name: Optional[str] = None) -> str:
    """Extract EOL information from AWS service documentation.
    
    Args:
        service_url: URL to the AWS service documentation page
        service_name: Optional name of the service for better logging
        
    Returns:
        JSON formatted EOL information for the AWS service
    """
    
    service_display = f"{service_name} ({service_url})" if service_name else service_url
    logger.info(f"Starting EOL extraction for: {service_display}")
    
    try:
        logger.info("Initializing MCP client for AWS documentation server...")
        
        # Find the site-packages directory
        site_packages_path = None
        for path in sys.path:
            if path.endswith('site-packages'):
                site_packages_path = path
                break
        
        # Try to find uvx executable in various possible locations
        uvx_path = 'uvx'  # Default fallback value
        possible_locations = []
        found_executable = False
        
        # Check in site-packages/bin (standard location in the layer)
        if site_packages_path:
            bin_path = os.path.join(site_packages_path, 'bin', 'uvx')
            possible_locations.append(bin_path)
            if os.path.isfile(bin_path):
                uvx_path = bin_path
                found_executable = True
                logger.info(f"Found uvx at {bin_path}")
        
        # Check in /opt/python/bin (another possible Lambda layer location)
        if not found_executable:
            opt_path = '/opt/python/lib/python3.12/site-packages/bin/uvx'
            possible_locations.append(opt_path)
            if os.path.isfile(opt_path):
                uvx_path = opt_path
                found_executable = True
                logger.info(f"Found uvx at {opt_path}")
        
        # Check in other common Lambda layer locations
        if not found_executable:
            other_paths = [
                '/var/task/bin/uvx',
                '/var/runtime/bin/uvx',
                '/opt/bin/uvx'
            ]
            for path in other_paths:
                possible_locations.append(path)
                if os.path.isfile(path):
                    uvx_path = path
                    found_executable = True
                    logger.info(f"Found uvx at {path}")
                    break
        
        # Add the PATH option to our list of checked locations
        if not found_executable:
            possible_locations.append('uvx (in PATH)')
            logger.warning("Could not find uvx executable in any expected location. Will try using 'uvx' from PATH.")
            
            logger.info(f"Using uvx path: {uvx_path}")
            logger.info(f"Checked locations: {possible_locations}")
            
            # List all files in the bin directory for debugging
            if site_packages_path and os.path.isdir(os.path.join(site_packages_path, 'bin')):
                bin_dir = os.path.join(site_packages_path, 'bin')
                logger.info(f"Contents of {bin_dir}: {os.listdir(bin_dir)}")
        
        # Create secure temporary directories
        temp_dir = tempfile.mkdtemp()
        uv_cache_dir = tempfile.mkdtemp()
        
        documentation_mcp_server = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command=uvx_path,  # Use the full path instead of just "uvx" 
                    args=["awslabs.aws-documentation-mcp-server@latest"],
                    env={
                        "FASTMCP_LOG_LEVEL": "INFO",
                        "AWS_DOCUMENTATION_PARTITION": "aws",
                        "UV_CACHE_DIR": uv_cache_dir,
                        "HOME": temp_dir
                        }
                )
            )
        )
        logger.info("MCP client initialized successfully")
        
        with documentation_mcp_server:
            logger.info("Connected to MCP server, listing available tools...")
            tools = documentation_mcp_server.list_tools_sync()
            logger.info(f"Found {len(tools)} tools available from MCP server")
            
            logger.info("Creating EOL extraction agent...")
            # Get current timestamp for lastUpdated field
            current_timestamp = datetime.now().strftime("%Y-%m-%d")
            
            # Create the EOL extraction agent
            eol_agent = Agent(
                model=bedrock_model,
                system_prompt=f"""You are an AWS documentation analyst specialized in extracting End of Life (EOL) information from AWS service documentation.

Your task:
1. Read and analyze the provided AWS service URL: {service_url}
2. Include the following key-value pairs for each entry, ensuring 
        all fields are present even if the value is null: 
        - "service": Name of the AWS service (use strictly from the following service names list: "Amazon RDS", "Amazon Lambda", "Amazon MSK", "Amazon EKS") 
        - "cycle": Version or release cycle identifier (e.g., "1.32" for EKS, "python3.9" for Lambda, "MySQL 5.7" for RDS, "nodejs20.x" for Lambda runtimes, "3.8.x" or "2.6.0" or "2.6.1" for MSK)
        - "lts": Boolean indicating long-term support status (true/false) 
        - "releaseDate": Initial release date in YYYY-MM-DD format 
        - "supportEndDate": Date until which standard support is provided (YYYY-MM-DD format) 
        - "eol": Official end-of-life date (YYYY-MM-DD format) 
        - "latest": Most recent version in this cycle 
        - "link": URL to official AWS documentation or release notes that 
        provides end of life or end of support dates. Ensure that all 
        extracted data is current, accurate, and directly sourced from 
        official AWS documentation. Verify the correctness of date formats 
        (YYYY-MM-DD) and proper representation of boolean values (true/false). 
        Your response should be comprehensive, covering a diverse range of AWS 
        services and their respective versions. Maintain consistency in 
        formatting and data presentation across all entries. Create one json 
        entry for each end of life or end of support data for an AWS Service. 
        Use null values for any unavailable information rather than omitting fields. 
        The extracted data will be instrumental for AWS customers and partners 
        in planning infrastructure upgrades and ensuring compliance with AWS 
        support policies. Therefore, prioritize accuracy, completeness, and 
        clarity in your response.
        - "lastUpdated": {current_timestamp}

3. Structure the information in the following JSON format:
{{
    "service": "string",
    "cycle": "string",
    "lts": "bool",
    "releaseDate": "YYYY-MM-DD",
    "supportEndDate": "YYYY-MM-DD",
    "eol": "YYYY-MM-DD",
    "latest": "string",
    "link": "url,"
    "lastUpdated": "{current_timestamp}"
}}

4. If no specific EOL information is found, indicate this in the JSON structure

CRITICAL: Your response must be ONLY valid JSON. Do not include any explanatory text, markdown formatting, or code blocks. Return only the raw JSON object starting with {{ and ending with }}. No other text before or after the JSON.

Focus only on factual EOL information from official AWS documentation.""",
                tools=tools,
            )
            logger.info("Agent created successfully")
            
            logger.info("Executing EOL extraction query...")
            query = f"Extract EOL information from this AWS service documentation URL: {service_url}"
            response = str(eol_agent(query))

            # If the response contains the word error, return an error
            if "error" in response.lower():
                logger.error(f"Error during EOL extraction: {response}")
                error_response = {
                    "error": "Failed to extract EOL information",
                    "service_url": service_url,
                    "extraction_date": datetime.now().strftime("%Y-%m-%d")
                }
                return json.dumps(error_response, indent=2)
            else:
                logger.info("EOL extraction completed successfully")
                logger.info(f"Response : {response}")
                logger.info(f"Response length: {len(response)} characters")
                
                # Clean and validate JSON response
                cleaned_response = clean_json_response(response)
                logger.info(f"Cleaned response : {cleaned_response}")  # Log the cleaned response, not the original
            
                return cleaned_response
            
    except Exception as e:
        logger.error(f"Error during EOL extraction: {str(e)}", exc_info=True)
        error_response = {
            "error": f"Failed to extract EOL information: {str(e)}",
            "service_url": service_url,
            "extraction_date": datetime.now().strftime("%Y-%m-%d")
        }
        return json.dumps(error_response, indent=2)

def save_to_s3(data: list, service_name: str = None) -> None:
    """Save EOL data to S3 bucket.
    
    Args:
        data: List of validated EOL records to save
        service_name: Optional service name for individual file, if None saves as aggregated file
    """
    s3_bucket_name = os.environ.get('S3_BUCKET_NAME')
    
    if not s3_bucket_name:
        logger.warning("S3_BUCKET_NAME environment variable not set. Skipping S3 storage.")
        return
    
    try:
        config = Config(connect_timeout=120, read_timeout=300)
        s3_client = boto3.client('s3', config=config)
        
        if service_name:
            # Individual service file
            safe_service_name = service_name.replace(' ', '_').lower()
            s3_file_key = f"eol_results/{safe_service_name}.json"
        else:
            # Aggregated file for all services
            s3_file_key = "eol_results/eol_mcp_data.json"
        
        s3_client.put_object(
            Bucket=s3_bucket_name,
            Key=s3_file_key,
            Body=json.dumps(data),
            ContentType='application/json'
        )
        logger.info(f"Successfully saved EOL results to s3://{s3_bucket_name}/{s3_file_key}")
        
    except Exception as s3_error:
        logger.error(f"Failed to save results to S3: {str(s3_error)}")

def has_error_in_result(data):
    if isinstance(data, dict):
        # Check for explicit error field
        if 'error' in data:
            return True, data.get('error', 'Unknown error')
        # Check for error keywords in values
        for key, value in data.items():
            if isinstance(value, str) and 'error' in value.lower():
                return True, f"{key}: {value}"
    return False, None

def process_single_service(service_config: Dict) -> Dict:
    """Process a single AWS service for EOL information.
    
    Args:
        service_config: Dictionary containing service_name, service_url, and description
        
    Returns:
        Dictionary containing results for the single service
    """
    service_name = service_config.get('service_name', 'Unknown Service')
    service_url = service_config.get('service_url', '')
    
    logger.info(f"=== Processing single service: {service_name} ===")
    
    if not service_url:
        logger.error(f"No URL provided for service '{service_name}'")
        return {
            'service': service_name,
            'error': 'No service URL provided',
            'extraction_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    try:
        # Extract EOL information
        result = aws_eol_extractor(service_url, service_name)
        result_data = json.loads(result)
        logger.info(f"Extracted data: {result_data}")
        
        # Check for extraction errors
        has_error, error_msg = has_error_in_result(result_data)
        if has_error:
            logger.error(f"Service {service_name} returned error: {error_msg}")
            return {
                'service': service_name,
                'error': error_msg,
                'extraction_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        
        # Validate extracted data
        validated_results = []
        if isinstance(result_data, list):
            for record in result_data:
                schema_valid, schema_errors = validate_eol_schema(record)
                date_valid, date_errors = validate_date_logic(record)
                

                
                validated_results.append(record)
                
                if schema_valid and date_valid:
                    logger.info(f"✓ Validation passed for {record.get('service', 'Unknown')}")
                else:
                    logger.warning(f"⚠ Validation issues for {record.get('service', 'Unknown')}")
        else:
            schema_valid, schema_errors = validate_eol_schema(result_data)
            date_valid, date_errors = validate_date_logic(result_data)
            

            
            validated_results.append(result_data)
            
            if schema_valid and date_valid:
                logger.info(f"✓ Validation passed for {result_data.get('service', 'Unknown')}")
            else:
                logger.warning(f"⚠ Validation issues for {result_data.get('service', 'Unknown')}")
        
        logger.info(f"✓ Successfully processed {service_name}")
        
        # Save to S3 with service-specific filename
        save_to_s3(validated_results, service_name)
        
        return {
            'service': service_name,
            'service_name': service_name,
            'extraction_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'results': validated_results
        }
        
    except Exception as e:
        logger.error(f"✗ Failed to process {service_name}: {str(e)}")
        return {
            'service': service_name,
            'error': str(e),
            'extraction_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

def process_services(config_file: str = "cfg/aws_services.json") -> Dict:
    """Process  AWS services for EOL information.
    
    Args:
        config_file: Path to the service configuration file
        
    Returns:
        Dictionary containing all results and summary
    """
    logger.info("=== Starting batch EOL extraction ===")
    
    # Load service configurations
    services = load_service_config(config_file)
    if not services:
        logger.error("No services found in configuration file")
        return {
            "extraction_summary": {
                "total_services": 0,
                "successful_extractions": 0,
                "failed_extractions": 0,
                "extraction_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "results": []
        }
    
    logger.info(f"Found {len(services)} services to process")
    logger.info(f"List of services: {json.dumps(services)}")
    
    # Process each service
    all_results = []
    successful_extractions = 0
    failed_extractions = 0
    
    for i, service in enumerate(services, 1):
        service_name = service.get('service_name', 'Unknown Service')
        service_url = service.get('service_url', 'Unknown Url')
        
        if not service_url:
            logger.warning(f"Skipping service '{service_name}' - no URL provided")
            continue
            
        logger.info(f"Processing service {i}/{len(services)}: {service_name}")
        
        try:
            # Extract EOL information
            result = aws_eol_extractor(service_url, service_name)

            result_data = json.loads(result)
            logger.info(f"Extracted data: {result_data}")
            
            # Check for extraction errors first
            has_error, error_msg = has_error_in_result(result_data)
            if has_error:
                logger.error(f"Service {service_name} returned error: {error_msg}")
                failed_extractions += 1
                continue
            
            # Validate extracted data
            if isinstance(result_data, list):
                validated_results = []
                for record in result_data:
                    # Schema validation
                    schema_valid, schema_errors = validate_eol_schema(record)
                    if not schema_valid:
                        logger.error(f"Schema validation failed for {record.get('service', 'Unknown')}: {schema_errors}")
                    
                    # Date logic validation
                    date_valid, date_errors = validate_date_logic(record)
                    if not date_valid:
                        logger.error(f"Date validation failed for {record.get('service', 'Unknown')}: {date_errors}")
                    
                    # Validation performed but not stored in record
                    
                    validated_results.append(record)
                    if schema_valid and date_valid:
                        logger.info(f"✓ Validation passed for {record.get('service', 'Unknown')}")
                    else:
                        logger.warning(f"⚠ Validation issues found for {record.get('service', 'Unknown')}, but record included")
                
                all_results.extend(validated_results)
            else:
                # Schema validation
                schema_valid, schema_errors = validate_eol_schema(result_data)
                if not schema_valid:
                    logger.error(f"Schema validation failed for {result_data.get('service', 'Unknown')}: {schema_errors}")
                
                # Date logic validation
                date_valid, date_errors = validate_date_logic(result_data)
                if not date_valid:
                    logger.error(f"Date validation failed for {result_data.get('service', 'Unknown')}: {date_errors}")
                
                # Validation performed but not stored in record
                
                all_results.append(result_data)
                if schema_valid and date_valid:
                    logger.info(f"✓ Validation passed for {result_data.get('service', 'Unknown')}")
                else:
                    logger.warning(f"⚠ Validation issues found for {result_data.get('service', 'Unknown')}, but record included")
            
            logger.info(f"✓ Successfully processed {service_name}")
            successful_extractions += 1
            
        except Exception as e:
            logger.error(f"✗ Failed to process {service_name}: {str(e)}")
            failed_extractions += 1
            continue
    
    logger.info("=== Batch extraction completed ===")
    logger.info(f"Summary: {successful_extractions} successful, {failed_extractions} failed")
    logger.info(f"Final json results: {json.dumps(all_results)}")

    # Save aggregated results to S3
    save_to_s3(all_results)
    
    return {
    "extraction_summary": {
        "total_services": len(services),
        "successful_extractions": successful_extractions,
        "failed_extractions": failed_extractions,
        "extraction_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    },
    "results": all_results
}

def lambda_handler(event, context):
    """AWS Lambda handler function."""
    import json

    try:
        logger.info(f"Lambda invoked with event: {json.dumps(event, default=str)}")    

        # Check if event contains a single service configuration from Step Function Map state
        if 'service_name' in event and 'service_url' in event:
            logger.info("Processing single service from event")
            result = process_single_service(event)
            
            # Raise exception if processing failed to trigger Step Function retry
            if 'error' in result:
                error_msg = f"Failed to process service {event.get('service_name', 'Unknown')}: {result['error']}"
                logger.warning(error_msg)
                raise Exception(error_msg)
            
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps(result)
            }
        else:
            # No service in event, run batch processing of all services
            logger.info("Starting batch processing of all configured services")
            results = process_services()
            
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps(results)
            }
            
    except Exception as e:
        logger.warning(f"Lambda execution failed: {str(e)}", exc_info=True)
        # Re-raise the exception to trigger Step Function retry mechanism
        raise e
