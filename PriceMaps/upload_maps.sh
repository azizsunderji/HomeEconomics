#!/bin/bash

# This script uploads the maps to the server using the same method as the GitHub Actions

echo "Uploading PriceMaps to server..."

# You'll need to set these environment variables or replace with actual values
HOST="${SFTP_HOST}"
USER="${SFTP_USER}"
KEY_PATH="${SFTP_KEY_PATH}"  # Path to your SSH key
REMOTE_BASE="${REMOTE_BASE:-/home2/yxwrmjmy/public_html/wp-content/uploads/reports}"

if [ -z "$HOST" ] || [ -z "$USER" ] || [ -z "$KEY_PATH" ]; then
    echo "Error: Please set SFTP_HOST, SFTP_USER, and SFTP_KEY_PATH environment variables"
    exit 1
fi

# Upload using lftp
lftp -e "
  set sftp:auto-confirm yes;
  set sftp:connect-program 'ssh -i $KEY_PATH';
  set net:max-retries 3;
  set net:timeout 60;
  open -u $USER,dummy sftp://$HOST;
  mkdir -p ${REMOTE_BASE}/live/PriceMaps;
  put output/us_price_levels_with_search.html -o ${REMOTE_BASE}/live/PriceMaps/us_price_levels_with_search.html;
  put output/us_yoy_price_map_with_search.html -o ${REMOTE_BASE}/live/PriceMaps/us_yoy_price_map_with_search.html;
  bye
" || echo "Upload finished with warnings"

echo "✅ Maps should now be available at:"
echo "  - https://home-economics.us/wp-content/uploads/reports/live/PriceMaps/us_price_levels_with_search.html"
echo "  - https://home-economics.us/wp-content/uploads/reports/live/PriceMaps/us_yoy_price_map_with_search.html"