#!/bin/bash
source ~/.zprofile
cd "/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/listenqueue"

# Start HTTP server
python3 serve.py &
HTTP_PID=$!

# Wait for server to start
sleep 2

# Start Cloudflare tunnel and capture the URL
/opt/homebrew/bin/cloudflared tunnel --url http://localhost:8234 2>&1 | tee /tmp/listenqueue_tunnel.log &
CF_PID=$!

# Wait for tunnel URL
sleep 15
TUNNEL_URL=$(grep -o 'https://[^ ]*\.trycloudflare\.com' /tmp/listenqueue_tunnel.log | head -1)

if [ -n "$TUNNEL_URL" ]; then
    echo "Tunnel URL: $TUNNEL_URL"
    # Save tunnel URL for reference (Chrome extension etc.). Do NOT stamp it
    # into the podcast feed — the feed must always reference the permanent
    # home-economics.us URLs so old episodes survive Mac restarts. (The
    # cron run regenerates the feed with the default FEED_BASE_URL and
    # sync_to_bluehost.sh pushes audio + feed to home-economics.us.)
    echo "$TUNNEL_URL" > /tmp/listenqueue_tunnel_url.txt
fi

# Wait for either process to exit
wait $HTTP_PID $CF_PID
