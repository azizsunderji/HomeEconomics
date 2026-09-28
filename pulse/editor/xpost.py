"""UNUSED since 28 Sep 2026. Aziz set this module aside ("I think we don't need the
elaborate posting mechanism, just the cards formatted right, for both platforms"): the
editor no longer imports it, its /api/xpost routes and the "Post to X" panel were removed,
and the ~/work/noon/xposts state folder was deleted (it held no posted entries). Kept in
the repo for reference only.

Post an edition's four cards to X as one carousel post, only after the owner approves.

Owner's rule (Aziz, 27 Sep 2026: "Yes with approval pls"): nothing posts without his click
on "Approve and post" on the /cards/{date} page. There is no timer and no automatic post.

How it works: ONE new tab in the server's live Chrome (CDP 9223, cdp_tab.CdpTab; never
Playwright on 9223), which is logged in to X. It opens https://x.com/compose/post, types
the post text, attaches the four card PNGs to the composer's file input with
DOM.setFileInputFiles, waits for the four thumbnails, and then either
  preview(date)        screenshots the composer, closes it and discards the draft, or
  post_carousel(date)  clicks Post, waits for X's confirmation, reads the new post's URL
                       from the "View" link in X's toast (or the profile page), screenshots,
and closes the tab. Actions are spaced 1-3 s apart; at most 2 page loads per run, because
the browser exits through one fixed proxy IP (memory: no-scripted-bursts-on-proxy).

A login page aborts with "not logged in: re-login at https://browser.homeeconomics.us".

State: /home/aziz/work/noon/xposts/state.json {date: {status, url, posted_at, text, ...}}.
status: draft (text saved) | posting (Post is being clicked) | posted | unconfirmed (Post
was clicked but X's confirmation was not seen; check X by hand) | error (failed before the
click; safe to retry). Anything except draft/error blocks another post for that date, so
a date is never posted twice. Screenshots: xposts/<date>.png (after posting),
xposts/<date>_preview.png (composer preview).

    python xpost.py text 2026-09-25        # default post text and its length
    python xpost.py preview 2026-09-25     # composer screenshot, nothing posted
    (posting is done from the editor page only)
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import paths  # noqa: F401  (sys.path setup)
import cards
import drafts
from cdp_tab import CdpTab

logger = logging.getLogger("noon.xpost")

XPOST_DIR = Path(os.environ.get("NOON_XPOST_DIR", str(Path.home() / "work" / "noon" / "xposts")))
STATE_PATH = XPOST_DIR / "state.json"
LOCK_PATH = XPOST_DIR / ".lock"
COMPOSE_URL = "https://x.com/compose/post"
CTA = "Free edition daily at noon ET: https://www.homeeconomics.us/noon?utm_source=x&utm_medium=social"
LIMIT = 280
RELOGIN = "not logged in: re-login at https://browser.homeeconomics.us"
BLOCKING = ("posting", "posted", "unconfirmed")

TEXTBOX = '[data-testid="tweetTextarea_0"]'
FILE_INPUT = 'input[data-testid="fileInput"]'
POST_BTN = '[data-testid="tweetButton"]'


class NotLoggedIn(RuntimeError):
    pass


class AlreadyPosted(RuntimeError):
    pass


# ── text ────────────────────────────────────────────────────────────────

_URL = re.compile(r"https?://\S+")


def x_length(text: str) -> int:
    """Length as X counts it: every URL is 23; characters outside X's light ranges
    (most emoji, CJK, and a few punctuation marks such as the ellipsis) count 2."""
    n = 0
    rest = _URL.sub("", text)
    n += 23 * len(_URL.findall(text))
    for ch in rest:
        o = ord(ch)
        light = o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247
        n += 1 if light else 2
    return n


def default_text(draft: dict) -> str:
    """First two sentences of the standfirst (or the first theme's title), a blank line,
    then the sign-up line. Kept under 280 plain characters: two sentences, else one,
    else the first sentence cut at a word with an ellipsis."""
    s = cards.sentences(cards.plain(draft.get("intro") or ""))
    if not s:
        hook, _ = cards.hook_line(draft)
        s = [hook]
    budget = LIMIT - 1 - len(CTA) - 2
    lead = " ".join(s[:2])
    if len(lead) > budget:
        lead = s[0]
    if len(lead) > budget:
        lead = lead[: budget - 1].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return f"{lead}\n\n{CTA}"


# ── state ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(state: dict) -> None:
    XPOST_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, STATE_PATH)


def _update(date: str, **fields) -> dict:
    state = _load()
    rec = state.get(date, {})
    rec.update(fields)
    state[date] = rec
    _save(state)
    return rec


def get_state(date: str) -> dict:
    return _load().get(date, {})


@contextlib.contextmanager
def _lock():
    """One X run at a time (a preview and a post must never overlap)."""
    XPOST_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another X preview or post is running; try again in a minute")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def info(date: str) -> dict:
    """What the editor page shows: stored state plus the default text."""
    row = drafts.get(date)
    if row is None:
        raise KeyError(date)
    rec = get_state(date)
    dflt = default_text(row["json"])
    text = rec.get("text") or dflt
    return {"date": date, "status": rec.get("status") or "none", "url": rec.get("url"),
            "posted_at": rec.get("posted_at"), "error": rec.get("error"), "text": text,
            "default_text": dflt, "length": len(text), "x_length": x_length(text), "limit": LIMIT,
            "preview_at": rec.get("preview_at"), "preview_note": rec.get("preview_note"),
            "has_preview": (XPOST_DIR / f"{date}_preview.png").exists(),
            "has_shot": (XPOST_DIR / f"{date}.png").exists(),
            "cards": [str(p) for p in card_files(date)]}


def save_text(date: str, text: str) -> dict:
    rec = get_state(date)
    if rec.get("status") in BLOCKING:
        raise AlreadyPosted(f"{date} is {rec['status']}; the text can no longer change")
    _check_text(text)
    _update(date, text=text, status=rec.get("status") or "draft")
    return info(date)


def _check_text(text: str) -> None:
    if not text.strip():
        raise ValueError("the post text is empty")
    if x_length(text) > LIMIT:
        raise ValueError(f"the post text is {x_length(text)} characters as X counts them; the limit is {LIMIT}")


def card_files(date: str) -> list[Path]:
    return [cards.CARDS_DIR / f"Housing at Noon {date} card{i}.png" for i in range(1, 5)]


def _ensure_cards(date: str) -> list[Path]:
    files = card_files(date)
    if not all(f.exists() for f in files):
        row = drafts.get(date)
        if row is None:
            raise KeyError(date)
        cards.publish_cards(row["json"])
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise RuntimeError(f"cards missing after rendering: {', '.join(missing)}")
    return files


# ── browser ─────────────────────────────────────────────────────────────

async def _pause(lo: float = 1.0, hi: float = 3.0) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _js(s: str) -> str:
    return json.dumps(s)


async def _wait(tab: CdpTab, expr: str, timeout: float, step: float = 1.0):
    end = asyncio.get_event_loop().time() + timeout
    while True:
        v = await tab.eval(expr)
        if v:
            return v
        if asyncio.get_event_loop().time() > end:
            return v
        await asyncio.sleep(step)


async def _shot(tab: CdpTab, out: Path) -> None:
    r = await tab.send("Page.captureScreenshot", {"format": "png"})
    data = r.get("result", {}).get("data")
    if data:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data))


async def _click(tab: CdpTab, selector: str) -> bool:
    """A real mouse click at the element's centre (React and X's editor need one)."""
    box = await tab.eval(f"""(() => {{ const el = document.querySelector({_js(selector)});
        if (!el) return null; el.scrollIntoView({{block: 'center'}}); const r = el.getBoundingClientRect();
        return {{x: r.left + r.width / 2, y: r.top + r.height / 2}}; }})()""")
    if not box:
        return False
    for kind in ("mousePressed", "mouseReleased"):
        await tab.send("Input.dispatchMouseEvent", {"type": kind, "x": box["x"], "y": box["y"],
                                                    "button": "left", "clickCount": 1})
        await asyncio.sleep(0.08)
    return True


async def _type(tab: CdpTab, text: str) -> None:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            await tab.send("Input.insertText", {"text": line})
        if i < len(lines) - 1:
            for kind in ("keyDown", "keyUp"):
                ev = {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                      "nativeVirtualKeyCode": 13}
                if kind == "keyDown":
                    ev["text"] = "\r"
                await tab.send("Input.dispatchKeyEvent", ev)
            await asyncio.sleep(0.15)


LOGIN_URL = re.compile(r"/login|/i/flow/(login|signup)|/logout|account/access", re.I)


async def _open_composer(tab: CdpTab) -> None:
    info_ = await tab.goto(COMPOSE_URL, settle=5,
                           ready=f"!!document.querySelector({_js(TEXTBOX)}) || /\\/login|\\/i\\/flow/.test(location.href)",
                           timeout=25)
    url = info_.get("url", "")
    if LOGIN_URL.search(url):
        raise NotLoggedIn(RELOGIN)
    if not await tab.eval(f"!!document.querySelector({_js(TEXTBOX)})"):
        text = (info_.get("text") or "")[:2000]
        if re.search(r"\b(Sign in|Log in|Sign up)\b", text):
            raise NotLoggedIn(RELOGIN)
        raise RuntimeError(f"X composer did not appear (page: {url}, text starts: {text[:160]!r})")


THUMBS_JS = """(() => { const a = document.querySelector('[data-testid="attachments"]');
  if (!a) return 0;
  const imgs = [...a.querySelectorAll('img')].filter(i => (i.src || '').startsWith('blob:') || (i.src || '').includes('pbs.twimg'));
  return imgs.length; })()"""
UPLOADING_JS = """(() => { const a = document.querySelector('[data-testid="attachments"]');
  return !!a && /Uploading|\\d+%/.test(a.innerText || ''); })()"""
POST_READY_JS = f"""(() => {{ const b = document.querySelector({_js(POST_BTN)});
  return !!b && b.getAttribute('aria-disabled') !== 'true' && !b.disabled; }})()"""
NOTICE_JS = """(() => { const alerts = [...document.querySelectorAll('[role="alert"], [data-testid="toast"]')];
  return alerts.map(a => a.innerText).join(' | ').slice(0, 300); })()"""


async def _compose(tab: CdpTab, text: str, files: list[Path]) -> dict:
    await _open_composer(tab)
    await _pause()
    if not await _click(tab, TEXTBOX):
        raise RuntimeError("could not focus the X text box")
    await _pause(0.8, 1.6)
    await _type(tab, text)
    await _pause()
    r = await tab.send("Runtime.evaluate", {"expression": f"document.querySelector({_js(FILE_INPUT)})"})
    obj = r.get("result", {}).get("result", {}).get("objectId")
    if not obj:
        raise RuntimeError("X composer has no image file input (the page layout may have changed)")
    await tab.send("DOM.enable")
    r = await tab.send("DOM.setFileInputFiles", {"files": [str(f) for f in files], "objectId": obj})
    if "error" in r:
        raise RuntimeError(f"attaching the cards failed: {r['error']}")
    n = await _wait(tab, f"{THUMBS_JS} >= {len(files)}", timeout=60)
    thumbs = await tab.eval(THUMBS_JS)
    await _wait(tab, f"!{UPLOADING_JS}", timeout=60)
    typed = await tab.eval(f"(document.querySelector({_js(TEXTBOX)}) || {{}}).innerText || ''")
    notice = await tab.eval(NOTICE_JS)
    if not n:
        raise RuntimeError(f"X showed {thumbs} of {len(files)} image thumbnails" + (f" (X says: {notice})" if notice else ""))
    return {"thumbnails": thumbs, "typed_chars": len(typed or ""), "notice": notice or ""}


async def _discard(tab: CdpTab) -> str:
    """Close the composer and discard the unsent post. Returns what happened."""
    closed = False
    for sel in ('[data-testid="app-bar-close"]', '[aria-label="Close"]'):
        if await _click(tab, sel):
            closed = True
            break
    if not closed:
        return "no close button found; the tab was closed with the post unsent"
    await _pause(1.0, 2.0)
    dialog = await tab.eval("""(() => { const d = document.querySelector('[data-testid="confirmationSheetDialog"]');
        return d ? d.innerText.replace(/\\s+/g, ' ').slice(0, 200) : ''; })()""")
    if not dialog:
        return "composer closed; X showed no save/discard dialog"
    clicked = await tab.eval("""(() => { const d = document.querySelector('[data-testid="confirmationSheetDialog"]');
        const b = [...d.querySelectorAll('button, [role="button"]')].find(x => /^\\s*Discard\\s*$/i.test(x.innerText));
        if (!b) return false; b.click(); return true; })()""")
    await _pause(1.0, 2.0)
    return (f"X asked: \"{dialog}\"; clicked Discard" if clicked
            else f"X asked: \"{dialog}\"; no Discard button found, tab closed without saving")


async def _preview(date: str, text: str, files: list[Path]) -> dict:
    out = XPOST_DIR / f"{date}_preview.png"
    async with CdpTab(max_loads=1) as tab:
        await tab.send("Page.enable")
        res: dict = {}
        try:
            res.update(await _compose(tab, text, files))
            await _pause()
            await _shot(tab, out)
        finally:
            try:
                res["discard"] = await _discard(tab)
            except Exception as e:  # noqa: BLE001
                res["discard"] = f"discard step failed: {e}"
            logger.info(f"xpost preview {date}: discard -> {res['discard']}")
    res["screenshot"] = str(out)
    return res


async def _post(date: str, text: str, files: list[Path]) -> dict:
    out = XPOST_DIR / f"{date}.png"
    async with CdpTab(max_loads=2) as tab:
        await tab.send("Page.enable")
        try:
            res = await _compose(tab, text, files)
            if not await _wait(tab, POST_READY_JS, timeout=45):
                raise RuntimeError("the Post button did not become active (uploads unfinished?)")
        except Exception:
            # nothing was posted; leave no unsent draft behind
            with contextlib.suppress(Exception):
                logger.info(f"xpost post {date}: failed before Post; discard -> {await _discard(tab)}")
            raise
        await _pause()
        # From here on a failure may mean the post went out: never retry automatically.
        _update(date, status="posting", text=text, clicked_at=_now())
        if not await _click(tab, POST_BTN):
            _update(date, status="error", error="Post button vanished before the click")
            raise RuntimeError("Post button vanished before the click")
        link = await _wait(tab, """(() => { const t = document.querySelector('[data-testid="toast"]');
            if (!t) return null; const a = t.querySelector('a[href*="/status/"]');
            return a ? a.href : (/sent|posted/i.test(t.innerText) ? 'sent' : null); })()""", timeout=30)
        gone = await tab.eval(f"!document.querySelector({_js(TEXTBOX)})")
        notice = await tab.eval(NOTICE_JS)
        await _pause()
        await _shot(tab, out)
        url = link if link and link != "sent" else None
        confirmed = bool(link) or bool(gone)
        if confirmed and not url:
            url = await _find_url(tab, text)
            if url:
                await _shot(tab, out)
    return {**res, "confirmed": confirmed, "url": url, "notice": notice or "", "screenshot": str(out)}


async def _find_url(tab: CdpTab, text: str) -> str | None:
    """Open the account's profile and take the newest post whose text starts like ours."""
    prof = await tab.eval("""(() => { const a = document.querySelector('[data-testid="AppTabBar_Profile_Link"]');
        return a ? a.href : null; })()""")
    if not prof:
        return None
    await tab.goto(prof, settle=5, ready="document.querySelectorAll('article').length > 0", timeout=20)
    probe = " ".join(text.split())[:40]
    return await tab.eval(f"""(() => {{ for (const art of document.querySelectorAll('article')) {{
        const t = (art.innerText || '').replace(/\\s+/g, ' ');
        if (t.includes({_js(probe)})) {{ const a = art.querySelector('a[href*="/status/"] time');
          if (a) return a.parentElement.href; }} }} return null; }})()""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── public entry points ────────────────────────────────────────────────

def preview(date: str, text: str | None = None) -> dict:
    """Everything except Post: composer screenshot to xposts/<date>_preview.png, then the
    composer is closed, the draft discarded and the tab closed."""
    with _lock():
        rec = get_state(date)
        if text is None:
            text = rec.get("text") or info(date)["default_text"]
        _check_text(text)
        files = _ensure_cards(date)
        if rec.get("status") not in BLOCKING:
            _update(date, text=text, status=rec.get("status") or "draft")
        try:
            res = asyncio.run(_preview(date, text, files))
        except NotLoggedIn as e:
            _update(date, preview_note=str(e), preview_at=_now())
            raise
        _update(date, preview_at=_now(), preview_note=res.get("discard"))
        logger.info(f"xpost preview {date}: {res}")
        return res


def post_carousel(date: str, text: str | None = None) -> dict:
    """Post the four cards with the text. Refuses when this date was already posted (or a
    post was clicked but not confirmed)."""
    with _lock():
        rec = get_state(date)
        if rec.get("status") in BLOCKING:
            raise AlreadyPosted(f"{date} was already {rec['status']}" + (f": {rec.get('url')}" if rec.get("url") else ""))
        if text is None:
            text = rec.get("text") or info(date)["default_text"]
        _check_text(text)
        files = _ensure_cards(date)
        _update(date, text=text, status="draft", error=None)
        try:
            res = asyncio.run(_post(date, text, files))
        except NotLoggedIn as e:
            _update(date, status="error", error=str(e))
            raise
        except Exception as e:  # noqa: BLE001
            cur = get_state(date).get("status")
            if cur == "posting":  # clicked, then something broke: do not allow a retry
                _update(date, status="unconfirmed", error=f"{type(e).__name__}: {e}")
            else:
                _update(date, status="error", error=f"{type(e).__name__}: {e}")
            raise
        if res["confirmed"]:
            _update(date, status="posted", url=res.get("url"), posted_at=_now(), error=None)
        else:
            _update(date, status="unconfirmed", url=res.get("url"),
                    error="Post was clicked but X's confirmation was not seen; check the account on X")
        logger.info(f"xpost post {date}: {res}")
        return {**res, **get_state(date)}


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(sys.argv) < 3 or sys.argv[1] not in ("text", "preview"):
        print(__doc__)
        return 2
    cmd, date = sys.argv[1], sys.argv[2]
    if cmd == "text":
        i = info(date)
        print(i["text"])
        print(f"\n[{i['length']} characters; {i['x_length']} as X counts them]")
        return 0
    try:
        print(json.dumps(preview(date), indent=2))
    except NotLoggedIn as e:
        print(str(e))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
