#!/bin/bash

# Deploy CES viewer to WordPress server
# This script prepares files for manual upload to WordPress

echo "CES Viewer WordPress Deployment"
echo "================================"
echo ""

# Create temporary deployment directory
TEMP_DIR=$(mktemp -d)
echo "Preparing files in: $TEMP_DIR"

# Copy HTML viewer
cp src/ces-viewer.html "$TEMP_DIR/index.html"

# Copy data files to same directory as HTML
cp data/ces_historical_data.json "$TEMP_DIR/" 2>/dev/null || echo "Warning: No historical data file"
cp data/ces_data.json "$TEMP_DIR/" 2>/dev/null || echo "Warning: No regular data file"
cp src/recession_periods.json "$TEMP_DIR/" 2>/dev/null || cp data/recession_periods.json "$TEMP_DIR/" 2>/dev/null || echo "Warning: No recession periods file"

# Create deployment timestamp
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "Deployment timestamp: $TIMESTAMP"

echo ""
echo "Files prepared for deployment:"
ls -lh "$TEMP_DIR/"

echo ""
echo "Upload Instructions:"
echo "===================="
echo "1. Upload ALL files from $TEMP_DIR/ to:"
echo "   https://home-economics.us/wp-content/uploads/reports/live/ces/"
echo ""
echo "2. The files should be accessible at:"
echo "   - https://home-economics.us/wp-content/uploads/reports/live/ces/index.html (viewer)"
echo "   - https://home-economics.us/wp-content/uploads/reports/live/ces/ces_historical_data.json"
echo "   - https://home-economics.us/wp-content/uploads/reports/live/ces/recession_periods.json"
echo ""
echo "Files are ready in: $TEMP_DIR/"