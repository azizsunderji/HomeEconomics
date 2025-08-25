#!/bin/bash
# Upload social media charts to S3

# Set the date (defaults to today Eastern time)
DATE=${1:-$(TZ='America/New_York' date +'%Y-%m-%d')}

# Local directory with charts
LOCAL_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/${DATE}"

# S3 bucket path
S3_BUCKET="s3://azizpublic/home-economics/social-charts/${DATE}"

# Check if local directory exists
if [ ! -d "$LOCAL_DIR" ]; then
    echo "❌ Directory not found: $LOCAL_DIR"
    echo "Have you generated charts for ${DATE}?"
    exit 1
fi

# Count charts
CHART_COUNT=$(find "$LOCAL_DIR" -name "*.png" | wc -l)
echo "📊 Found ${CHART_COUNT} charts to upload"

# Upload to S3
echo "📤 Uploading to ${S3_BUCKET}..."
aws s3 sync "$LOCAL_DIR" "$S3_BUCKET" \
    --exclude ".DS_Store" \
    --exclude "*.json" \
    --content-type "image/png" \
    --cache-control "public, max-age=3600" \
    --acl public-read

# Upload index.json separately with proper content type
if [ -f "$LOCAL_DIR/index.json" ]; then
    aws s3 cp "$LOCAL_DIR/index.json" "$S3_BUCKET/index.json" \
        --content-type "application/json" \
        --cache-control "public, max-age=3600" \
        --acl public-read
fi

echo "✅ Upload complete!"
echo "🔗 Charts available at: https://azizpublic.s3.amazonaws.com/home-economics/social-charts/${DATE}/"
echo ""
echo "Example URLs:"
echo "  https://azizpublic.s3.amazonaws.com/home-economics/social-charts/${DATE}/denver_co_metro_area/active_listings.png"
echo "  https://azizpublic.s3.amazonaws.com/home-economics/social-charts/${DATE}/seattle_wa_metro_area/median_sale_price.png"