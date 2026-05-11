#!/usr/bin/env python3
"""Capture screenshots of NYT and FT front pages and upload to Bluehost.

Two execution modes:
- Cloud mode (default if BROWSERBASE_API_KEY is set): creates a Browserbase
  session attached to the persistent context (cookies already saved during
  one-time browser login) and screenshots via CDP. No local Chrome required.
- Local mode (fallback): uses playwright + browser_cookie3 to read Chrome
  cookies from the local profile. For dev/debug only.

Uploads JPGs to https://home-economics.us/pulse-screenshots/ where the Pulse
pipeline embeds them in the daily email.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

OUT_DIR = Path("/tmp/pulse_frontpages")
OUT_DIR.mkdir(exist_ok=True)

# Bluehost SFTP config
SSH_KEY = os.environ.get("SFTP_KEY_PATH", os.path.expanduser("~/.ssh/bluehost_deploy"))
SSH_USER = os.environ.get("SFTP_USER", "yxwrmjmy")
SSH_HOST = os.environ.get("SFTP_HOST", "home-economics.us")
REMOTE_DIR = "public_html/pulse-screenshots"

# Browserbase config (set by GHA secrets or ~/.zprofile)
BB_API_KEY = os.environ.get("BROWSERBASE_API_KEY", "")
BB_PROJECT_ID = os.environ.get("BROWSERBASE_PROJECT_ID", "")
BB_CONTEXT_ID = os.environ.get("BROWSERBASE_CONTEXT_ID", "")
USE_BROWSERBASE = bool(BB_API_KEY and BB_PROJECT_ID and BB_CONTEXT_ID)

SOURCES = [
    {"name": "nyt", "url": "https://www.nytimes.com", "wait_ms": 5000},
    {"name": "ft",  "url": "https://www.ft.com",      "wait_ms": 5000},
]

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 1600


_CHROME_BASE = "/Users/azizsunderji/Library/Application Support/Google/Chrome"
_CHROME_PROFILES = ["Default"] + [f"Profile {i}" for i in range(1, 6)]


def _get_cookies(url: str) -> list[dict]:
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain.startswith('www.'):
        domain = '.' + domain[4:]   # www.nytimes.com -> .nytimes.com
    else:
        domain = '.' + domain
    seen, raw_cookies = set(), []
    for profile in _CHROME_PROFILES:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=f"{_CHROME_BASE}/{profile}/Cookies"
            )
            for c in jar:
                key = (c.name, c.domain)
                if key not in seen:
                    seen.add(key)
                    raw_cookies.append(c)
        except Exception:
            pass
    cookies = [
        {'name': c.name, 'value': c.value, 'domain': c.domain,
         'path': c.path, 'secure': bool(c.secure), 'httpOnly': False}
        for c in raw_cookies
    ]
    print(f"  Loaded {len(cookies)} cookies for {domain}")
    return cookies


def _create_bb_session() -> tuple[str, str]:
    """Create a Browserbase session attached to the persistent context.

    Returns (session_id, connect_ws_url).
    """
    payload = json.dumps({
        "projectId": BB_PROJECT_ID,
        "browserSettings": {
            "context": {"id": BB_CONTEXT_ID, "persist": True},
            "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        },
        "timeout": 600,
    }).encode()
    req = urllib.request.Request(
        "https://api.browserbase.com/v1/sessions",
        data=payload,
        headers={"x-bb-api-key": BB_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return data["id"], data["connectUrl"]


def _release_bb_session(session_id: str) -> None:
    try:
        req = urllib.request.Request(
            f"https://api.browserbase.com/v1/sessions/{session_id}",
            data=json.dumps({"projectId": BB_PROJECT_ID, "status": "REQUEST_RELEASE"}).encode(),
            headers={"x-bb-api-key": BB_API_KEY, "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"  Failed to release Browserbase session: {e}")


async def _capture_browserbase() -> list[tuple[str, Path, Path]]:
    print(f"Capturing via Browserbase context {BB_CONTEXT_ID[:8]}...")
    captured = []
    session_id, connect_url = _create_bb_session()
    print(f"  Session: {session_id[:8]}...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(connect_url)
            ctx = browser.contexts[0]
            for source in SOURCES:
                name = source["name"]
                url = source["url"]
                png_path = OUT_DIR / f"{name}.png"
                jpg_path = OUT_DIR / f"{name}.jpg"
                try:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.set_viewport_size({"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
                    await page.goto(url, wait_until="load", timeout=45000)
                    await page.wait_for_timeout(source["wait_ms"])
                    await page.screenshot(path=str(png_path), full_page=False)
                    print(f"  Captured {name}: {png_path} ({png_path.stat().st_size // 1024} KB)")
                    captured.append((name, png_path, jpg_path))
                except Exception as e:
                    print(f"  Failed to capture {name}: {e}")
            await browser.close()
    finally:
        _release_bb_session(session_id)
    return captured


async def _capture_local() -> list[tuple[str, Path, Path]]:
    print("Capturing via local Chrome (browser_cookie3)...")
    if browser_cookie3 is None:
        print("  browser_cookie3 not installed — local mode unavailable")
        return []
    captured = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            channel='chrome',
            args=[f'--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}'],
        )
        for source in SOURCES:
            name = source["name"]
            url = source["url"]
            png_path = OUT_DIR / f"{name}.png"
            jpg_path = OUT_DIR / f"{name}.jpg"
            try:
                cookies = _get_cookies(url)
                context = await browser.new_context(
                    viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                    device_scale_factor=2,
                    user_agent=(
                        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/147.0.0.0 Safari/537.36'
                    ),
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto(url, wait_until='load', timeout=30000)
                await page.wait_for_timeout(source["wait_ms"])
                await page.screenshot(path=str(png_path), full_page=False)
                await context.close()
                print(f"  Captured {name}: {png_path} ({png_path.stat().st_size // 1024} KB)")
                captured.append((name, png_path, jpg_path))
            except Exception as e:
                print(f"  Failed to capture {name}: {e}")
        await browser.close()
    return captured


async def _capture_all():
    if USE_BROWSERBASE:
        return await _capture_browserbase()
    return await _capture_local()


def _resize_to_jpg(png_path: Path, jpg_path: Path, max_width: int = 900) -> bool:
    try:
        from PIL import Image
        img = Image.open(png_path)
        w, h = img.size
        if w > max_width:
            new_h = int(h * max_width / w)
            img = img.resize((max_width, new_h), Image.LANCZOS)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        img.save(jpg_path, "JPEG", quality=82, optimize=True)
        print(f"  Resized to {jpg_path} ({jpg_path.stat().st_size // 1024} KB)")
        return True
    except Exception as e:
        print(f"  Resize failed: {e}")
        return False


def _upload_to_bluehost(local_files: list[str]) -> bool:
    # In GHA the key is in SFTP_KEY (env var). In local mode it's a file.
    sftp_key_content = os.environ.get("SFTP_KEY", "")
    key_path = SSH_KEY
    if sftp_key_content and not os.path.exists(key_path):
        key_path = "/tmp/sftp_key"
        with open(key_path, "w") as f:
            f.write(sftp_key_content)
            if not sftp_key_content.endswith("\n"):
                f.write("\n")
        os.chmod(key_path, 0o600)

    if not os.path.exists(key_path):
        print(f"SSH key not found at {key_path} — skipping upload")
        return False

    subprocess.run([
        "ssh", "-i", key_path, "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{SSH_HOST}", f"mkdir -p {REMOTE_DIR}",
    ], check=True)

    for f in local_files:
        result = subprocess.run([
            "scp", "-i", key_path, "-o", "StrictHostKeyChecking=no", "-q",
            f, f"{SSH_USER}@{SSH_HOST}:{REMOTE_DIR}/",
        ])
        if result.returncode != 0:
            print(f"  Failed to upload {f}")
            return False
        print(f"  Uploaded {os.path.basename(f)}")
    return True


def main():
    print("Capturing front page screenshots...")
    captured_raw = asyncio.run(_capture_all())

    upload_files = []
    for name, png_path, jpg_path in captured_raw:
        if _resize_to_jpg(png_path, jpg_path):
            upload_files.append(str(jpg_path))

    if not upload_files:
        print("No screenshots captured — nothing to upload")
        sys.exit(1)

    if _upload_to_bluehost(upload_files):
        print("\nDone. Screenshots live at:")
        for f in upload_files:
            print(f"  https://home-economics.us/pulse-screenshots/{os.path.basename(f)}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
