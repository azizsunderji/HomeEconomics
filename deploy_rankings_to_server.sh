#!/bin/bash

# Deploy metro rankings to WordPress server
# This uploads the rankings to the live website

echo "Metro Rankings Server Deployment"
echo "================================="
echo ""

# Check if rankings directory exists
if [ ! -d "rankings" ]; then
    echo "Error: rankings directory not found!"
    exit 1
fi

# Count files
FILE_COUNT=$(ls -1 rankings/*.html 2>/dev/null | wc -l)
echo "Found $FILE_COUNT HTML files to deploy"

# Get current year and month for WordPress upload path
YEAR=$(date +%Y)
MONTH=$(date +%m)

# Base URL for the website
BASE_URL="https://home-economics.us/wp-content/uploads"

echo ""
echo "Deployment Details:"
echo "==================="
echo "Target: $BASE_URL/$YEAR/$MONTH/rankings/"
echo "Files: $FILE_COUNT HTML files"
echo ""

# Create a temporary directory for deployment
TEMP_DIR=$(mktemp -d)
echo "Preparing files in: $TEMP_DIR"

# Copy rankings to temp directory
cp -r rankings "$TEMP_DIR/"

# Create deployment timestamp
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "Deployment timestamp: $TIMESTAMP"

# Create a manifest file
cat > "$TEMP_DIR/rankings/manifest.json" << EOF
{
    "deployed": "$TIMESTAMP",
    "files": $FILE_COUNT,
    "data_through": "August 24, 2025",
    "default_view": "Top 10% (Large Markets)",
    "metrics": [
        "median_sale_price",
        "active_listings",
        "weeks_supply",
        "homes_sold",
        "new_listings",
        "median_days_on_market",
        "pending_sales",
        "off_market_in_2_weeks",
        "median_days_to_close",
        "sale_to_list_ratio",
        "pct_listings_w__price_drops",
        "age_of_inventory"
    ]
}
EOF

echo ""
echo "Files prepared for deployment"
echo ""
echo "Upload Instructions:"
echo "===================="
echo "1. Open WordPress Admin: https://home-economics.us/wp-admin"
echo "2. Go to Media Library"
echo "3. Create folder structure: $YEAR/$MONTH/rankings/"
echo "4. Upload all files from: $TEMP_DIR/rankings/"
echo ""
echo "Or use FTP/SFTP to upload directly to:"
echo "  /wp-content/uploads/$YEAR/$MONTH/rankings/"
echo ""
echo "Files ready in: $TEMP_DIR/rankings/"
echo ""
echo "Public URLs will be:"
echo "  $BASE_URL/$YEAR/$MONTH/rankings/median_sale_price.html"
echo "  $BASE_URL/$YEAR/$MONTH/rankings/active_listings.html"
echo "  etc..."
echo ""
echo "To link from WordPress posts, use:"
echo "  <iframe src=\"$BASE_URL/$YEAR/$MONTH/rankings/median_sale_price.html\""
echo "          width=\"100%\" height=\"800\" frameborder=\"0\"></iframe>"
echo ""
echo "Local files prepared at: $TEMP_DIR/rankings/"
echo "Remember to upload these files to the WordPress Media Library!"