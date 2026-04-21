#!/usr/bin/env python3
"""Gmail OAuth flow — uses only stdlib (no httpx dependency)."""

import json
import os
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import urlopen, Request

print("=== Gmail OAuth Re-authorization ===")
print()

# Get credentials
CLIENT_ID = ""
CLIENT_SECRET = ""
for var in ("GMAIL_TOKEN", "GMAIL_TOKENS"):
    raw = os.environ.get(var, "")
    if not raw:
        continue
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0]
        CLIENT_ID = data.get("client_id", "")
        CLIENT_SECRET = data.get("client_secret", "")
        if CLIENT_ID and CLIENT_SECRET:
            break
    except Exception:
        continue

if not CLIENT_ID:
    CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: No credentials found. Set GMAIL_TOKEN env var.")
    sys.exit(1)

print(f"Using client_id: {CLIENT_ID[:20]}...")

REDIRECT_URI = "http://localhost:8080"
auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        auth_code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Done! Close this tab and go back to terminal.</h1>")
    def log_message(self, *a):
        pass

# Start server first
print("Starting callback server...")
server = HTTPServer(("localhost", 8080), Handler)

auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "scope": "https://www.googleapis.com/auth/gmail.modify",
    "access_type": "offline",
    "prompt": "consent",
})

print("Opening browser...")
print()
print(">>> If browser doesn't open, paste this URL:")
print(auth_url)
print()
webbrowser.open(auth_url)

print("Waiting for authorization...")
server.handle_request()

if not auth_code:
    print("ERROR: No auth code received")
    sys.exit(1)

print("Exchanging code for tokens...")
post_data = urlencode({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": auth_code,
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT_URI,
}).encode()

req = Request("https://oauth2.googleapis.com/token", data=post_data, method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")
resp = urlopen(req)
tokens = json.loads(resp.read())
refresh_token = tokens.get("refresh_token")

if not refresh_token:
    print("ERROR: No refresh token returned")
    sys.exit(1)

gmail_token = json.dumps({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": refresh_token,
})

# Record issue timestamp so pipeline_health can warn before expiry.
# Testing-mode External apps with sensitive scopes die 7 days after issue.
try:
    from datetime import datetime, timezone
    from pathlib import Path
    stamp_path = Path(__file__).parent.parent / "data" / "gmail_token_issued.json"
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if stamp_path.exists():
        try:
            existing = json.loads(stamp_path.read_text())
        except Exception:
            existing = {}
    existing[CLIENT_ID] = {
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires": True,
        "expires_after_days": 7,
    }
    stamp_path.write_text(json.dumps(existing, indent=2))
    print(f"Recorded issue timestamp to {stamp_path}")
except Exception as e:
    print(f"Note: could not record issue timestamp: {e}")

print()
print("=" * 60)
print("SUCCESS! Update GMAIL_TOKENS in GitHub secrets with:")
print("=" * 60)
print(f"[{gmail_token}]")
print("=" * 60)
