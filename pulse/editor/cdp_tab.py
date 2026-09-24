"""One tab in the server's live Chrome (CDP 9223), driven over raw CDP.

Shared by gs_feed.py and login_status.py (2026-09-24). Playwright's connect_over_cdp on 9223
attaches to every open target (Gmail, ad iframes) and times out, and it would touch the owner's
own tabs, so this opens one new tab, speaks to that tab only, and closes it on exit. Page loads
are spaced MIN_GAP..MAX_GAP seconds apart and capped at max_loads per run, because the browser
exits through one fixed proxy IP and bursts get that IP rate-limited for the owner too.

    async with CdpTab(max_loads=30) as tab:
        info = await tab.goto(url, settle=8)      # {"url", "title", "text"}
        value = await tab.eval("document.title")
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.request

import websockets

CDP = "http://127.0.0.1:9223"
MIN_GAP, MAX_GAP = 3.0, 5.0


class LoadBudgetExceeded(RuntimeError):
    pass


def _http(path: str, method: str = "GET", cdp: str = CDP):
    req = urllib.request.Request(f"{cdp}{path}", method=method)
    raw = urllib.request.urlopen(req, timeout=20).read()
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


class CdpTab:
    def __init__(self, max_loads: int = 30, cdp: str = CDP):
        self.cdp = cdp
        self.max_loads = max_loads
        self.loads = 0
        self._last_load = 0.0
        self._n = 0
        self.tab_id = None
        self.ws = None

    async def __aenter__(self) -> "CdpTab":
        tab = _http("/json/new?about:blank", "PUT", self.cdp)
        self.tab_id = tab["id"]
        self.ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=60_000_000, open_timeout=30)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.ws is not None:
                await self.ws.close()
        finally:
            if self.tab_id:
                try:
                    _http(f"/json/close/{self.tab_id}", cdp=self.cdp)
                except Exception:  # noqa: BLE001
                    pass

    async def send(self, method: str, params: dict | None = None, timeout: float = 90) -> dict:
        self._n += 1
        n = self._n
        await self.ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            if msg.get("id") == n:
                return msg

    async def eval(self, expression: str):
        r = await self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    async def goto(self, url: str, settle: float = 8.0, ready: str | None = None, timeout: float = 20.0) -> dict:
        """Navigate, wait `settle` seconds (then, if `ready` is a JS expression, poll it once a second
        until it is truthy or `timeout` passes), and return the final url, title and body text."""
        if self.loads >= self.max_loads:
            raise LoadBudgetExceeded(f"load budget of {self.max_loads} used up")
        gap = random.uniform(MIN_GAP, MAX_GAP) - (time.monotonic() - self._last_load)
        if self._last_load and gap > 0:
            await asyncio.sleep(gap)
        self.loads += 1
        self._last_load = time.monotonic()
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(settle)
        if ready:
            end = time.monotonic() + timeout
            while time.monotonic() < end and not await self.eval(ready):
                await asyncio.sleep(1)
        info = await self.eval("JSON.stringify({url: location.href, title: document.title, "
                               "text: document.body ? document.body.innerText : ''})")
        self._last_load = time.monotonic()
        return json.loads(info or "{}")
