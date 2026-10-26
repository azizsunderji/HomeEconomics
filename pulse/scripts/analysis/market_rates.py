"""Live 30-year mortgage rate and 10-year Treasury yield for the daily writer.

Owner's rule (Aziz, 26 Oct 2026): "whenever you quote mortgage rates or
ten-year yields, you need to quote the most recent one, not something that's
outdated and quoted in an article. Although if you're quoting an article, you
can say the article says this number, but the actual live number is whatever."
FRED is not used for "today" because it lags.

Sources
  30-year fixed   Mortgage News Daily daily index (page scrape; MND publishes
                  once a weekday at about 4 PM ET, so a morning run gets the
                  previous business day's reading, which is still the latest).
  10-year         CNBC quote service (live, intraday).
  10-year backup  Treasury daily par yield curve XML (end of day).

fetch_live_rates() never raises: a failed source is logged and left out.
The result is cached in ~/work/noon/market_rates/YYYY-MM-DD.json (ET date)
so the synthesis step, the v4b writer calls and the post-write check in one
run all use the same numbers.

Also here, because the writers and the runner share them:
  rates_prompt_block()   the LIVE MARKET NUMBERS block appended to writer prompts
  check_rate_mentions()  post-write scan for stale rate figures in entries
  notify_rate_warnings() one Telegram message via ~/.local/bin/tgnotify

Run:  python3 -m pulse.scripts.analysis.market_rates   (from the repo root)
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import subprocess
import xml.etree.ElementTree as XmlET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("market_rates")

ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
MND_URL = "https://www.mortgagenewsdaily.com/mortgage-rates"
CNBC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            "?symbols=US10Y&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}")
TREASURY_PAGE = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                 "TextView?type=daily_treasury_yield_curve")
CACHE_DIR = Path(os.environ.get("PULSE_MARKET_RATES_DIR",
                                str(Path.home() / "work" / "noon" / "market_rates")))
CACHE_MAX_AGE_MINUTES = 180   # one pipeline run (synthesis + v4b) fits well inside this
TGNOTIFY = Path.home() / ".local" / "bin" / "tgnotify"

_MEMO: dict = {}


# ─── fetch + parse ──────────────────────────────────────────────────────

def _get(url: str, timeout: int = 20) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                     timeout=timeout)
    r.raise_for_status()
    return r.text


def _num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def parse_mnd(page: str) -> dict:
    """30 Yr. Fixed from the Mortgage News Daily rates page.

    Two places on the page carry it: the top widget (rate, daily change,
    52-week range in a title attribute) and the comparison table, whose
    'Mortgage News Daily' header carries the reading's date (mm/dd/yy).
    Plain str.find + short bounded regexes on small slices only."""
    out: dict = {}
    i = page.find("30 Yr. Fixed")
    if i < 0:
        raise ValueError("'30 Yr. Fixed' not found on MND page")
    widget = page[i:i + 4000]
    m = re.search(r'class="rate">\s*(\d{1,2}\.\d{1,3})%', widget)
    if not m:
        raise ValueError("30-year rate value not found after '30 Yr. Fixed'")
    out["value"] = float(m.group(1))
    m = re.search(r'rate-daily-chg[^>]{0,80}>\s*([^<]{1,40})<', widget)
    if m:
        txt = html_lib.unescape(m.group(1)).strip().replace("−", "-")
        chg = _num(txt)
        if chg is not None:
            out["daily_change"] = -abs(chg) if txt.startswith("-") else abs(chg)
    title = html_lib.unescape(widget)
    m = re.search(r"Low:\s{0,3}>?\s{0,3}(\d{1,2}\.\d{1,3})%", title)
    if m:
        out["low_52w"] = float(m.group(1))
    m = re.search(r"High:\s{0,3}(\d{1,2}\.\d{1,3})%", title)
    if m:
        out["high_52w"] = float(m.group(1))
    # Reading date: the first comparison-table header that links to
    # /mortgage-rates/mnd with a mm/dd/yy date just before it (the same link
    # also sits in the nav menu and footers, without a date).
    anchor = 'href="/mortgage-rates/mnd">Mortgage News Daily</a>'
    j = page.find(anchor)
    while j > 0:
        dates = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{2})\b", page[max(0, j - 400):j])
        if dates:
            mo, d, y = dates[-1]
            out["as_of"] = f"20{y}-{int(mo):02d}-{int(d):02d}"
            break
        j = page.find(anchor, j + len(anchor))
    if j > 0:
        # Cross-check: the table's own 30-year value.
        k = page.find("30 Yr. Fixed", j)
        if k > 0:
            m = re.search(r'<td class="rate">\s*(\d{1,2}\.\d{1,3})%', page[k:k + 400])
            if m and abs(float(m.group(1)) - out["value"]) > 0.001:
                logger.warning(f"MND widget {out['value']}% differs from table {m.group(1)}%; "
                               f"using the table")
                out["value"] = float(m.group(1))
    return out


def fetch_mnd() -> dict:
    d = parse_mnd(_get(MND_URL))
    d.update({"source": "Mortgage News Daily", "url": MND_URL, "series": "30-year fixed, daily index"})
    if d.get("as_of"):
        d["as_of_label"] = datetime.strptime(d["as_of"], "%Y-%m-%d").strftime("%a %b %-d, %Y")
    return d


def parse_cnbc(text: str) -> dict:
    q = json.loads(text)["FormattedQuoteResult"]["FormattedQuote"][0]
    val = _num(q.get("last"))
    if val is None:
        raise ValueError(f"CNBC US10Y has no 'last': {q.get('last')!r}")
    out = {"value": val}
    lt = q.get("last_time")
    if lt:
        t = datetime.strptime(lt, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(ET)
        out["as_of"] = t.isoformat(timespec="minutes")
        out["as_of_label"] = t.strftime("%-I:%M %p ET %a %b %-d")
    for k_src, k_out in (("change", "daily_change"), ("previous_day_closing", "previous_close")):
        v = _num(q.get(k_src))
        if v is not None:
            out[k_out] = v
    return out


def fetch_cnbc_10y() -> dict:
    d = parse_cnbc(_get(CNBC_URL))
    d.update({"source": "CNBC", "url": "https://www.cnbc.com/quotes/US10Y", "series": "10-year Treasury yield, live"})
    return d


def parse_treasury_xml(text: str) -> Optional[dict]:
    """Latest (NEW_DATE, BC_10YEAR) in one month's Treasury yield-curve feed."""
    root = XmlET.fromstring(text)
    best: Optional[tuple[str, float]] = None
    for props in root.iter():
        if not props.tag.endswith("}properties"):
            continue
        date = val = None
        for child in props:
            if child.tag.endswith("}NEW_DATE"):
                date = (child.text or "")[:10]
            elif child.tag.endswith("}BC_10YEAR") and child.text:
                val = float(child.text)
        if date and val is not None and (best is None or date > best[0]):
            best = (date, val)
    if best is None:
        return None
    return {"value": best[1], "as_of": best[0],
            "as_of_label": datetime.strptime(best[0], "%Y-%m-%d").strftime("%a %b %-d, %Y") + " close"}


def fetch_treasury_10y(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(ET)
    first = now.replace(day=1)
    for month in (now, first - timedelta(days=1)):   # this month, then last month
        d = parse_treasury_xml(_get(TREASURY_URL.format(yyyymm=month.strftime("%Y%m")), timeout=30))
        if d:
            d.update({"source": "US Treasury", "url": TREASURY_PAGE,
                      "series": "10-year par yield, end of day"})
            return d
    raise ValueError("no 10-year entries in this or last month's Treasury feed")


# ─── main entry point ───────────────────────────────────────────────────

def _cache_path(date_et: str) -> Path:
    return CACHE_DIR / f"{date_et}.json"


def _cache_fresh(d: dict, max_age_minutes: int) -> bool:
    try:
        t = datetime.fromisoformat(d["fetched_at"])
    except Exception:  # noqa: BLE001
        return False
    if not (d.get("mortgage_30y") and d.get("treasury_10y")):
        return False   # refetch when a source failed last time
    return datetime.now(timezone.utc) - t < timedelta(minutes=max_age_minutes)


def fetch_live_rates(use_cache: bool = True,
                     max_age_minutes: int = CACHE_MAX_AGE_MINUTES) -> dict:
    """{'date_et', 'fetched_at' (UTC iso), 'fetched_at_et', 'fetched_at_label',
    'mortgage_30y': {...} | None, 'treasury_10y': {...} | None, 'errors': [...]}.
    Each rate dict has value, source, url, as_of, as_of_label (+ extras).
    Never raises."""
    now_et = datetime.now(ET)
    date_et = now_et.strftime("%Y-%m-%d")
    if use_cache:
        memo = _MEMO.get(date_et)
        if memo and _cache_fresh(memo, max_age_minutes):
            return memo
        try:
            p = _cache_path(date_et)
            if p.exists():
                d = json.loads(p.read_text())
                if _cache_fresh(d, max_age_minutes):
                    _MEMO[date_et] = d
                    return d
        except Exception as e:  # noqa: BLE001
            logger.warning(f"market rates cache unreadable: {e}")

    out: dict = {"date_et": date_et,
                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "fetched_at_et": now_et.isoformat(timespec="minutes"),
                 "fetched_at_label": now_et.strftime("%-I:%M %p ET, %a %b %-d, %Y"),
                 "mortgage_30y": None, "treasury_10y": None, "errors": []}
    try:
        out["mortgage_30y"] = fetch_mnd()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Mortgage News Daily fetch failed: {e}")
        out["errors"].append(f"mortgage_30y (Mortgage News Daily): {str(e)[:200]}")
    try:
        out["treasury_10y"] = fetch_cnbc_10y()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"CNBC 10-year fetch failed: {e}; trying Treasury end-of-day")
        out["errors"].append(f"treasury_10y (CNBC): {str(e)[:200]}")
        try:
            out["treasury_10y"] = fetch_treasury_10y(now_et)
        except Exception as e2:  # noqa: BLE001
            logger.warning(f"Treasury 10-year fetch failed: {e2}")
            out["errors"].append(f"treasury_10y (US Treasury): {str(e2)[:200]}")

    _MEMO[date_et] = out
    if use_cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _cache_path(date_et).write_text(json.dumps(out, indent=2))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"market rates cache not written: {e}")
    return out


# ─── writer prompt block ────────────────────────────────────────────────

def rates_prompt_block(rates: Optional[dict] = None) -> str:
    """The LIVE MARKET NUMBERS block appended to every entry-writer system
    prompt. Empty string when no rate could be fetched (the writer then
    runs exactly as before). Never raises."""
    try:
        rates = rates if rates is not None else fetch_live_rates()
        m, t = rates.get("mortgage_30y"), rates.get("treasury_10y")
        if not m and not t:
            return ""
        parts = []
        if m:
            extra = []
            if m.get("daily_change") is not None:
                extra.append(f"change on the day {m['daily_change']:+.2f}")
            if m.get("low_52w") is not None and m.get("high_52w") is not None:
                extra.append(f"52-week range {m['low_52w']:.2f}%-{m['high_52w']:.2f}%")
            parts.append(f"30-year fixed mortgage rate {m['value']:.2f}% ({m['source']}, "
                         + (f"latest daily reading dated {m['as_of_label']}" if m.get("as_of_label")
                            else "latest daily reading")
                         + (f"; {'; '.join(extra)}" if extra else "") + ")")
        if t:
            parts.append(f"10-year Treasury yield {t['value']:.2f}% ({t['source']}, "
                         f"{t.get('as_of_label') or 'today'})")
        x = f"{m['value']:.2f}" if m else "X.XX"
        return (
            "\n\n## LIVE MARKET NUMBERS (fetched today, "
            f"{rates.get('fetched_at_label')})\n"
            + "; ".join(parts) + ".\n"
            "RULE: whenever you state a mortgage rate or a 10-year yield, use these live "
            "numbers. If a source article quotes a different figure, attribute it to the "
            "article and give the live number alongside, e.g. 'HousingWire's piece cites "
            f"7.28%; Mortgage News Daily has the 30-year at {x}%.' Never present an "
            "article's rate as the current rate, and never say 'highest since' or 'biggest "
            "jump' based on an article's figure. Past levels (a year ago, a peak, a "
            "low) may be cited as past levels. Do not add these numbers to an entry that "
            "does not otherwise discuss mortgage rates or Treasury yields.\n"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"rates prompt block unavailable: {e}")
        return ""


# ─── post-write check ───────────────────────────────────────────────────

# Figures with a decimal point only: whole numbers ("above 7%", "below 5%")
# are nearly always thresholds, not quoted rates.
_FIG_RE = re.compile(r"(\d{1,2}\.\d{1,3})\s?(?:%|percent\b)")
# Alternation order matters: "other" (rates this check does not police) is
# tried first so "30-year Treasury" is not read as a 30-year mortgage rate.
_KW_RE = re.compile(
    r"(?P<other>15-year|15-yr|fifteen-year|jumbo|\bARMs?\b|adjustable|\bFHA\b|\bVA\b|"
    r"5/1|7/1|2-year|2-yr|two-year|5-year|five-year|20-year|"
    r"30-year (?:Treasury|Treasuries|bond|yield)|thirty-year (?:Treasury|bond)|"
    r"high-yield|yield-to-worst|\bmunis?\b|municipal|corporate bonds?|\bjunk\b)"
    r"|(?P<mortgage>mortgage rates?|30-year|30-yr|30 year|thirty-year)"
    r"|(?P<ten>10-year|10-yr|10 year|\b10y\b|ten-year|Treasury|Treasuries)",
    re.IGNORECASE)
# A bare maturity ("the 10-year slug priced at 4.7%") is the Treasury only with this context.
_TEN_BARE_RE = re.compile(r"(?:10-year|10-yr|10 year|ten-year)$", re.IGNORECASE)
_TEN_FOLLOW_RE = re.compile(r"\s(?:Treasury|Treasuries|yields?|notes?)\b", re.IGNORECASE)
_TEN_CONTEXT_RE = re.compile(r"Treasur|\b10y\b", re.IGNORECASE)
_ATTRIB_RE = re.compile(
    r"\b(?:cites?|cited|citing|says?|said|reports?|reported|according to|quotes?|quoted|"
    r"puts?|pegs?|pegged|estimates?|estimated|writes|wrote|notes|noted|claims?|claimed|"
    r"framed|article|piece|story|headline)\b", re.IGNORECASE)
_ATTRIB_AFTER_RE = re.compile(r"\b(?:according to|per|cited by|reported by|says|said)\b",
                              re.IGNORECASE)
_PAST_BEFORE_RE = re.compile(
    r"\b(?:from|peak(?:ed)?|peaking|high of|low of|last (?:year|week|month)|"
    r"a year (?:ago|earlier)|in (?:19|20)\d\d|pre-pandemic|previous(?:ly)?)\b", re.IGNORECASE)
_PAST_AFTER_RE = re.compile(
    r"\b(?:a year (?:ago|earlier)|year-ago|last (?:year|week|month)|in (?:19|20)\d\d|"
    r"previously|earlier this year|at the (?:start|beginning) of|"
    r"in (?:January|February|March|April|May|June|July|August|September|October|November|December))\b",
    re.IGNORECASE)
_LINK_RE = re.compile(r"\[([^\]]{0,500})\]\([^)\s]{0,2000}\)")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
MAX_KW_DISTANCE = 100
AFTER_PENALTY = 20
PLAUSIBLE_BAND = 3.0   # a figure further than this from the live value is some other number


def _classify(sentence: str, start: int, end: int, context: str = "") -> Optional[str]:
    best: Optional[tuple[int, str]] = None
    for m in _KW_RE.finditer(sentence):
        kind = m.lastgroup
        if (kind == "ten" and _TEN_BARE_RE.match(m.group(0))
                and not _TEN_FOLLOW_RE.match(sentence, m.end())
                and not _TEN_CONTEXT_RE.search(context or sentence)):
            kind = "other"
        if m.end() <= start:
            dist = start - m.end()
        elif m.start() >= end:
            dist = m.start() - end + AFTER_PENALTY
        else:
            continue
        if dist <= MAX_KW_DISTANCE and (best is None or dist < best[0]):
            best = (dist, kind)
    if best is None or best[1] == "other":
        return None
    return "mortgage_30y" if best[1] == "mortgage" else "treasury_10y"


def check_text(text: str, rates: dict, tolerance: float = 0.05) -> list[dict]:
    """Rate figures in `text` that differ from the live number by `tolerance`
    points or more and are not attributed or marked as past levels."""
    hits: list[dict] = []
    plain = _LINK_RE.sub(r"\1", text or "")
    for sent in _SENT_SPLIT_RE.split(plain):
        for fm in _FIG_RE.finditer(sent):
            kind = _classify(sent, fm.start(), fm.end(), plain)
            live = (rates.get(kind) or {}) if kind else {}
            if not live or live.get("value") is None:
                continue
            fig = float(fm.group(1))
            diff = round(fig - live["value"], 3)
            if abs(diff) > PLAUSIBLE_BAND or abs(diff) < tolerance:
                continue
            before = sent[:fm.start()]
            after = sent[fm.end():fm.end() + 40]
            if _ATTRIB_RE.search(before[-80:]) or _ATTRIB_AFTER_RE.search(after):
                continue
            if _PAST_BEFORE_RE.search(before[-40:]) or _PAST_AFTER_RE.search(after):
                continue
            hits.append({"kind": kind, "figure": fig, "live": live["value"], "diff": diff,
                         "live_source": live.get("source"), "live_as_of": live.get("as_of_label"),
                         "sentence": sent.strip()[:400]})
    return hits


def check_rate_mentions(entries: list[dict], rates: dict, tolerance: float = 0.05,
                        extra_texts: Optional[dict] = None) -> list[dict]:
    """Scan each entry's title and summary (plus any extra_texts, e.g. the
    intro) for stale mortgage-rate / 10-year figures. Reports only; never
    edits the prose."""
    out: list[dict] = []
    for i, e in enumerate(entries or [], 1):
        if not isinstance(e, dict):
            continue
        for field in ("title", "summary"):
            for h in check_text(e.get(field) or "", rates, tolerance):
                h.update({"entry_rank": e.get("rank", i), "entry_title": e.get("title"),
                          "field": field})
                out.append(h)
    for label, text in (extra_texts or {}).items():
        if isinstance(text, str):
            for h in check_text(text, rates, tolerance):
                h.update({"entry_rank": None, "entry_title": label, "field": label})
                out.append(h)
    return out


def format_warnings(warnings: list[dict], header: str) -> str:
    names = {"mortgage_30y": "30-yr mortgage", "treasury_10y": "10-yr Treasury"}
    lines = [header]
    for w in warnings:
        where = f"#{w['entry_rank']} " if w.get("entry_rank") else ""
        lines.append(f"- {where}{w.get('entry_title')}: says {w['figure']}% ({names[w['kind']]}); "
                     f"live {w['live']:.2f}% ({w['live_source']}, {w['live_as_of']})")
    return "\n".join(lines)


def notify_rate_warnings(warnings: list[dict], header: str) -> bool:
    """One Telegram message via ~/.local/bin/tgnotify. False when there is
    nothing to send, tgnotify is not on this machine (GitHub runners), or
    the send failed."""
    if not warnings:
        return False
    if not TGNOTIFY.exists():
        logger.info("tgnotify not on this machine; rate warnings stay in the stored brief")
        return False
    try:
        r = subprocess.run([str(TGNOTIFY), format_warnings(warnings, header)], timeout=60,
                           check=False, capture_output=True, text=True)
        return r.returncode == 0 and "sent" in (r.stdout or "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tgnotify failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import sys
    r = fetch_live_rates(use_cache="--no-cache" not in sys.argv)
    print(json.dumps(r, indent=2))
    print(rates_prompt_block(r))
