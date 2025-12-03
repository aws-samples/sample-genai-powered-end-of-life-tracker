"""
Lambda function to recalculate accuracy scores for all records after all models have processed.

This ensures that consensus scores are accurate by comparing against all peer models,
not just the models that ran before each record was created.
"""

import json
import boto3
import logging
import os
from decimal import Decimal
from collections import defaultdict

# Import accuracy scoring module
from AccuracyScorer import calculate_accuracy_score

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Recalculate accuracy scores for all records in DynamoDB.
    
    This function:
    1. Scans all records from DynamoDB
    2. Groups them by service-cycle
    3. Recalculates accuracy score for each record with full peer data
    4. Updates records with new accuracy scores
    
    Args:
        event: Lambda event (unused, but can contain optional filters)
        context: Lambda context
        
    Returns:
        Response with count of updated records
    """
    try:
        # Initialize DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ.get("TABLE_NAME", "EOLTrackerDB")
        table = dynamodb.Table(table_name)
        
        logger.info(f"Starting accuracy score recalculation for table: {table_name}")
        
        # Step 1: Scan all records from DynamoDB
        logger.info("Scanning all records from DynamoDB...")
        all_records = []
        
        response = table.scan()
        all_records.extend(response.get('Items', []))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            all_records.extend(response.get('Items', []))
        
        logger.info(f"Found {len(all_records)} total records")
        
        # Step 2: Group records by service-cycle for efficient peer lookup
        logger.info("Grouping records by service-cycle...")
        records_by_service_cycle = defaultdict(list)
        
        for record in all_records:
            service = record.get('service')
            cycle = record.get('cycle')
            
            if service and cycle:
                key = (service, cycle)
                records_by_service_cycle[key].append(record)
        
        logger.info(f"Found {len(records_by_service_cycle)} unique service-cycle combinations")
        
        # Step 3: Recalculate accuracy scores
        logger.info("Recalculating accuracy scores...")
        updated_count = 0
        skipped_count = 0
        
        for (service, cycle), peer_records in records_by_service_cycle.items():
            logger.debug(f"Processing {service} - {cycle} with {len(peer_records)} peer records")
            
            for record in peer_records:
                model_id = record.get('model_name')
                
                if not model_id:
                    logger.warning(f"Skipping record without model_name: {service} - {cycle}")
                    skipped_count += 1
                    continue
                
                # Calculate new accuracy score with all peer records
                old_score = record.get('accuracy_score')
                
                new_score = calculate_accuracy_score(
                    record=record,
                    peer_records=peer_records,
                    model_id=model_id
                )
                
                # Convert to Decimal for DynamoDB
                new_score_decimal = Decimal(str(new_score))
                
                # Only update if score changed
                if old_score != new_score_decimal:
                    # Update the record in DynamoDB
                    cycle_model = record.get('cycle_model')
                    
                    table.update_item(
                        Key={
                            'service': service,
                            'cycle_model': cycle_model
                        },
                        UpdateExpression='SET accuracy_score = :score',
                        ExpressionAttributeValues={
                            ':score': new_score_decimal
                        }
                    )
                    
                    updated_count += 1
                    
                    # Format old score safely
                    old_score_str = f"{float(old_score):.4f}" if old_score else "N/A"
                    
                    logger.debug(
                        f"Updated {service} - {cycle} - {model_id}: "
                        f"{old_score_str} -> {new_score:.4f}"
                    )
        
        logger.info(
            f"Recalculation complete: {updated_count} records updated, "
            f"{skipped_count} records skipped, "
            f"{len(all_records) - updated_count - skipped_count} records unchanged"
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Accuracy score recalculation completed successfully",
                "total_records": len(all_records),
                "updated_records": updated_count,
                "skipped_records": skipped_count,
                "unchanged_records": len(all_records) - updated_count - skipped_count
            })
        }
        
    except Exception as e:
        logger.error(f"Error recalculating accuracy scores: {str(e)}")
        
        # Raise exception to trigger Step Function error handling
        raise Exception(f"Error recalculating accuracy scores: {str(e)}")


if __name__ == '__main__':
    # For local testing
    test_event = {}
    test_context = None
    
    result = lambda_handler(test_event, test_context)
    print(json.dumps(result, indent=2))
