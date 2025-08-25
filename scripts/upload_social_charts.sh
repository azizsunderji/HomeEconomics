#!/bin/bash

# Upload social media charts to server via SFTP
# Uses the same credentials as the GitHub Actions workflow

echo "📤 Uploading social media charts to server..."

# Configuration
LOCAL_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/2025-08-22"
REMOTE_BASE="/var/www/html/charts/social"  # Adjust this path as needed
DATE="2025-08-22"

# Check if local directory exists
if [ ! -d "$LOCAL_DIR" ]; then
    echo "❌ Local directory not found: $LOCAL_DIR"
    exit 1
fi

# Count files
TOTAL_FILES=$(find "$LOCAL_DIR" -name "*.png" | wc -l | tr -d ' ')
echo "📊 Found $TOTAL_FILES charts to upload"

# You'll need to set these environment variables or replace with actual values
HOST="${SFTP_HOST:-your-server.com}"
USER="${SFTP_USER:-your-username}"
KEY_PATH="${SFTP_KEY:-~/.ssh/id_rsa}"

# Check if SSH key exists
if [ ! -f "$KEY_PATH" ]; then
    echo "❌ SSH key not found at: $KEY_PATH"
    echo "Please set SFTP_KEY environment variable or update the script"
    exit 1
fi

echo "🔗 Connecting to $HOST as $USER..."

# Use rsync for efficient transfer (only uploads new/changed files)
rsync -avz --progress \
    -e "ssh -i $KEY_PATH -o StrictHostKeyChecking=no" \
    "$LOCAL_DIR/" \
    "$USER@$HOST:$REMOTE_BASE/$DATE/"

if [ $? -eq 0 ]; then
    echo "✅ Upload complete!"
    echo "📍 Charts available at: https://$HOST/charts/social/$DATE/"
    
    # Generate a sample URL
    SAMPLE_METRO=$(ls "$LOCAL_DIR" | head -1)
    if [ -n "$SAMPLE_METRO" ]; then
        SAMPLE_CHART=$(ls "$LOCAL_DIR/$SAMPLE_METRO" | grep ".png" | head -1)
        echo "🔗 Sample URL: https://$HOST/charts/social/$DATE/$SAMPLE_METRO/$SAMPLE_CHART"
    fi
else
    echo "❌ Upload failed"
    exit 1
fi