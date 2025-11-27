# EOL Tracker

## Introduction

The EOL Tracker is an AWS-based solution that automatically extracts and tracks End-of-Life (EOL) information for AWS services using Amazon Bedrock agents with Model Context Protocol (MCP) integration. The system crawls AWS documentation, extracts EOL data, stores it in DynamoDB, and provides a REST API for querying the information.

The solution leverages:
- **Amazon Bedrock** with Anthropic Claude for intelligent document analysis
- **Model Context Protocol (MCP)** for AWS documentation access
- **AWS Step Functions** for orchestrating the data extraction workflow
- **DynamoDB** for storing EOL data
- **API Gateway + CloudFront** for secure API access
- **EventBridge Scheduler** for automated monthly updates

![Architecture](img/Architecture.png)

## ⚠️ Important Disclaimer

**AI-Generated Results:** This system uses AI (Amazon Bedrock with Anthropic Claude) to extract and analyze End-of-Life information from AWS documentation. Please note that:

- **Results are non-deterministic by nature** - The same input may produce slightly different outputs across runs
- **Manual verification is required** - All extracted EOL data should be validated against official AWS documentation before use in production environments
- **Continuous improvement** - We are actively developing enhancements to make the results more deterministic and reliable

Always cross-reference the generated EOL information with the official AWS service documentation before making critical business or technical decisions.

## Architecture Components

- **Lambda Functions**:
  - `EOLMcpAgentFunction`: Extracts EOL data from AWS documentation using MCP
  - `EOLDataImportFunction`: Imports extracted data into DynamoDB
  - `EOLDataQueryFunction`: Provides API endpoint for querying EOL data
  - `CustomAuthorizerFunction`: Handles API authentication
  - `LoadConfigFunction`: Loads service configuration for processing
- **VPC Configuration**:
  - Private subnets for Lambda functions
  - NAT Gateway for internet access (AWS documentation)
  - VPC Endpoints for AWS services (S3, DynamoDB, Bedrock, SQS, Step Functions)
  - Security groups with restricted egress rules
  - VPC Flow Logs for network monitoring
- **Step Functions State Machine**: Orchestrates sequential execution of data extraction and import
- **DynamoDB Table**: Stores EOL information with service and cycle as keys
- **API Gateway**: REST API with custom authorization and X-Ray tracing
- **CloudFront Distribution**: CDN with WAF protection (includes Log4Shell protection)
- **EventBridge Scheduler**: Triggers monthly data updates
- **Dead Letter Queues**: SQS queues for failed Lambda invocations
- **KMS Encryption**: All logs and data encrypted at rest with key rotation enabled

![Step Function State Machine](img/Step%20Function%20State%20Machine.png)

## Prerequisites

### Required Software

1. **Python 3.12+**
   - Download from: https://www.python.org/downloads/

2. **AWS CLI**
   - Installation guide: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

3. **AWS SAM CLI**
   - Installation guide: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html

### AWS Requirements

- AWS Account with appropriate permissions
- Bedrock model access enabled for:
  - Anthropic Claude models
  - Amazon Titan Text Embeddings v2
- S3 bucket for storing Lambda layers and EOL data
- Region: **us-east-1** or **us-west-2** (required for Bedrock model availability)

### Enable Bedrock Models

Navigate to the Bedrock console → Model access and request access to:
- All Anthropic Claude models
- Amazon Titan Text Embeddings v2

## Deployment Instructions

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd EOLTracker
```

### Step 2: Configure AWS Credentials

Configure your AWS credentials before proceeding:

```bash
aws configure
```

Provide your:
- AWS Access Key ID
- AWS Secret Access Key
- Default region (us-east-1 or us-west-2)
- Default output format (json)

### Step 3: Build Lambda Layer

The Lambda layer contains all dependencies for the MCP agent including the `uv` package manager and required Python libraries.
Before you launch the below commands, you need to create an S3 bucket manually to store the layer.

```bash
cd cfn-templates/build-layer
chmod +x ./build-layer.sh
./build-layer.sh --s3-bucket <your-s3-bucket-name>
```

This script will:
- Install `uv` package manager
- Package all dependencies
- Upload the layer to your S3 bucket at `s3://<bucket>/layers/eol_mcp_layer.zip`

### Step 4: Deploy CloudFormation Stack

Ensure your AWS credentials are configured, then navigate to the CloudFormation templates directory and deploy using SAM:

```bash
cd ../
sam deploy --guided \
  --stack-name EOLTracker \
  --region us-east-1 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    S3BucketName=<your-s3-bucket-name> \
    Region=us-east-1 \
  --template ./EOLTrackerTemplate.yml
```

![SAM CLI](img/SAM%20CLI.png)

During the guided deployment, you'll be prompted for:
- Stack name (default: EOLTracker)
- AWS Region (us-east-1 or us-west-2)
- S3 bucket name for storing data
- Confirmation to create IAM roles

![CloudFormation Console](img/CloudFormation%20Console.png)

### Step 5: Get API Endpoint

After successful deployment, retrieve the API URL from the CloudFormation outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name EOLTracker \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text
```

## Usage

### Install awscurl

Install awscurl to query the API with AWS Signature Version 4 authentication:

```bash
pip install awscurl
```

### Query EOL Data

Use awscurl to query EOL information for AWS services:

```bash
awscurl --service execute-api \
  "https://<api-gateway-id>.execute-api.us-east-1.amazonaws.com/dev/eol?service=Amazon%20RDS"
```

![awscurl CLI](img/awscurl%20CLI.png)

### View Stored Data

Access DynamoDB to view the extracted EOL data:

![EOL DynamoDB data](img/EOL%20DynamoDB%20data.png)

### Manual Trigger

To manually trigger the EOL data extraction workflow:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:<region>:<account-id>:stateMachine:EOLDataProcessingStateMachine \
  --input '{"manualExecution": true}'
```

### Scheduled Execution

By default, the system runs automatically on the 1st of every month at 3 AM UTC. You can modify the schedule by updating the `ScheduleExpression` parameter in the CloudFormation template.

## Configuration

### AWS Services Configuration

Edit `cfn-templates/src/cfg/aws_services.json` to add or modify AWS services to track:

```json
[
  {
    "service_name": "Amazon RDS",
    "service_url": "https://docs.aws.amazon.com/rds/..."
  }
]
```

### Adjust Schedule

Modify the `ScheduleExpression` parameter in the CloudFormation template:

```yaml
Parameters:
  ScheduleExpression:
    Type: String
    Default: "cron(0 3 1 * ? *)"  # 1st of month at 3 AM UTC
```

## Cost Estimation

Estimated monthly costs for running the EOL Tracker (based on us-east-1 pricing):

### Monthly Scheduled Execution (Default: Once per month)

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| **Amazon Bedrock** | Claude 3.5 Sonnet (~50K input tokens, ~5K output tokens per run) | ~$0.30/month |
| **Lambda - EOLMcpAgentFunction** | 1 execution × 15 min × 2048 MB | ~$0.05/month |
| **Lambda - EOLDataImportFunction** | 1 execution × 5 min × 128 MB | ~$0.01/month |
| **Lambda - EOLDataQueryFunction** | 100 API queries × 1 sec × 128 MB | ~$0.01/month |
| **Lambda - CustomAuthorizerFunction** | 100 authorizations × 1 sec × 128 MB | ~$0.01/month |
| **Lambda - LoadConfigFunction** | 1 execution × 1 sec × 128 MB | <$0.01/month |
| **VPC NAT Gateway** | 730 hours + minimal data processing | ~$33.00/month |
| **VPC Endpoints (Interface)** | 3 endpoints × 730 hours (Bedrock, SQS, Step Functions) | ~$21.60/month |
| **VPC Endpoints (Gateway)** | S3 and DynamoDB | Free |
| **Elastic IP** | 1 EIP for NAT Gateway | Free (in use) |
| **SQS Dead Letter Queues** | Minimal usage for failed invocations | <$0.01/month |
| **Step Functions** | 1 execution × 2 state transitions | <$0.01/month |
| **DynamoDB** | On-demand, ~100 items, minimal reads/writes | ~$0.25/month |
| **API Gateway** | 100 requests | <$0.01/month |
| **CloudFront** | 100 requests, minimal data transfer | <$0.01/month |
| **S3** | Storage for Lambda layer (~50 MB) and EOL data (~1 MB) | <$0.01/month |
| **CloudWatch Logs** | ~500 MB logs with 7-14 day retention | ~$0.25/month |
| **EventBridge Scheduler** | 1 scheduled execution | <$0.01/month |
| **KMS** | Key storage and API calls | ~$1.00/month |
| **WAF** | Web ACL with basic rules | ~$5.00/month |

**Total Estimated Cost: ~$61-62 USD/month**

### Cost Optimization Tips

#### ⚠️ Recommended: Delete Stack After Each Use

**IMPORTANT:** To minimize costs, it is **strongly recommended** to delete the CloudFormation stack after each usage, especially if you only need to run the EOL extraction occasionally.

**Why?** The majority of costs (~90%) come from resources that charge hourly:
- **NAT Gateway**: ~$33/month (~$0.045/hour) - charges even when idle
- **Interface VPC Endpoints**: ~$21.60/month (~$0.01/hour per endpoint) - 3 endpoints charging continuously

These resources accumulate costs 24/7 regardless of whether the system is actively processing data.

**Workflow for Cost Savings:**

1. **Deploy stack when needed:**
   ```bash
   sam deploy --guided --stack-name EOLTracker
   ```

2. **Run the EOL extraction:**
   ```bash
   aws stepfunctions start-execution \
     --state-machine-arn arn:aws:states:<region>:<account-id>:stateMachine:EOLDataProcessingStateMachine \
     --input '{"manualExecution": true}'
   ```

3. **Export DynamoDB data (optional):**
   ```bash
   aws dynamodb scan --table-name EOLTrackerDB > eol-data-backup.json
   ```

4. **Delete stack immediately after use:**
   ```bash
   aws cloudformation delete-stack --stack-name EOLTracker
   ```

**Cost Comparison:**
- **Keeping stack running 24/7**: ~$61-62/month
- **Deploy → Run → Delete (once per month)**: ~$2-3/execution
- **Annual savings**: ~$700/year

#### Other Optimization Options

- **Remove VPC configuration** if security isolation is not required (saves ~$55/month from NAT Gateway and Interface Endpoints)
- **Remove WAF** if not required for your use case (saves ~$5/month)
- **Reduce Lambda memory** for EOLMcpAgentFunction if processing fewer services
- **Adjust log retention** to 3-5 days instead of 7-14 days
- **Use S3 Intelligent-Tiering** for long-term data storage
- **Disable CloudFront** if you don't need CDN capabilities (use API Gateway directly)
- **Consider removing Interface VPC Endpoints** and use NAT Gateway for all traffic (saves ~$22/month but increases data transfer costs)
- **Disable EventBridge Scheduler** if you prefer manual execution only

**Note:** Costs may vary based on:
- Number of AWS services tracked
- API query volume
- Bedrock model token usage
- Data transfer and storage growth

For detailed cost estimates, use the [AWS Pricing Calculator](https://calculator.aws).

## Uninstall

To remove all resources:

```bash
aws cloudformation delete-stack --stack-name EOLTracker
```

Note: Manually delete the S3 bucket contents and the Lambda layer if needed.

## Troubleshooting

### Lambda Timeout Issues
- Increase the `Timeout` parameter for `EOLMcpAgentFunction` (default: 900 seconds)
- Check CloudWatch Logs for detailed error messages

### MCP Connection Errors
- Verify the Lambda layer was built and uploaded correctly
- Check that `/tmp` directory has sufficient space
- Review environment variables in the Lambda function

### API Authorization Failures
- Verify the custom authorizer is configured correctly
- Check CloudWatch Logs for `CustomAuthorizerFunction`

## Monitoring

View logs in CloudWatch Log Groups:
- `/aws/lambda/EOLMcpAgentFunction`
- `/aws/lambda/EOLDataImportFunction`
- `/aws/lambda/EOLDataQueryFunction`
- `/aws/lambda/LoadConfigFunction`
- `/aws/lambda/CustomAuthorizerFunction`
- `/aws/stepfunctions/EOLDataProcessingStateMachine`
- `API-Gateway-Access-Logs-*`

### X-Ray Tracing

API Gateway has X-Ray tracing enabled for request analysis and debugging. View traces in the AWS X-Ray console.

## Security

- **Network Isolation**: Lambda functions deployed in private VPC subnets
- **VPC Endpoints**: Private connectivity to AWS services (S3, DynamoDB, Bedrock, SQS, Step Functions)
- **Security Groups**: Restricted egress rules (HTTPS only to VPC CIDR and internet)
- **NAT Gateway**: Controlled internet access for AWS documentation retrieval
- **VPC Flow Logs**: Network traffic monitoring and analysis
- **Encryption at Rest**: All data encrypted using AWS KMS with key rotation enabled
- **API Security**: Custom authorizer with AWS Signature Version 4 authentication
- **CloudFront + WAF**: Protection with AWS Managed Rules (includes Log4Shell/CVE-2021-44228 protection)
- **IAM Best Practices**: Least privilege managed policies (no inline policies)
- **CloudWatch Logs**: Encrypted with KMS, 7-14 day retention
- **Dead Letter Queues**: SQS queues with encryption for failed Lambda invocations
- **S3 Security**: Bucket encryption, versioning, and access logging enabled
- **X-Ray Tracing**: Request analysis and debugging for API Gateway

## License

This project is licensed under the MIT-0 License.

## Credits

This project utilizes:
- [AWS Documentation MCP Server](https://github.com/awslabs/aws-documentation-mcp-server)
- Amazon Bedrock with Anthropic Claude
- AWS Serverless Application Model (SAM)
