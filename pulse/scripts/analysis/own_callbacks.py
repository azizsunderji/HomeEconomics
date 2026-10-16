"""Callbacks to the owner's prior work (owner, 14 Oct 2026).

Owner's rule: "When there's a piece of news that touches on something we've written before (and
that's often the case), we should try to make a callback to that piece, along with the chart that I
included in that piece. This should be integrated into the pipeline and happen automatically."

Retrieval only. For every entry of the edition: embed its title and summary, compare with every
own publication (pulse/data/own_publications.json, built on the server by
pulse/editor/own_publications.py), and send the top candidates to a judge (stage 2), keep its pick when rated JUDGE_MIN_STRENGTH or more, with the
chart from that piece that best matches the entry. The writer pass in v4b_runner.py decides
whether the match really bears on the point and writes the sentence.

Calibrated on the owner's own callbacks in the 12 editions sent 28 Sep - 14 Oct 2026 (190 entries, 7
callbacks from news to an earlier piece; NewsAtNoon/scripts/313, 319, 320; study in
NewsAtNoon/outputs/callback_context_study.md). Cosine alone has no usable threshold: at 0.55 it keeps 1 of 7
and 100 other entries. His piece is always within the top 10 by cosine (ranks 1-9), so stage 2 sends
those 10 to a judge. Sonnet 5 at strength >= 4: his exact piece for 4 of 7 callbacks, 14 other picks
in 12 editions (most are defensible callbacks he did not make that day). Haiku: 117 other picks.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np

logger = logging.getLogger("own_callbacks")

OWN_PUBS_JSON = Path(__file__).resolve().parents[2] / "data" / "own_publications.json"
EMBED_MODEL = "text-embedding-3-small"
JUDGE_MIN_STRENGTH = 3          # 4 -> 3 on 15 Oct 2026: background/framework pieces count (owner)
JUDGE_MODEL = "claude-sonnet-5"  # Haiku rated 117 of 175 non-callback entries 4+; Sonnet 5 rated 14
PUB_TEXT_CHARS = 1500

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SLUG_RE = re.compile(r"(?:homeeconomics\.substack\.com|home-economics\.us|homeeconomics\.us)/p/([a-z0-9-]+)")


def load_own_publications(path: Path | str = OWN_PUBS_JSON) -> list[dict]:
    try:
        return json.loads(Path(path).read_text()).get("posts") or []
    except (OSError, ValueError) as e:
        logger.warning(f"own publications not loaded from {path}: {e}")
        return []


def _plain(md: str) -> str:
    md = _IMG_RE.sub("", md or "")
    md = md.replace("::: context", "").replace(":::", "")
    return _LINK_RE.sub(r"\1", md).strip()


def entry_text(title: str, summary: str) -> str:
    return f"{(title or '').strip()}\n{_plain(summary)}"[:8000]


def pub_text(p: dict) -> str:
    return f"{p.get('title') or ''}\n{p.get('summary') or ''}\n{(p.get('text') or '')[:PUB_TEXT_CHARS]}"[:8000]


def _embed(texts: list[str], openai_client) -> np.ndarray:
    out = []
    for i in range(0, len(texts), 100):
        resp = openai_client.embeddings.create(model=EMBED_MODEL, input=[t or " " for t in texts[i:i + 100]])
        out.extend(d.embedding for d in resp.data)
    e = np.array(out, dtype=np.float32)
    return e / np.clip(np.linalg.norm(e, axis=1, keepdims=True), 1e-8, None)


def cited_slugs(summary: str) -> set[str]:
    return set(_SLUG_RE.findall(summary or ""))


def score_matrix(entries: list[dict], pubs: list[dict], openai_client) -> tuple[np.ndarray, np.ndarray]:
    """(entries x pubs cosine matrix, entry embeddings)."""
    if not entries or not pubs:
        return np.zeros((len(entries), len(pubs))), np.zeros((len(entries), 1536))
    ee = _embed([entry_text(e.get("title"), e.get("summary")) for e in entries], openai_client)
    pe = _embed([pub_text(p) for p in pubs], openai_client)
    return ee @ pe.T, ee


def pick_chart(pub: dict, entry_emb: np.ndarray, openai_client) -> dict | None:
    """The chart of `pub` whose caption (and the sentence before it in the post) is closest to the entry."""
    charts = [c for c in pub.get("charts") or [] if c.get("hosted_url")]
    if not charts:
        return None
    if len(charts) == 1:
        return charts[0]
    ce = _embed([f"{c.get('caption') or ''}\n{c.get('context') or ''}" for c in charts], openai_client)
    return charts[int(np.argmax(ce @ entry_emb))]


def eligible_pubs(pubs: list[dict], as_of: str) -> list[dict]:
    """Pieces published before the edition date, with a hosted chart, that are not roundups."""
    return [p for p in pubs if (p.get("date") or "") < as_of and p.get("charts")
            and not ROUNDUP_RE.search(p.get("title") or "")]


def candidates(entries: list[dict], pubs: list[dict], openai_client) -> tuple[list[list[tuple[int, float]]], np.ndarray]:
    """Stage 1: for each entry, up to CANDIDATES_K (pub index, cosine) above COSINE_FLOOR, skipping
    pieces the entry already cites. Returns (lists, entry embeddings)."""
    sims, ee = score_matrix(entries, pubs, openai_client)
    out = []
    for i, e in enumerate(entries):
        skip = cited_slugs(e.get("summary") or "")
        order = [j for j in np.argsort(-sims[i]) if pubs[j]["slug"] not in skip and sims[i, j] >= COSINE_FLOOR]
        out.append([(int(j), float(sims[i, j])) for j in order[:CANDIDATES_K]])
    return out, ee


def find_callbacks(entries: list[dict], openai_client, as_of: str, anthropic_client=None,
                   judge_model: str = JUDGE_MODEL, pubs: list[dict] | None = None,
                   min_strength: int | None = None) -> tuple[dict[int, dict], list[dict]]:
    """entries: dicts with title and summary; as_of: the edition date (YYYY-MM-DD). Stage 1
    (embeddings) proposes up to CANDIDATES_K pieces per entry; stage 2 (judge) picks at most one and
    rates it; a pick with strength >= JUDGE_MIN_STRENGTH is a match. Returns ({entry index: match},
    log). A match carries score (cosine), strength, reason, slug, url, title, date, summary and the
    piece's chart closest to the entry {hosted_url, caption}."""
    min_strength = JUDGE_MIN_STRENGTH if min_strength is None else min_strength
    pubs = eligible_pubs(load_own_publications() if pubs is None else pubs, as_of)
    log: list[dict] = []
    if not pubs or not entries:
        return {}, log
    if anthropic_client is None:
        import anthropic
        anthropic_client = anthropic.Anthropic()
    cand, ee = candidates(entries, pubs, openai_client)
    out: dict[int, dict] = {}
    for i, e in enumerate(entries):
        rec = {"entry": i, "title": (e.get("title") or "")[:90],
               "candidates": [(pubs[j]["slug"], round(s, 3)) for j, s in cand[i][:5]]}
        if not cand[i]:
            rec["verdict"] = None
            log.append(rec)
            continue
        try:
            v = judge_entry(e, [pubs[j] for j, _ in cand[i]], anthropic_client, judge_model)
        except Exception as ex:  # noqa: BLE001
            v = {"pick": None, "strength": 0, "reason": f"judge failed: {ex}"[:200]}
        rec["verdict"] = v
        rec["matched"] = bool(v["pick"]) and v["strength"] >= min_strength
        log.append(rec)
        if not rec["matched"]:
            continue
        j, s = next((j, s) for j, s in cand[i] if pubs[j]["slug"] == v["pick"])
        p = pubs[j]
        chart = pick_chart(p, ee[i], openai_client)
        if not chart:
            continue
        out[i] = {"score": round(s, 3), "strength": v["strength"], "reason": v["reason"], "slug": p["slug"],
                  "url": p["url"], "title": p["title"], "date": p["date"], "summary": p.get("summary") or "",
                  "chart": {"hosted_url": chart["hosted_url"], "caption": chart.get("caption") or ""}
                  | {k: chart.get(k) for k in CHART_FRESHNESS_FIELDS}}
    return out, log




# ── stage 2: judge (one Haiku call per entry) ──────────────────────────
# Cosine alone cannot tell the owner's callbacks from same-topic pieces (calibration: his
# Question 7 -> NYC fast-track callback scored 0.51 while 124 of 183 non-callback entries had a
# better top score). So the top CANDIDATES_K pieces go to a judge that picks at most one and
# rates how directly its finding bears on the entry's point.
CANDIDATES_K = 10
COSINE_FLOOR = 0.45             # below this nothing is even a candidate
ROUNDUP_RE = re.compile(r"week in review|^product launch|^introducing|^welcome", re.I)

JUDGE_SYSTEM = (
    "You help Aziz Sunderji, who writes the Home Economics newsletter on US housing economics, decide whether "
    "a news entry in his daily briefing should call back to one of his earlier articles, with its chart. He does "
    "this a few times a week, not for every entry. A callback is right only when one article's central FINDING "
    "(what its chart shows) is about the same specific phenomenon the entry reports, so that 'as I wrote here' "
    "plus the chart tells readers something about this news they would not otherwise get. Examples he made: "
    "existing-home sales still weak -> his piece showing the pandemic surge in sales turned into a deficit vs "
    "trend; children leaving NYC schools -> his map of the exodus of children from the city; a debate over "
    "whether building lowers rents -> his piece showing building goes with higher, not lower, prices and rents; "
    "a Massachusetts ballot question on permitting -> his piece on which NYC districts build and why. "
    "Same broad topic (mortgages, prices, zoning) is NOT enough on its own. But a piece that explains the "
    "mechanism or history behind the news counts even when today's news moves the other way: an entry on "
    "rents rising in San Francisco is served by his pieces on what drives rents and the rent cycle, because "
    "the reader gets the framework for judging today's number. Answer with JSON only: "
    '{"pick": "<slug or null>", "strength": <1-5>, "reason": "<one sentence>"}. '
    "strength: 5 = the article's central finding is about exactly this phenomenon and its chart illustrates the "
    "entry's point; 4 = closely on point, he would likely link it; 3 = the piece supplies the framework, "
    "mechanism or history a reader needs for this news (he would plausibly link it as background); 2 = same "
    "broad topic only, nothing to learn; 1 = nothing relevant (pick null).")


def _model_kwargs(model: str) -> dict:
    if model.startswith("claude-sonnet-5-5"):   # rejects thinking disabled; lowest setting
        return {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
    if model.startswith(("claude-sonnet-5", "claude-opus-5")):
        return {"thinking": {"type": "disabled"}}
    return {}


def judge_entry(entry: dict, cands: list[dict], anthropic_client, model: str) -> dict:
    lines = [f"[{c['slug']}] {c['title']} ({c['date']}): {c.get('summary') or ''} CHARTS: "
             + "; ".join(ch.get("caption") or "" for ch in (c.get("charts") or [])[:4]) for c in cands]
    user = (f"NEWS ENTRY\nTitle: {entry.get('title') or ''}\n{_plain(entry.get('summary'))[:2500]}\n\n"
            f"EARLIER ARTICLES (slug in brackets)\n" + "\n".join(lines) +
            "\n\nWhich one article, if any, would make the best callback? JSON only.")
    resp = anthropic_client.messages.create(model=model, max_tokens=1500, system=JUDGE_SYSTEM,
                                            messages=[{"role": "user", "content": user}], **_model_kwargs(model))
    try:
        from analysis.anthropic_spend import record_usage
        record_usage(model, resp.usage)
    except Exception:  # noqa: BLE001
        pass
    text = "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    slugs = {c["slug"] for c in cands}
    pick = d.get("pick") if d.get("pick") in slugs else None
    return {"pick": pick, "strength": int(d.get("strength") or 0) if pick else 0, "reason": str(d.get("reason") or "")[:300]}


# ── chart staleness (owner, 16 Oct 2026) ───────────────────────────────
# Owner: "You are putting in charts that are like years old and obviously need to be updated. In some
# cases they don't really need to be updated ... It needs to be clever to figure out whether that chart
# needs updating or not." Each chart carries chart_kind / last_period_date / update_frequency from
# editor/own_chart_meta.py (Sonnet 5.5 reading the image). A time-series chart is out of date when its
# last period is older than 3 months; 30 days for data published daily or weekly, 60 days for monthly data
# (one release missing). The bond-yields chart of 16 Oct (daily/monthly yields through August 2026) is stale;
# the 3-month rule alone kept it.
# Static and unclear charts are shown as they are. Annual data: 14 months (next year's figure is rarely out
# sooner). A chart is also current while its piece is younger than 30 days (daily/weekly data), 45 days (monthly)
# or 3 months (other), since it carried the latest data when it was published. Used here (GitHub Actions) and by
# the server's updater.
CHART_MAX_AGE_DAYS = {"daily": 30, "weekly": 30, "monthly": 60, "annual": 425}
CHART_DEFAULT_MAX_AGE_DAYS = 92
CHART_MIN_PIECE_AGE_DAYS = {"daily": 30, "weekly": 30, "monthly": 45}


def chart_max_age_days(freq: str | None) -> int:
    return CHART_MAX_AGE_DAYS.get(freq or "", CHART_DEFAULT_MAX_AGE_DAYS)


def chart_needs_refresh(chart: dict, as_of: str, pub_date: str | None = None) -> bool:
    if chart.get("chart_kind") != "time-series":
        return False
    from datetime import date as _date
    freq = chart.get("update_frequency")
    try:
        today = _date.fromisoformat(as_of[:10])
        if pub_date and (today - _date.fromisoformat(pub_date[:10])).days <= CHART_MIN_PIECE_AGE_DAYS.get(freq or "", 92):
            return False
        lpd = chart.get("last_period_date")
        if not lpd:
            return True          # a time series whose end could not be read: check it
        return (today - _date.fromisoformat(lpd[:10])).days > chart_max_age_days(freq)
    except ValueError:
        return True


CHART_FRESHNESS_FIELDS = ("chart_kind", "last_period", "last_period_date", "update_frequency", "description",
                          "current_url", "refreshed_at", "refresh_status")
CHART_REUSE_DAYS = 7
CHART_REFRESH_TIMEOUT = 90
REFRESH_URL = "https://noon.homeeconomics.us/api/own-chart-refresh"


def _refresh_token() -> str:
    """Shared secret for the server's updater endpoint: HMAC of PULSE_UNSUB_SECRET (set in the V4b step and in
    ~/.noon_env on the server), so no new secret or workflow change is needed and the raw secret is never sent."""
    import hashlib
    import hmac
    import os
    sec = os.environ.get("PULSE_UNSUB_SECRET", "")
    return hmac.new(sec.encode(), b"own-chart-refresh", hashlib.sha256).hexdigest() if sec else ""


def resolve_callback_chart(match: dict, as_of: str) -> tuple[dict | None, str]:
    """Which image a callback shows (owner, 16 Oct 2026). Returns ({"url", "caption"} or None, reason).
    Static/unclear charts and recent time series: the original. Stale time series: its current_url if refreshed
    within CHART_REUSE_DAYS, else the server's updater (synchronous, CHART_REFRESH_TIMEOUT s). None when no
    current chart can be had: the caller then inserts the sentence without a chart."""
    import os
    from datetime import datetime, timedelta, timezone
    ch = match.get("chart") or {}
    orig = {"url": ch.get("hosted_url"), "caption": ch.get("caption") or ""}
    if not chart_needs_refresh(ch, as_of, match.get("date")):
        return orig, f"{ch.get('chart_kind') or 'unclassified'}; last period {ch.get('last_period')}: used as is"
    if ch.get("current_url") and ch.get("refreshed_at") and ch.get("refresh_status") in ("refreshed", "checked-current"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(ch["refreshed_at"])
            if age < timedelta(days=CHART_REUSE_DAYS):
                return {"url": ch["current_url"], "caption": ch.get("description") or orig["caption"]}, \
                    f"refreshed {age.days} days ago"
        except ValueError:
            pass
    m = re.search(r"/images/own/([a-z0-9-]+)/(\d+)\.png$", ch.get("hosted_url") or "")
    if not m:
        return None, "stale time series, chart URL not recognised"
    tok = _refresh_token()
    if not tok:
        return None, "stale time series; PULSE_UNSUB_SECRET not set, updater not called"
    import httpx
    try:
        r = httpx.post(os.environ.get("OWN_CHART_REFRESH_URL", REFRESH_URL),
                       json={"slug": m.group(1), "n": int(m.group(2)), "as_of": as_of, "timeout": CHART_REFRESH_TIMEOUT},
                       headers={"x-refresh-token": tok}, timeout=CHART_REFRESH_TIMEOUT + 15)
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return None, f"stale time series ({ch.get('last_period')}); updater call failed: {e}"[:300]
    if d.get("status") in ("current", "refreshed", "checked-current") and d.get("current_url"):
        cap = d.get("caption") or (ch.get("description") if d.get("status") == "refreshed" else None) or orig["caption"]
        return {"url": d["current_url"], "caption": cap}, f"{d['status']}: {d.get('reason')}"[:300]
    return None, f"stale time series ({ch.get('last_period')}); {d.get('status')}: {d.get('reason')}"[:300]
