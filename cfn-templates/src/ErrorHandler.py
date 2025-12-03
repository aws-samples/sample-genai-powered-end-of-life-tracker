"""
Centralized error handling module for EOL Tracker.

This module provides consistent error handling, logging, and storage
across all Lambda functions with model context support.
"""

import json
import logging
import boto3
import os
from datetime import datetime
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_model_extraction_error(
    error: Exception,
    model_id: str,
    service_name: str,
    error_type: Optional[str] = None,
    additional_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Centralized error handling for model extraction failures.
    
    Logs error with full context including model_id and service_name,
    stores error details in S3, and returns structured error response.
    
    Args:
        error: The exception that occurred
        model_id: Bedrock model identifier
        service_name: AWS service name being processed
        error_type: Optional custom error type classification
        additional_context: Optional additional context to include
        
    Returns:
        Dictionary containing structured error information
    """
    timestamp = datetime.now().isoformat()
    
    # Build error context with model and service information
    error_context = {
        'model_id': model_id,
        'service_name': service_name,
        'error_type': error_type or type(error).__name__,
        'error_message': str(error),
        'timestamp': timestamp
    }
    
    # Add any additional context
    if additional_context:
        error_context.update(additional_context)
    
    # Log error with full context (includes model_id and service_name)
    logger.error(
        f"Model extraction failed - Model: {model_id}, Service: {service_name}, "
        f"Error: {error_context['error_type']}: {error_context['error_message']}"
    )
    logger.error(f"Full error context: {json.dumps(error_context)}")
    
    # Store error details in S3 for analysis
    try:
        store_error_in_s3(error_context, service_name, model_id, timestamp)
    except Exception as s3_error:
        logger.error(f"Failed to store error in S3: {str(s3_error)}")
    
    return error_context


def store_error_in_s3(
    error_context: Dict[str, Any],
    service_name: str,
    model_id: str,
    timestamp: str
) -> None:
    """
    Store error details in S3 with model context.
    
    Args:
        error_context: Dictionary containing error information
        service_name: AWS service name
        model_id: Bedrock model identifier
        timestamp: ISO format timestamp
    """
    s3_bucket_name = os.environ.get('S3_BUCKET_NAME')
    
    if not s3_bucket_name:
        logger.warning("S3_BUCKET_NAME not set, skipping error storage")
        return
    
    try:
        s3_client = boto3.client('s3')
        
        # Create S3 key with model context
        safe_service = service_name.replace(' ', '_').lower()
        safe_model = model_id.replace(':', '_').replace('.', '_')
        safe_timestamp = timestamp.replace(':', '-').replace('.', '-')
        
        s3_error_key = f"eol_errors/{safe_service}/{safe_model}_{safe_timestamp}.json"
        
        s3_client.put_object(
            Bucket=s3_bucket_name,
            Key=s3_error_key,
            Body=json.dumps(error_context, indent=2),
            ContentType='application/json'
        )
        
        logger.info(f"Stored error details in S3: s3://{s3_bucket_name}/{s3_error_key}")
        
    except ClientError as e:
        logger.warning(f"S3 error storage failed: {str(e)}")
        raise


def create_dlq_message(
    error: Exception,
    model_id: str,
    service_name: str,
    original_event: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Create a DLQ message with model context.
    
    Args:
        error: The exception that occurred
        model_id: Bedrock model identifier
        service_name: AWS service name
        original_event: Optional original Lambda event
        
    Returns:
        Dictionary formatted for DLQ with model context
    """
    dlq_message = {
        'model_id': model_id,
        'service_name': service_name,
        'error_type': type(error).__name__,
        'error_message': str(error),
        'timestamp': datetime.now().isoformat()
    }
    
    if original_event:
        dlq_message['original_event'] = original_event
    
    logger.info(f"Created DLQ message with model context: {json.dumps(dlq_message)}")
    
    return dlq_message


def log_error_with_context(
    message: str,
    model_id: Optional[str] = None,
    service_name: Optional[str] = None,
    **kwargs
) -> None:
    """
    Log error message with model and service context.
    
    Args:
        message: Error message to log
        model_id: Optional Bedrock model identifier
        service_name: Optional AWS service name
        **kwargs: Additional key-value pairs to include in log
    """
    context_parts = []
    
    if model_id:
        context_parts.append(f"Model: {model_id}")
    if service_name:
        context_parts.append(f"Service: {service_name}")
    
    for key, value in kwargs.items():
        context_parts.append(f"{key}: {value}")
    
    context_str = ", ".join(context_parts) if context_parts else "No context"
    
    logger.error(f"{message} | Context: {context_str}")
