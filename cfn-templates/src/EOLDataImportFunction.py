import json
import boto3
import logging
import os
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_data_from_s3(bucket_name, service_name):
    """
    Retrieve EOL data from S3 bucket
    
    Args:
        bucket_name: Name of the S3 bucket
        service_name: Name of the service (used for file naming)
        
    Returns:
        List of EOL data items or None if retrieval fails
    """
    try:
        logger.info(f"Starting get_data_from_s3 function - bucket: {bucket_name}, service: {service_name}")
        
        # Initialize S3 client to use private VPC endpoint
        import botocore.config
        logger.info("Imported botocore.config successfully")
        
        # Configure S3 client with timeout settings - VPC endpoint will be used automatically
        config = botocore.config.Config(
            read_timeout=30,
            connect_timeout=10,
            retries={'max_attempts': 2},
            s3={
                'addressing_style': 'path'  # Use path-style addressing for VPC endpoints
            }
        )
        logger.info("Created S3 client configuration")
        
        # Create S3 client - VPC Gateway endpoint will route traffic automatically
        logger.info("About to create S3 client...")
        s3_client = boto3.client('s3', config=config)
        logger.info("Successfully created S3 client to use private VPC Gateway endpoint")
        
        # Create the file path
        s3_file_key = f"eol_results/{service_name}.json"
        
        logger.info(f"In get_data_from_s3 attempting to retrieve object from S3: bucket={bucket_name}, key={s3_file_key}")
        
        # First, check if the object exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_file_key)
            logger.info(f"Object exists in S3: {s3_file_key}")
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == '404':
                logger.warning(f"Object not found in S3: {s3_file_key}")
                return None
            else:
                logger.error(f"Error checking object existence: {str(e)}")
                return None
        
        # Get the object from S3
        logger.info(f"Starting S3 get_object operation...")
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_file_key)
        logger.info(f"S3 get_object completed successfully")
        
        file_content = response['Body'].read().decode('utf-8')
        logger.info(f"Successfully read {len(file_content)} characters from S3 object")
        
        # Log raw content for debugging (truncated if too long)
        logger.info(f"Retrieved content from S3: {file_content[:200]}..." if len(file_content) > 200 else file_content)
        
        try:
            # Parse the JSON content
            json_data = json.loads(file_content)
            logger.info(f"Successfully parsed JSON data {json.dumps(json_data)}")
            
            # Check if data is already in the expected array format
            if isinstance(json_data, list):
                logger.info(f"Found array of {len(json_data)} items in JSON data")
                # Validate each item has the required fields
                valid_items = []
                for item in json_data:
                    if not isinstance(item, dict):
                        logger.warning(f"JSON array contains non-object item: {item}")
                        continue
                        
                    # Ensure required fields exist (add defaults if not)
                    item.setdefault("service", service_name)
                    item.setdefault("cycle", "Unknown")
                    item.setdefault("lts", None)
                    item.setdefault("releaseDate", None)
                    item.setdefault("supportEndDate", None)
                    item.setdefault("eol", None)
                    item.setdefault("latest", None)
                    item.setdefault("link", None)
                    item.setdefault("lastUpdated", None)
                    
                    valid_items.append(item)
                
                logger.info(f"Returning {len(valid_items)} valid items")
                return valid_items
            else:
                logger.warning(f"JSON data from S3 is not an array: {type(json_data)}")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {str(e)}")
            logger.error(f"Invalid JSON content: {file_content[:500]}...")
            return None
        
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
            logger.warning(f"No S3 file found at eol_results/{service_name}.json")
        else:
            logger.error(f"Error retrieving data from S3: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error retrieving data from S3: {str(e)}")
        return None

def lambda_handler(event, context):
    """
    Lambda function to import AWS service EOL data from JSON file into DynamoDB
    """
    try:
        # Initialize DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ.get("TABLE_NAME", "EOLTrackerDB")
        table = dynamodb.Table(table_name)

        # Get data from event or S3, no fallback to hardcoded data
        if "data" in event:
            eol_data = event["data"]
            logger.info("Using data provided in the event")
        elif "results" in event:
            eol_data = event["results"]
            logger.info(f"Using results array from event with {len(eol_data)} items")
        else:
            # Try to get data from S3
            s3_bucket_name = os.environ.get("S3_BUCKET_NAME")
            service_name = event.get("service_name", event.get("service", "eol_mcp_data"))  # Try service_name, then service
            
            if not s3_bucket_name:
                error_msg = "S3_BUCKET_NAME environment variable not configured"
                logger.error(error_msg)
                return {
                    "statusCode": 500,
                    "body": json.dumps({"message": error_msg})
                }
                
            logger.info(f"In Lambda Handler: Attempting to retrieve data from S3 bucket {s3_bucket_name} for service {service_name}")
            s3_data = get_data_from_s3(s3_bucket_name, service_name)
            
            if not s3_data or len(s3_data) == 0:
                error_msg = f"No valid data found in S3 bucket {s3_bucket_name} for service {service_name}"
                logger.error(error_msg)
                return {
                    "statusCode": 404,
                    "body": json.dumps({"message": error_msg})
                }
                
            eol_data = s3_data
            logger.info(f"Successfully retrieved {len(eol_data)} items from S3")

        # Import data into DynamoDB
        imported_count = 0
        for item in eol_data:
            # Add additional attributes if needed
            table.put_item(Item=item)
            imported_count += 1

        logger.info(
            f"Imported {imported_count} records into DynamoDB table {table_name}"
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Imported {imported_count} records into DynamoDB",
                    "count": imported_count,
                }
            ),
        }

    except Exception as e:
        logger.error(f"Error importing data: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": f"Error importing data: {str(e)}"}),
        }
