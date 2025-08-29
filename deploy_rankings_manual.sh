#!/bin/bash

# Manual deployment script for metro rankings
# This creates a simple local deployment for testing

echo "Metro Rankings Deployment"
echo "========================="

# Check if rankings directory exists
if [ ! -d "rankings" ]; then
    echo "Error: rankings directory not found!"
    exit 1
fi

# Count files
FILE_COUNT=$(ls -1 rankings/*.html 2>/dev/null | wc -l)
echo "Found $FILE_COUNT HTML files in rankings/"

# Create deployment directory
DEPLOY_DIR="rankings-deploy-$(date +%Y%m%d-%H%M%S)"
echo "Creating deployment directory: $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"

# Copy files
cp -r rankings/* "$DEPLOY_DIR/"
echo "Files copied to $DEPLOY_DIR"

# Create a simple index page if not exists
if [ ! -f "$DEPLOY_DIR/index.html" ]; then
    echo "Creating index redirect..."
    cat > "$DEPLOY_DIR/index.html" << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0; url=median_sale_price.html">
</head>
<body>
    Redirecting to Metro Rankings...
</body>
</html>
EOF
fi

echo ""
echo "Deployment complete!"
echo "Files are ready in: $DEPLOY_DIR"
echo ""
echo "Available pages:"
ls -1 "$DEPLOY_DIR"/*.html | head -5
echo "..."
echo ""
echo "To view locally, open:"
echo "  file://$(pwd)/$DEPLOY_DIR/median_sale_price.html"
echo ""
echo "Note: For production deployment, use GitHub Actions workflow"
echo "or upload to your web server at:"
echo "  https://www.home-economics.us/live/rankings/"