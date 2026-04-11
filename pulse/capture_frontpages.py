#!/usr/bin/env python3
"""Capture screenshots of NYT and FT front pages and upload to Bluehost.

Runs locally on the home machine where the debug Chrome instance lives.
The screenshots are uploaded to https://home-economics.us/pulse-screenshots/
where the Pulse pipeline (running on GitHub Actions) can fetch them for
embedding in the daily email.
"""

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websocket

CDP = "http://localhost:9222"

OUT_DIR = Path("/tmp/pulse_frontpages")
OUT_DIR.mkdir(exist_ok=True)

SOURCES = [
    {
        "name": "nyt",
        "url": "https://www.nytimes.com",
        "wait": 15,
    },
    {
        "name": "ft",
        "url": "https://www.ft.com",
        "wait": 15,
    },
]

# Bluehost SFTP config
SSH_KEY = os.path.expanduser("~/.ssh/bluehost_deploy")
SSH_USER = "yxwrmjmy"
SSH_HOST = "home-economics.us"
REMOTE_DIR = "public_html/pulse-screenshots"


def send_recv(ws, msg_id, method, params=None):
    """Send a CDP command and wait for the matching response by id."""
    cmd = {"id": msg_id, "method": method}
    if params is not None:
        cmd["params"] = params
    ws.send(json.dumps(cmd))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            return resp


def capture_page(url, output_path, wait=15, viewport_width=1280, viewport_height=1600):
    """Navigate to URL via Chrome CDP and capture a screenshot."""
    resp = httpx.put(f"{CDP}/json/new?about:blank", timeout=15)
    tab = resp.json()
    ws_url = tab["webSocketDebuggerUrl"]
    tab_id = tab["id"]

    ws = websocket.create_connection(ws_url)
    try:
        send_recv(ws, 1, "Page.enable")
        send_recv(ws, 2, "Emulation.setDeviceMetricsOverride", {
            "width": viewport_width,
            "height": viewport_height,
            "deviceScaleFactor": 2,
            "mobile": False,
        })
        send_recv(ws, 3, "Page.navigate", {"url": url})
        time.sleep(wait)
        screenshot_resp = send_recv(ws, 4, "Page.captureScreenshot", {
            "format": "png",
            "captureBeyondViewport": False,
        })
        data = screenshot_resp.get("result", {}).get("data", "")
        if not data:
            print(f"No screenshot data for {url}: {screenshot_resp}")
            return False

        img_bytes = base64.b64decode(data)
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        print(f"Captured {output_path} ({len(img_bytes) / 1024:.0f} KB)")
        return True
    finally:
        ws.close()
        try:
            httpx.get(f"{CDP}/json/close/{tab_id}", timeout=5)
        except Exception:
            pass


def resize_image(input_path, output_path, max_width=900):
    """Resize image to a max width using PIL."""
    try:
        from PIL import Image
        img = Image.open(input_path)
        w, h = img.size
        if w > max_width:
            new_h = int(h * max_width / w)
            img = img.resize((max_width, new_h), Image.LANCZOS)
        # Save as JPEG for smaller email attachments
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        img.save(output_path, "JPEG", quality=82, optimize=True)
        size = os.path.getsize(output_path)
        print(f"Resized to {output_path} ({size / 1024:.0f} KB)")
        return True
    except Exception as e:
        print(f"Resize failed: {e}")
        return False


def upload_to_bluehost(local_files):
    """Upload files to Bluehost via SFTP."""
    if not os.path.exists(SSH_KEY):
        print(f"SSH key not found at {SSH_KEY} — skipping upload")
        return False

    # Ensure remote directory exists
    subprocess.run([
        "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{SSH_HOST}", f"mkdir -p {REMOTE_DIR}",
    ], check=True)

    # Upload each file
    for f in local_files:
        result = subprocess.run([
            "scp", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-q",
            f, f"{SSH_USER}@{SSH_HOST}:{REMOTE_DIR}/",
        ])
        if result.returncode != 0:
            print(f"Failed to upload {f}")
            return False
        print(f"Uploaded {os.path.basename(f)}")
    return True


def main():
    # 1. Check Chrome debug port is available
    try:
        httpx.get(f"{CDP}/json/version", timeout=3)
    except Exception:
        print("Chrome debug port (9222) not available — is the chrome-debug launchd agent running?")
        sys.exit(1)

    uploaded_files = []

    for source in SOURCES:
        name = source["name"]
        png_path = OUT_DIR / f"{name}.png"
        jpg_path = OUT_DIR / f"{name}.jpg"

        if not capture_page(source["url"], str(png_path), wait=source["wait"]):
            continue

        if not resize_image(str(png_path), str(jpg_path), max_width=900):
            continue

        uploaded_files.append(str(jpg_path))

    if not uploaded_files:
        print("No screenshots captured — nothing to upload")
        sys.exit(1)

    if upload_to_bluehost(uploaded_files):
        print(f"\nDone. Screenshots live at:")
        for f in uploaded_files:
            name = os.path.basename(f)
            print(f"  https://home-economics.us/pulse-screenshots/{name}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
