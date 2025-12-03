import json
import boto3
import logging
import os
from boto3.dynamodb.conditions import Key
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Model tier definitions for tie-breaking
PREMIUM_MODELS = [
    'anthropic.claude-sonnet-4-20250514-v1:0',
    'us.anthropic.claude-3-7-sonnet-20250219-v1:0',
    'anthropic.claude-3-5-sonnet-20241022-v2:0'
]

MID_TIER_MODELS = [
    'amazon.nova-premier-v1:0'
]


def decimal_to_float(obj):
    """Convert Decimal objects to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_model_tier_priority(model_id: str) -> int:
    """Return priority for tie-breaking (higher is better)."""
    if model_id in PREMIUM_MODELS:
        return 3
    elif model_id in MID_TIER_MODELS:
        return 2
    else:
        return 1


def select_best_record(items):
    """Select the single best record based on accuracy score, model tier, and timestamp."""
    if not items:
        return []
    
    if len(items) == 1:
        return items
    
    # Sort by: accuracy_score (desc), model_tier (desc), lastUpdated (desc)
    best_record = max(items, key=lambda x: (
        float(x.get('accuracy_score', 0.0)) if isinstance(x.get('accuracy_score'), Decimal) else x.get('accuracy_score', 0.0),
        get_model_tier_priority(x.get('model_name', '')),
        x.get('lastUpdated', '')
    ))
    
    return [best_record]


def lambda_handler(event, context):
    """
    Lambda function to query AWS service EOL data from DynamoDB.
    Returns only the best record per service-cycle based on accuracy score.
    """
    try:
        # Initialize DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ.get("TABLE_NAME", "EOLTrackerDB")
        table = dynamodb.Table(table_name)

        # Get query parameters
        query_params = event.get("queryStringParameters", {}) or {}
        service = query_params.get("service")
        cycle = query_params.get("cycle")
        model = query_params.get("model")

        # Get all items if no specific query
        if not service:
            response = table.scan()
            items = response.get("Items", [])

            # Handle pagination for large datasets
            while "LastEvaluatedKey" in response:
                response = table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))
            
            # Group by service-cycle and select best from each group
            grouped = {}
            for item in items:
                key = (item.get('service'), item.get('cycle'))
                if key not in grouped:
                    grouped[key] = []
                grouped[key].append(item)
            
            items = []
            for group_items in grouped.values():
                items.extend(select_best_record(group_items))

        # Query by service only
        elif service and not cycle:
            response = table.query(
                KeyConditionExpression=Key("service").eq(service)
            )
            items = response.get("Items", [])
            
            # Group by cycle and select best from each group
            grouped = {}
            for item in items:
                cycle_key = item.get('cycle')
                if cycle_key not in grouped:
                    grouped[cycle_key] = []
                grouped[cycle_key].append(item)
            
            items = []
            for group_items in grouped.values():
                items.extend(select_best_record(group_items))

        # Query by service and cycle (with optional model filtering)
        elif service and cycle:
            if model:
                # Query specific service, cycle, and model using composite key
                cycle_model = f"{cycle}#{model}"
                response = table.get_item(
                    Key={"service": service, "cycle_model": cycle_model}
                )
                items = [response.get("Item")] if "Item" in response else []
            else:
                # Query by service and cycle prefix (all models for this cycle)
                response = table.query(
                    KeyConditionExpression=Key("service").eq(service) & 
                                           Key("cycle_model").begins_with(f"{cycle}#")
                )
                items = response.get("Items", [])
                
                # Select only the best record
                items = select_best_record(items)

        # Filter by model if specified (for scan/query results without specific cycle)
        if model and items:
            items = [item for item in items if item.get("model_name") == model]

        # Return results with Decimal conversion
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
            "body": json.dumps(items, default=decimal_to_float),
        }

    except Exception as e:
        logger.error(f"Error querying data: {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"message": f"Error querying data: {str(e)}"}),
        }
