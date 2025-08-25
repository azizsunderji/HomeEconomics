#!/bin/bash

# Manual upload script for social media charts
# Run this script to upload charts to your server

echo "📤 Starting social media charts upload..."
echo ""

# Configuration
LOCAL_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/2025-08-22"
DATE="2025-08-22"

# Check if local directory exists
if [ ! -d "$LOCAL_DIR" ]; then
    echo "❌ Local directory not found: $LOCAL_DIR"
    exit 1
fi

# Count files
TOTAL_FILES=$(find "$LOCAL_DIR" -name "*.png" | wc -l | tr -d ' ')
TOTAL_METROS=$(find "$LOCAL_DIR" -type d -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')

echo "📊 Found $TOTAL_FILES PNG charts across $TOTAL_METROS metros"
echo ""

# Check for GitHub CLI
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) not found. Please install it first:"
    echo "   brew install gh"
    exit 1
fi

# Check GitHub authentication
if ! gh auth status &> /dev/null; then
    echo "❌ Not authenticated with GitHub. Please run:"
    echo "   gh auth login"
    exit 1
fi

echo "🚀 Triggering GitHub Actions workflow to upload charts..."
echo ""

# Trigger the GitHub Actions workflow
cd "/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics"

gh workflow run upload-social-charts.yml \
    -f date="$DATE" \
    --repo "azizsunderji/HomeEconomics"

if [ $? -eq 0 ]; then
    echo "✅ Workflow triggered successfully!"
    echo ""
    echo "📍 Monitor progress at:"
    echo "   https://github.com/azizsunderji/HomeEconomics/actions"
    echo ""
    echo "🌐 Once complete, charts will be available at:"
    echo "   https://home-economics.us/charts/social/$DATE/"
    echo ""
    echo "💡 Example URLs:"
    echo "   https://home-economics.us/charts/social/$DATE/denver_co/denver_co_median_sale_price_social.png"
    echo "   https://home-economics.us/charts/social/$DATE/austin_tx/austin_tx_active_listings_social.png"
else
    echo "❌ Failed to trigger workflow"
    echo "Please check your GitHub configuration and try again"
    exit 1
fi