import json
import boto3
import os

s3 = boto3.client('s3')

def lambda_handler(event, context):
    bucket = os.environ['S3_BUCKET_NAME']
    
    # Load config from local file
    with open('cfg/aws_services.json', 'r', encoding='utf-8') as f:
        services = json.load(f)
    
    return {
        'statusCode': 200,
        'services': services
    }
