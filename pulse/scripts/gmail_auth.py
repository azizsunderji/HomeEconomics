#!/usr/bin/env python3
"""One-time Gmail OAuth flow to get a refresh token.

Run locally only — requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.
"""

import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
import httpx

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
if not CLIENT_ID or not CLIENT_SECRET:
    print("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars first")
    exit(1)
REDIRECT_URI = "http://localhost:8080"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        auth_code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Done! You can close this tab.</h1>")

    def log_message(self, format, *args):
        pass  # Suppress logs

# Step 1: Open browser for consent
auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "scope": SCOPES,
    "access_type": "offline",
    "prompt": "consent",
})

print("Opening browser for Gmail authorization...")
webbrowser.open(auth_url)

# Step 2: Wait for redirect
server = HTTPServer(("localhost", 8080), Handler)
server.handle_request()

if not auth_code:
    print("ERROR: No auth code received")
    exit(1)

# Step 3: Exchange code for tokens
print("Exchanging code for tokens...")
resp = httpx.post("https://oauth2.googleapis.com/token", data={
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": auth_code,
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT_URI,
})

if resp.status_code != 200:
    print(f"ERROR: {resp.text}")
    exit(1)

tokens = resp.json()
refresh_token = tokens.get("refresh_token")

gmail_token = json.dumps({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": refresh_token,
})

print("\n=== SUCCESS ===")
print(f"\nYour GMAIL_TOKEN (save this):\n")
print(gmail_token)
print(f"\nFor your terminal:\n")
print(f"export GMAIL_TOKEN='{gmail_token}'")
