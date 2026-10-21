"""Gift links ("share with a non-subscriber") for FT, WSJ and Bloomberg, minted from Aziz's logged-in
subscriptions in the server Chrome (CDP 9223), for the Housing at Noon courtesy email (21 Oct 2026).

Aziz: "When we send them the link we should send them the gift link because I have subscriptions to the
FT, the Wall Street Journal, and Bloomberg. It's within policy: I'm allowed to send a gift link to somebody."

    from gift_links import gift_link
    gift_link("https://www.ft.com/content/42ed...")   # -> "https://giftarticle.ft.com/..." or None

    python gift_links.py URL [URL...]      mint (or reuse) and print one line per URL
    python gift_links.py --status          cached links and the last quota seen per host

`gift_link` never raises. It returns None for unsupported hosts, when the host is logged out, robot-checked
or out of quota, and on any error (logged to ~/work/noon/logs/gift_links.log).

How each publisher mints (recorded 21 Oct 2026 by watching the page's own requests; the module makes the
same calls with fetch() inside one article tab, so no clicking and no cookie copying):

  FT         GET  https://www.ft.com/article/gift-credits
                  -> {"allowance":20,"consumedCredits":1,"remainingCredits":19,"renewalDate":"2026-11-01T00:00:00.000Z"}
             GET  https://www.ft.com/article/gift-link/<article uuid>
                  -> {"redemptionUrl":"https://giftarticle.ft.com/giftarticle/actions/redeem/<token>",
                      "remainingAllowance":18,"redemptionLimit":3}
             QUOTA: 20 gift articles per calendar month ("Gift up to 20 articles per month to single
             non-subscribers"), renewing on the 1st. EACH LINK "can be viewed 3 times and is valid for 90 days",
             so a cached FT link is handed out at most FT_HANDOUTS_PER_LINK times. Minting the same article
             again returns the SAME link and spends no credit, so after 3 hand-outs that article cannot be
             gifted again (gift_link returns None) until the link expires. One FT article = at most 3 readers.

  WSJ        POST https://www.wsj.com/emailthis/share-url  body {"method":"copyurl","url":"<article url>"}
                  -> {"msg":"ok","shareUrl":"<article url>?st=<token>"}
             (The "Gift unlocked article" button calls exactly this and copies the URL to the clipboard.)
             QUOTA: none shown anywhere in the WSJ UI ("WSJ members: Sharing with this link will unlock this
             content for recipients"), and no official figure was found. DATA NEEDED. Any response without a
             shareUrl is treated as a refusal and minting pauses for a day.

  Bloomberg  GET  https://login.bloomberg.com/api/article-gifting/gift-articles
                  -> {"articles":[...ids...],"maxAllowedCount":5,"currentUsedCount":0,"articlesRemaining":5}
             PUT  https://login.bloomberg.com/api/article-gifting/generate-link
                  body {"articleUrl":"<article url>","articleId":"<meta parsely-post-id>"}
                  header x-csrf-token = current value of the readable _reg-csrf-token cookie (rotates after use)
                  -> {"articlesRemaining":4,"url":"<article url>?accessToken=<JWT>"}
             QUOTA: 5 gift articles per month ("As a subscriber, you can gift up to 5 articles each month").
             Re-gifting an article already in "articles" spends no credit (articlesRemaining unchanged).
             The JWT expires 7 days after minting (exp - iat = 604800 s), so cached links are re-minted after that.
             Bloomberg sometimes robot-checks the server Chrome; then this returns None and pauses Bloomberg for
             BLOCK_BACKOFF (Aziz clears the check by ticking the box at https://browser.homeeconomics.us).

Politeness: one tab per call batch (cdp_tab.CdpTab: one new tab, closed on exit, loads 3-5 s apart), at least
MIN_GAP_S seconds between mints across processes (last mint time is stored in the DB), and a file lock so two
processes never drive the browser at once. Cache: ~/work/noon/gift_links.db.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_tab import CdpTab  # noqa: E402

DB_PATH = Path(os.environ.get("NOON_GIFT_DB", str(Path.home() / "work" / "noon" / "gift_links.db")))
LOCK_PATH = DB_PATH.with_suffix(".lock")
LOG_DIR = Path(os.environ.get("NOON_LOG_DIR", str(Path.home() / "work" / "noon" / "logs")))
MIN_GAP_S = 20.0                       # between mints, across processes
FT_HANDOUTS_PER_LINK = 3               # FT: "Link can be viewed 3 times" -> new link after 3 hand-outs
EXPIRY_MARGIN = timedelta(days=1)      # re-mint a link this long before it expires
FT_LINK_DAYS = 90                      # FT: "valid for 90 days"
REFUSAL_BACKOFF = timedelta(hours=24)  # WSJ refusal / unknown quota state
BLOCK_BACKOFF = timedelta(hours=2)     # robot check or logged out
SETTLE_S = 8.0

LOG_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("noon.giftlinks")
if not log.handlers:
    h = logging.FileHandler(LOG_DIR / "gift_links.log")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

FT_UUID = re.compile(r"^/content/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
WSJ_ARTICLE = re.compile(r"^/(?!video/|livecoverage/|podcasts/).+-[0-9a-f]{8}$")
BBG_ARTICLE = re.compile(r"^/(news|opinion)/(articles|features|newsletters)/\d{4}-\d{2}-\d{2}/[^/]+$")
ROBOT = ("are you a robot", "verification required", "access is temporarily restricted", "unusual activity")


class Stop(Exception):
    """A host cannot mint right now (quota, robot check, logged out). Carries the pause length."""

    def __init__(self, reason: str, until: datetime):
        super().__init__(reason)
        self.reason, self.until = reason, until


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first_of_next_month(d: datetime) -> datetime:
    return (d.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1)


def classify(url: str) -> tuple[str, str] | None:
    """(host key, canonical article URL) for a giftable article, else None. Query and fragment are dropped
    (FT's ?syn-..., WSJ's ?mod=...)."""
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    path = u.path.rstrip("/")
    if host in ("ft.com", "www.ft.com") and FT_UUID.match(path):
        return "ft", f"https://www.ft.com{path}"
    if host in ("wsj.com", "www.wsj.com") and WSJ_ARTICLE.match(path):
        return "wsj", f"https://www.wsj.com{path}"
    if host in ("bloomberg.com", "www.bloomberg.com") and BBG_ARTICLE.match(path):
        return "bloomberg", f"https://www.bloomberg.com{path}"
    return None


# ---------------------------------------------------------------- cache

def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS gift_links (
            url TEXT PRIMARY KEY,          -- canonical article URL
            host TEXT NOT NULL,
            gift_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,               -- NULL = no known expiry (WSJ)
            max_handouts INTEGER,          -- NULL = unlimited
            handouts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS gift_history (  -- every link ever minted, incl. replaced ones
            url TEXT, host TEXT, gift_url TEXT, created_at TEXT, remaining_after INTEGER
        );
        CREATE TABLE IF NOT EXISTS gift_state (
            host TEXT PRIMARY KEY,
            remaining INTEGER, allowance INTEGER, resets_at TEXT,
            paused_until TEXT, pause_reason TEXT,
            last_mint_at TEXT, checked_at TEXT
        );
    """)
    return con


def _state(con, host: str) -> dict:
    r = con.execute("SELECT * FROM gift_state WHERE host=?", (host,)).fetchone()
    return dict(r) if r else {"host": host}


def _set_state(con, host: str, **kw) -> None:
    con.execute("INSERT OR IGNORE INTO gift_state(host) VALUES (?)", (host,))
    for k, v in kw.items():
        con.execute(f"UPDATE gift_state SET {k}=? WHERE host=?", (v, host))
    con.commit()


def _usable(row) -> bool:
    if row is None:
        return False
    exp = _parse(row["expires_at"])
    if exp and _now() > exp - EXPIRY_MARGIN:
        return False
    return row["max_handouts"] is None or row["handouts"] < row["max_handouts"]


# ---------------------------------------------------------------- minting (inside one logged-in tab)

FT_JS = """(async () => {
  const c = await fetch('/article/gift-credits', {credentials: 'include'});
  const credits = c.ok ? await c.json() : null;
  if (!credits) return JSON.stringify({stage: 'credits', status: c.status, body: (await c.text()).slice(0, 300)});
  if (!(credits.remainingCredits > 0)) return JSON.stringify({stage: 'quota', credits});
  const r = await fetch('/article/gift-link/' + %s, {credentials: 'include'});
  const body = await r.text();
  return JSON.stringify({stage: 'mint', status: r.status, credits, body: body.slice(0, 2000)});
})()"""

WSJ_JS = """(async () => {
  const r = await fetch('https://www.wsj.com/emailthis/share-url', {method: 'POST', credentials: 'include',
     headers: {'content-type': 'application/json'}, body: JSON.stringify({method: 'copyurl', url: %s})});
  return JSON.stringify({status: r.status, body: (await r.text()).slice(0, 2000)});
})()"""

BBG_JS = """(async () => {
  const m = document.querySelector('meta[name="parsely-post-id"]');
  const id = m && m.content;
  if (!id) return JSON.stringify({stage: 'id'});
  const q = await fetch('https://login.bloomberg.com/api/article-gifting/gift-articles', {credentials: 'include'});
  const quota = q.ok ? await q.json() : null;
  if (!quota) return JSON.stringify({stage: 'quota-read', status: q.status});
  if ((quota.articles || []).indexOf(id) < 0 && !(quota.articlesRemaining > 0)) return JSON.stringify({stage: 'quota', quota});
  const csrf = (document.cookie.match(/(?:^|; )_reg-csrf-token=([^;]+)/) || [])[1];
  if (!csrf) return JSON.stringify({stage: 'csrf'});
  const r = await fetch('https://login.bloomberg.com/api/article-gifting/generate-link', {method: 'PUT',
     credentials: 'include',
     headers: {'accept': 'application/json', 'content-type': 'application/json', 'x-csrf-token': decodeURIComponent(csrf)},
     body: JSON.stringify({articleUrl: %s, articleId: id})});
  return JSON.stringify({stage: 'mint', status: r.status, quota, body: (await r.text()).slice(0, 3000)});
})()"""


def _jwt_exp(url: str) -> datetime | None:
    try:
        tok = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["accessToken"][0]
        part = tok.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


async def _open(tab: CdpTab, host: str, url: str) -> None:
    info = await tab.goto(url, settle=SETTLE_S, ready="document.readyState === 'complete'", timeout=15)
    text = (info.get("text") or "")[:4000].lower() + " " + (info.get("title") or "").lower()
    if any(m in text for m in ROBOT):
        raise Stop("robot check shown in server Chrome: tick it at https://browser.homeeconomics.us", _now() + BLOCK_BACKOFF)
    final = urllib.parse.urlsplit(info.get("url", ""))
    if re.search(r"login|signin|sign-in|/sso|/auth", final.netloc + final.path, re.I):
        raise Stop(f"redirected to a login page ({final.netloc}): log in at https://browser.homeeconomics.us",
                   _now() + BLOCK_BACKOFF)


async def _mint(tab: CdpTab, con, host: str, url: str) -> tuple[str, datetime | None, int | None, int | None]:
    """Return (gift_url, expires_at, max_handouts, remaining_after). Raise Stop or RuntimeError."""
    await _open(tab, host, url)
    if host == "ft":
        uuid = FT_UUID.match(urllib.parse.urlsplit(url).path).group(1)
        res = json.loads(await tab.eval(FT_JS % json.dumps(uuid)) or "{}")
        cr = res.get("credits") or {}
        if cr:
            _set_state(con, host, remaining=cr.get("remainingCredits"), allowance=cr.get("allowance"),
                       resets_at=cr.get("renewalDate"), checked_at=_iso(_now()))
        if res.get("stage") == "credits":
            raise Stop(f"gift-credits HTTP {res.get('status')}: probably logged out of ft.com", _now() + BLOCK_BACKOFF)
        if res.get("stage") == "quota":
            raise Stop("FT monthly gift quota used up", _parse(cr.get("renewalDate")) or _first_of_next_month(_now()))
        body = json.loads(res.get("body") or "{}")
        gift = body.get("redemptionUrl")
        if res.get("status") != 200 or not gift:
            raise RuntimeError(f"FT gift-link HTTP {res.get('status')}: {res.get('body', '')[:200]}")
        left = body.get("remainingAllowance")
        _set_state(con, host, remaining=left, checked_at=_iso(_now()))
        return gift, _now() + timedelta(days=FT_LINK_DAYS), body.get("redemptionLimit") or FT_HANDOUTS_PER_LINK, left
    if host == "wsj":
        res = json.loads(await tab.eval(WSJ_JS % json.dumps(url)) or "{}")
        try:
            body = json.loads(res.get("body") or "{}")
        except ValueError:
            body = {}
        gift = body.get("shareUrl")
        if res.get("status") != 200 or not gift or "st=" not in gift:
            raise Stop(f"WSJ share-url refused (HTTP {res.get('status')}): {str(res.get('body'))[:200]}",
                       _now() + REFUSAL_BACKOFF)
        _set_state(con, host, checked_at=_iso(_now()))
        return gift, None, None, None
    if host == "bloomberg":
        res = json.loads(await tab.eval(BBG_JS % json.dumps(url)) or "{}")
        q = res.get("quota") or {}
        if q:
            _set_state(con, host, remaining=q.get("articlesRemaining"), allowance=q.get("maxAllowedCount"),
                       checked_at=_iso(_now()))
        if res.get("stage") == "id":
            raise RuntimeError("Bloomberg page has no parsely-post-id (not an article, or page did not render)")
        if res.get("stage") == "csrf":
            raise Stop("Bloomberg _reg-csrf-token cookie missing: probably logged out", _now() + BLOCK_BACKOFF)
        if res.get("stage") == "quota-read":
            raise Stop(f"Bloomberg gift quota unreadable (HTTP {res.get('status')}): logged out?", _now() + BLOCK_BACKOFF)
        if res.get("stage") == "quota":
            raise Stop("Bloomberg monthly gift quota used up", _first_of_next_month(_now()))
        body = json.loads(res.get("body") or "{}")
        gift = body.get("url")
        if res.get("status") != 200 or not gift or "accessToken=" not in gift:
            raise RuntimeError(f"Bloomberg generate-link HTTP {res.get('status')}: {res.get('body', '')[:200]}")
        left = body.get("articlesRemaining")
        _set_state(con, host, remaining=left, checked_at=_iso(_now()))
        return gift, _jwt_exp(gift) or _now() + timedelta(days=7), None, left
    raise RuntimeError(f"unsupported host {host}")


async def _mint_many(todo: list[tuple[str, str]], con) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    async with CdpTab(max_loads=len(todo)) as tab:
        for host, url in todo:
            st = _state(con, host)
            paused = _parse(st.get("paused_until"))
            if paused and _now() < paused:
                log.info(f"{host} paused until {st['paused_until']} ({st.get('pause_reason')}); skip {url}")
                out[url] = None
                continue
            last = _parse(st.get("last_mint_at"))
            all_last = [_parse(r[0]) for r in con.execute("SELECT last_mint_at FROM gift_state WHERE last_mint_at IS NOT NULL")]
            last = max([d for d in all_last + [last] if d], default=None)
            if last:
                wait = MIN_GAP_S - (_now() - last).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
            try:
                gift, exp, max_h, left = await _mint(tab, con, host, url)
            except Stop as s:
                _set_state(con, host, paused_until=_iso(s.until), pause_reason=s.reason)
                log.warning(f"{host} stop: {s.reason}; paused until {_iso(s.until)}; {url}")
                out[url] = None
                continue
            except Exception as e:  # noqa: BLE001
                log.error(f"{host} mint failed for {url}: {type(e).__name__}: {str(e)[:300]}")
                out[url] = None
                continue
            finally:
                _set_state(con, host, last_mint_at=_iso(_now()))
            old = con.execute("SELECT * FROM gift_links WHERE url=?", (url,)).fetchone()
            same = old is not None and old["gift_url"] == gift
            if same and old["max_handouts"] is not None and old["handouts"] >= old["max_handouts"]:
                # FT returns the SAME 3-view link for an article it has already gifted (seen 21 Oct 2026),
                # so a used-up link cannot be replaced until it expires.
                log.warning(f"{host} link for {url} already handed out {old['handouts']}x "
                            f"(limit {old['max_handouts']}) and the publisher re-issued the same link; returning None")
                out[url] = None
                continue
            handouts = old["handouts"] + 1 if same else 1
            created = old["created_at"] if same else _iso(_now())
            con.execute("""INSERT INTO gift_links(url, host, gift_url, created_at, expires_at, max_handouts, handouts)
                           VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT(url) DO UPDATE SET gift_url=excluded.gift_url, created_at=excluded.created_at,
                             expires_at=excluded.expires_at, max_handouts=excluded.max_handouts, handouts=excluded.handouts""",
                        (url, host, gift, created, (old["expires_at"] if same and old["expires_at"] else _iso(exp)), max_h, handouts))
            con.execute("INSERT INTO gift_history VALUES (?,?,?,?,?)", (url, host, gift, _iso(_now()), left))
            con.execute("UPDATE gift_state SET paused_until=NULL, pause_reason=NULL WHERE host=?", (host,))
            con.commit()
            log.info(f"{host} minted {url} -> {gift[:120]} (remaining this month: {left})")
            out[url] = gift
    return out


# ---------------------------------------------------------------- public API

def gift_links(urls: list[str]) -> dict[str, str | None]:
    """Map each input URL to a gift URL or None. Reuses cached links; mints the rest in one browser tab.
    Every call that returns a link counts as one hand-out (matters for FT's 3-view links)."""
    result: dict[str, str | None] = {u: None for u in urls}
    try:
        canon = {u: classify(u) for u in urls}
        con = _db()
        with open(LOCK_PATH, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            todo: list[tuple[str, str]] = []
            for u, c in canon.items():
                if not c:
                    continue
                host, url = c
                row = con.execute("SELECT * FROM gift_links WHERE url=?", (url,)).fetchone()
                if _usable(row):
                    con.execute("UPDATE gift_links SET handouts=handouts+1 WHERE url=?", (url,))
                    con.commit()
                    result[u] = row["gift_url"]
                elif (host, url) not in todo:
                    todo.append((host, url))
            if todo:
                minted = asyncio.run(_mint_many(todo, con))
                for u, c in canon.items():
                    if c and result[u] is None and c[1] in minted:
                        result[u] = minted[c[1]]
        con.close()
    except Exception as e:  # noqa: BLE001
        log.error(f"gift_links failed: {type(e).__name__}: {str(e)[:300]}")
    return result


def gift_link(url: str) -> str | None:
    """Gift URL for an FT / WSJ / Bloomberg article, or None (unsupported, logged out, robot check, quota, error)."""
    try:
        return gift_links([url]).get(url)
    except Exception as e:  # noqa: BLE001
        log.error(f"gift_link failed: {type(e).__name__}: {str(e)[:300]}")
        return None


def _status() -> None:
    con = _db()
    for r in con.execute("SELECT * FROM gift_state ORDER BY host"):
        print(dict(r))
    for r in con.execute("SELECT host, url, gift_url, created_at, expires_at, handouts, max_handouts FROM gift_links ORDER BY created_at DESC LIMIT 50"):
        print(f"{r['host']:9} {r['created_at'][:16]} used {r['handouts']}/{r['max_handouts'] or '-'} exp {(r['expires_at'] or '-')[:10]}  {r['url']}\n          -> {r['gift_url']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if args[0] == "--status":
        _status()
        sys.exit(0)
    for u, g in gift_links(args).items():
        print(f"{u}\t{g or 'None'}")
