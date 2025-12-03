import json
import boto3
import os
import re
import logging

# Import centralized error handling
from ErrorHandler import log_error_with_context

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')

# Default model list to use when models field is missing or empty
DEFAULT_MODELS = ['us.anthropic.claude-3-7-sonnet-20250219-v1:0']


def validate_model_identifier(model_id: str) -> bool:
    """Validate Bedrock model identifier format.
    
    Valid formats:
    - Standard: provider.model-name-version:variant
      Example: anthropic.claude-3-sonnet-20240229-v1:0
    - Cross-region inference: region.provider.model-name-version:variant
      Example: us.anthropic.claude-sonnet-4-20250514-v1:0
    
    Args:
        model_id: The model identifier string to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not model_id or not isinstance(model_id, str):
        return False
    
    # Pattern supports both standard and cross-region inference formats
    # Allows 2-3 dot-separated segments (region.provider.model or provider.model)
    # followed by colon and numeric variant
    pattern = r'^[a-z0-9\-]+\.[a-z0-9\-]+(\.[a-z0-9\-]+)?:[0-9]+$'
    return bool(re.match(pattern, model_id))


def load_config(config_path: str) -> dict:
    """Load configuration from JSON file.
    
    Returns dict with 'models' and 'services' arrays.
    Provides default models if 'models' field is missing.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        dict: Configuration with 'models' and 'services' keys
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # Handle both old format (array) and new format (object)
    if isinstance(config, list):
        # Old format: array of services
        logger.info("Loading configuration in old format (array of services)")
        return {
            'models': DEFAULT_MODELS.copy(),
            'services': config
        }
    
    # New format: object with models and services
    if 'models' not in config or not config['models']:
        logger.warning("Configuration missing 'models' field or empty, using default models")
        config['models'] = DEFAULT_MODELS.copy()
    
    if 'services' not in config:
        logger.warning("Configuration missing 'services' field, using empty array")
        config['services'] = []
    
    return config


def load_config_with_validation(config_path: str) -> dict:
    """Load configuration and validate model identifiers.
    
    Invalid model identifiers are logged and skipped.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        dict: Configuration with validated models and services
    """
    config = load_config(config_path)
    
    valid_models = []
    invalid_count = 0
    
    for model_id in config.get('models', []):
        if validate_model_identifier(model_id):
            valid_models.append(model_id)
        else:
            invalid_count += 1
            logger.error(f"Invalid model identifier skipped: {model_id}")
    
    if invalid_count > 0:
        logger.warning(f"Skipped {invalid_count} invalid model identifier(s)")
    
    config['models'] = valid_models
    
    # If all models were invalid, use default
    if not valid_models:
        logger.warning("All model identifiers were invalid, using default models")
        config['models'] = DEFAULT_MODELS.copy()
    
    return config


def lambda_handler(event, context):
    """Lambda handler for loading EOL Tracker configuration.
    
    Reads EOLTracker_config.json and returns both models and services arrays.
    Validates model identifiers and provides defaults when needed.
    
    Args:
        event: Lambda event (unused)
        context: Lambda context
        
    Returns:
        dict: Response with statusCode, models, and services
    """
    try:
        # Load config from local file (renamed from aws_services.json)
        config = load_config_with_validation('cfg/EOLTracker_config.json')
        
        logger.info(f"Loaded {len(config['models'])} model(s) and {len(config['services'])} service(s)")
        
        return {
            'statusCode': 200,
            'models': config['models'],
            'services': config['services']
        }
    
    except FileNotFoundError:
        logger.error("Configuration file not found: cfg/EOLTracker_config.json")
        # Return default configuration
        return {
            'statusCode': 200,
            'models': DEFAULT_MODELS.copy(),
            'services': []
        }
    
    except json.JSONDecodeError as e:
        # Use centralized error logging
        log_error_with_context(
            message=f"Invalid JSON in configuration file: {str(e)}",
            error_type="JSONDecodeError"
        )
        # Return default configuration
        return {
            'statusCode': 200,
            'models': DEFAULT_MODELS.copy(),
            'services': []
        }
    
    except Exception as e:
        # Use centralized error logging
        log_error_with_context(
            message=f"Unexpected error loading configuration: {str(e)}",
            error_type=type(e).__name__
        )
        return {
            'statusCode': 500,
            'error': str(e),
            'models': DEFAULT_MODELS.copy(),
            'services': []
        }
