import json
import boto3
import logging
import os
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Lambda function to query AWS service EOL data from DynamoDB
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

        # Query by service only
        elif service and not cycle:
            response = table.query(
                KeyConditionExpression=Key("service").eq(service)
            )
            items = response.get("Items", [])

        # Query by service and cycle (full primary key)
        else:
            response = table.get_item(Key={"service": service, "cycle": cycle})
            items = [response.get("Item")] if "Item" in response else []

        # Return results
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
            "body": json.dumps(items),
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
