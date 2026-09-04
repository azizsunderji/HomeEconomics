"""Clean the links in a draft before anyone edits or renders it.

1. Tracking redirects (newsletter click-trackers such as
   links.message.bloomberg.com, awstrack.me, Substack /redirect/, Mailchimp,
   SendGrid …) are resolved to the article they point at, so readers see the
   real address and the source pills name the real outlet. Any URL whose
   final location sits on a different host is replaced; everything else is
   left exactly as written. Tracking query parameters (utm_*, mc_cid …) are
   dropped from the result.
2. `news_outlets` is rebuilt from the cited URLs with the renderer's own
   host -> name table, so the cards and the email pills agree and never show
   a collection channel ("gmail") or a subject line as a source.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

import paths  # noqa: F401
from delivery.email_lunch import _source_name_for_host

logger = logging.getLogger("noon.links")

_URL_RE = re.compile(r"\]\((https?://[^\s)]+)\)")
_TRACKING_PARAMS = re.compile(r"^(utm_|mc_cid|mc_eid|fbclid|gclid|mkt_tok|_hsenc|_hsmi|vero_id|ref$|cmpid$|srnd$|sref$)")
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
TIMEOUT = 8.0


def _strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def _awstrack(url: str) -> str | None:
    """awstrack.me/L0/<urlencoded target>/... -> target, no network."""
    m = re.match(r"https?://[^/]*awstrack\.me/L0/([^/]+)", url)
    if m:
        t = unquote(m.group(1))
        return t if t.startswith("http") else None
    return None


def _google_url(url: str) -> str | None:
    """google.com/url?url=<target> (Google Alerts wrapper) -> target, no network."""
    parts = urlsplit(url)
    if parts.netloc.lower().replace("www.", "") == "google.com" and parts.path == "/url":
        for k, v in parse_qsl(parts.query):
            if k in ("url", "q") and v.startswith("http"):
                return v
    return None


def resolve(url: str) -> str:
    """Final address for a redirecting link; the original if it does not
    redirect off its host or cannot be fetched."""
    direct = _awstrack(url) or _google_url(url)
    if direct:
        return _strip_tracking(direct)
    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT,
                          headers={"User-Agent": _UA, "Accept": "text/html,*/*"}) as c:
            # GET with a streamed body we never read: some trackers ignore HEAD.
            with c.stream("GET", url) as r:
                final = str(r.url)
    except Exception as e:  # noqa: BLE001
        logger.info(f"could not resolve {url[:60]}: {type(e).__name__}")
        return url
    if urlsplit(final).netloc.lower().replace("www.", "") == urlsplit(url).netloc.lower().replace("www.", ""):
        return url
    return _strip_tracking(final)


def resolve_summary(md: str, cache: dict[str, str]) -> tuple[str, int]:
    """Replace redirecting URLs in one markdown summary. Returns (text, n_changed)."""
    urls = list(dict.fromkeys(_URL_RE.findall(md or "")))
    todo = [u for u in urls if u not in cache]
    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for u, r in zip(todo, ex.map(resolve, todo)):
                cache[u] = r
    n = 0
    out = md
    for u in urls:
        r = cache[u]
        if r != u:
            out = out.replace(f"]({u})", f"]({r})")
            n += 1
    return out, n


OWN_HOSTS = ("homeeconomics.substack.com", "home-economics.us", "homeeconomics.us", "noon.homeeconomics.us")


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().replace("www.", "")


MAILCHIMP_ACCOUNTS = {"opennewyork": "Open New York"}


def outlets_for(entry: dict) -> list[str]:
    """Source pills for an entry: the renderer's name for each cited host, in
    order of first citation, minus our own properties (the publisher is not
    a source) and minus collection channels; Mailchimp-hosted newsletter
    pages are named after the newsletter (mailchi.mp/<account>/…)."""
    out: list[str] = []
    for u in dict.fromkeys(_URL_RE.findall(entry.get("summary") or "")):
        h = _host(u)
        if h in OWN_HOSTS:
            continue
        if h == "mailchi.mp":
            seg = urlsplit(u).path.strip("/").split("/")[0].lower()
            name = MAILCHIMP_ACCOUNTS.get(seg) or " ".join(w.capitalize() for w in re.split(r"[-_]+", seg)) or "Newsletter"
        else:
            name = (_source_name_for_host(h) or "").strip()
        if not name or name.lower() in ("gmail", "newsletter", "homeeconomics", "home economics"):
            continue
        if name not in out:
            out.append(name)
    return out


def clean_draft(draft: dict) -> dict:
    """Resolve redirects in every entry (and the paper), then rebuild each
    entry's news_outlets from the cited URLs. Mutates and returns the draft."""
    cache: dict[str, str] = {}
    changed = 0
    for e in draft.get("entries") or []:
        if not isinstance(e, dict):
            continue
        e["summary"], n = resolve_summary(e.get("summary") or "", cache)
        changed += n
        e["news_outlets"] = outlets_for(e)
        e["_pills"] = list(e["news_outlets"])  # the renderer shows exactly these
    paper = draft.get("paper_of_the_day")
    if isinstance(paper, dict) and paper.get("summary"):
        paper["summary"], n = resolve_summary(paper["summary"], cache)
        changed += n
    draft["_links_resolved"] = changed
    logger.info(f"links: {changed} redirect(s) resolved across {len(cache)} URLs")
    return draft
