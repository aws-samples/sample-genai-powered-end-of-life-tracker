#!/bin/bash
set -e

# Configuration
LAYER_DIR="lambda_layer"
TARGET_DIR="$LAYER_DIR/python/lib/python3.12/site-packages"
REQUIREMENTS_FILE="requirements.txt"
OUTPUT_ZIP="eol_mcp_layer.zip"
S3_KEY="layers/eol_mcp_layer.zip"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --s3-bucket)
      S3_BUCKET="$2"
      shift 2
      ;;
    --help)
      echo "Usage: $0 [--s3-bucket BUCKET_NAME]"
      echo ""
      echo "Options:"
      echo "  --s3-bucket BUCKET_NAME    Upload the layer to this S3 bucket after building"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "=== EOL MCP Lambda Layer Build Script ==="
echo "Creating Lambda layer for EOLMcpAgent..."

# Check if requirements file exists
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo "ERROR: $REQUIREMENTS_FILE not found in current directory!"
    exit 1
fi

# Clean up previous builds
if [ -d "$LAYER_DIR" ]; then
    echo "Cleaning up previous build..."
    rm -rf "$LAYER_DIR"
    rm -f "$OUTPUT_ZIP"

fi

# Create directory structure for Lambda layer
echo "Creating layer directory structure..."
mkdir -p "$TARGET_DIR"

# Install dependencies into the layer directory
echo "Installing dependencies from $REQUIREMENTS_FILE..."
if command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
else
    PIP_CMD="pip"
fi

# Use pip to install packages to the target directory
"$PIP_CMD" install -r "$REQUIREMENTS_FILE" -t "$TARGET_DIR" --no-cache-dir

# Clean up unnecessary files to reduce package size
# Remove Python cache files
echo "  - Removing Python cache files..."
find "$LAYER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$LAYER_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$LAYER_DIR" -type f -name "*.pyo" -delete 2>/dev/null || true
find "$LAYER_DIR" -type f -name "*.pyd" -delete 2>/dev/null || true

# Create ZIP file
echo "Creating ZIP file: $OUTPUT_ZIP..."
cd "$LAYER_DIR"
zip -r "../$OUTPUT_ZIP" .
cd ..

# Check if the ZIP was created successfully
if [ -f "$OUTPUT_ZIP" ]; then
    ZIP_SIZE=$(du -h "$OUTPUT_ZIP" | cut -f1)
    echo "✅ Layer package created successfully: $OUTPUT_ZIP ($ZIP_SIZE)"
else
    echo "❌ Failed to create layer package"
    exit 1
fi

# Clean up
echo "Cleaning up..."
rm -rf "$LAYER_DIR"

echo "=== Build Complete ==="

# Upload to S3 if bucket is specified
if [ -n "$S3_BUCKET" ]; then
    echo "Uploading layer to s3://$S3_BUCKET/$S3_KEY..."
    
    # Check if AWS CLI is installed
    if ! command -v aws &> /dev/null; then
        echo "Error: AWS CLI is not installed. Cannot upload to S3."
        echo "Please install AWS CLI or upload the ZIP manually."
        exit 1
    fi
    
    # Upload to S3
    aws s3 cp "$OUTPUT_ZIP" "s3://$S3_BUCKET/$S3_KEY"
    
    if [ "$?" -eq 0 ]; then
        echo "✅ Layer successfully uploaded to s3://$S3_BUCKET/$S3_KEY"
    else
        echo "❌ Failed to upload layer to S3"
        exit 1
    fi
    
    echo "Layer is now ready to be used by CloudFormation template."
fi
