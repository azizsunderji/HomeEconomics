#!/bin/bash

# Upload SAMPLE social media charts to server
# Just a few examples for testing/preview

echo "📤 Uploading SAMPLE social media charts..."
echo ""

# Configuration
SAMPLES_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_chart_examples"
DATE="2025-08-22"

# Check if samples directory exists
if [ ! -d "$SAMPLES_DIR" ]; then
    echo "❌ Samples directory not found: $SAMPLES_DIR"
    exit 1
fi

# Count files
TOTAL_FILES=$(find "$SAMPLES_DIR" -name "*.png" | wc -l | tr -d ' ')
echo "📊 Found $TOTAL_FILES sample charts to upload"
echo ""

# List the samples
echo "📁 Sample charts:"
ls -la "$SAMPLES_DIR"/*.png
echo ""

echo "These sample charts are already saved in:"
echo "📂 $SAMPLES_DIR"
echo ""
echo "You can access them locally for posting to social media."
echo ""

# Optional: Copy to a specific location if needed
SOCIAL_SAMPLES="/Users/azizsunderji/Dropbox/Home Economics/social_media_samples"
mkdir -p "$SOCIAL_SAMPLES"
cp "$SAMPLES_DIR"/*.png "$SOCIAL_SAMPLES/"

echo "✅ Samples also copied to: $SOCIAL_SAMPLES"
echo ""
echo "🎯 Ready for social media posting!"