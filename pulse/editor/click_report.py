"""Daily click report for one Housing at Noon edition, from clicks.db (Resend webhook events).

Owner (21 Oct 2026): use click tracking to push free readers to the paid tier; the plumbing first. This report is the
first output of that plumbing: who clicked, who hit the upgrade wall and on which sources, and which free readers hit
the wall in more than one edition (the candidates for a courtesy email later). Runs weekdays at 17:00 ET
(noon-clickreport.timer), five hours after the send, and posts a summary to Telegram; the wall-hitters list is written
to ~/work/noon/clicks/wall_hitters_<edition>.csv.

Usage: click_report.py [--edition YYYY-MM-DD] [--days 14] [--telegram]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import paths  # noqa: F401  (loads the environment first)
import clicks
import drafts
import render

sys.path.insert(0, str(paths.SCRIPTS_DIR))
from delivery import variants  # noqa: E402

OUT_DIR = Path.home() / "work" / "noon" / "clicks"
TGNOTIFY = Path.home() / ".local" / "bin" / "tgnotify"


def _edition_sources(date: str) -> dict[str, dict]:
    """link_id -> {url, text, theme} for the edition's premium email."""
    row = drafts.get(date)
    if not row:
        return {}
    obj = row["json"] if isinstance(row["json"], dict) else json.loads(row["json"])
    premium_html, _free, _top = render.render_variants(obj)
    wm = variants.wall_map(premium_html)
    url_theme: dict[str, str] = {}
    for e in obj.get("entries") or []:
        blob = " ".join(str(e.get(k) or "") for k in ("summary", "context", "body", "text"))
        for u in re.findall(r"\((https?://[^)\s]+)", blob):
            url_theme.setdefault(u.rstrip(".,"), e.get("title") or "")
    for lid, d in wm.items():
        d["theme"] = url_theme.get(d["url"], "")
        d["host"] = (urlparse(d["url"]).netloc or "").removeprefix("www.")
    return wm


SCANNER_UA = re.compile(r"python-requests|curl/|go-http|java/|okhttp|libwww|wget|bot|crawler|spider|scanner|urlscan|"
                        r"proofpoint|mimecast|barracuda|forcepoint|symantec|trendmicro|sophos|paloalto|fortinet|zscaler",
                        re.I)
SCANNER_MAX_LINKS = 6      # a person reads one or two sources; a link checker opens every URL in the email
SCANNER_FAST_SECS = 120    # ...and does it within a couple of minutes of delivery


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def scanner_emails(ev) -> set[str]:
    """Recipients whose clicks are a mail-security link scanner, not a reader (owner, 21 Oct 2026: 616 of the first
    day's 720 "clickers" had opened ten or more links each). Flagged when any click comes from a non-browser user
    agent, when the address clicked SCANNER_MAX_LINKS or more distinct links in the edition, or when it clicked
    three or more distinct links within SCANNER_FAST_SECS of delivery. Their events are dropped from every count."""
    delivered = {}
    for r in ev:
        if r["type"] == "email.delivered":
            delivered[r["email"]] = min(delivered.get(r["email"], 1e18), _ts(r["created_at"]))
    links: dict[str, set] = defaultdict(set)
    fast: dict[str, set] = defaultdict(set)
    flagged: set[str] = set()
    for r in ev:
        if r["type"] != "email.clicked":
            continue
        e = r["email"]
        if SCANNER_UA.search(r["user_agent"] or ""):
            flagged.add(e)
        links[e].add(r["link"])
        if e in delivered and _ts(r["created_at"]) - delivered[e] <= SCANNER_FAST_SECS:
            fast[e].add(r["link"])
    flagged |= {e for e, s in links.items() if len(s) >= SCANNER_MAX_LINKS}
    flagged |= {e for e, s in fast.items() if len(s) >= 3}
    return flagged


def build(edition: str, days: int) -> tuple[str, list[dict]]:
    conn = clicks.connect()
    ev_all = conn.execute("SELECT * FROM events WHERE edition = ?", (edition,)).fetchall()
    scanners = scanner_emails(ev_all)
    # Scanner addresses keep their delivered/opened rows (they are real subscribers); only their clicks are dropped.
    ev = [r for r in ev_all if not (r["type"] == "email.clicked" and r["email"] in scanners)]
    scanner_clicks = len(ev_all) - len(ev)
    delivered = {t: {r["email"] for r in ev if r["type"] == "email.delivered" and r["tier"] == t} for t in ("free", "premium")}
    clickers = {t: {r["email"] for r in ev if r["type"] == "email.clicked" and r["tier"] == t} for t in ("free", "premium")}
    wall = [r for r in ev if r["type"] == "email.clicked" and r["wall"] and (r["tier"] or "free") == "free"]
    per_person: Counter = Counter(r["email"] for r in wall)
    by_link: Counter = Counter(r["link_id"] for r in wall if r["link_id"])
    other_free = Counter(r["link"] for r in ev if r["type"] == "email.clicked" and r["tier"] == "free" and not r["wall"])
    premium_links = Counter(r["link"] for r in ev if r["type"] == "email.clicked" and r["tier"] == "premium")
    bounced = sum(1 for r in ev if r["type"] == "email.bounced")
    complained = sum(1 for r in ev if r["type"] == "email.complained")
    sources = _edition_sources(edition)

    # repeat wall-hitters over the last `days` editions
    since = (datetime.strptime(edition, "%Y-%m-%d") - timedelta(days=days)).date().isoformat()
    win = conn.execute("SELECT * FROM events WHERE edition BETWEEN ? AND ?", (since, edition)).fetchall()
    by_ed: dict[str, list] = defaultdict(list)
    for r in win:
        by_ed[r["edition"]].append(r)
    rep_eds: dict[str, set] = defaultdict(set)
    rep_hits: Counter = Counter()
    for ed, rows_ in by_ed.items():
        sc = scanner_emails(rows_)
        for r in rows_:
            if r["type"] == "email.clicked" and r["wall"] and (r["tier"] or "free") == "free" and r["email"] not in sc:
                rep_eds[r["email"]].add(ed)
                rep_hits[r["email"]] += 1
    rep = sorted(({"email": e, "editions": len(s), "hits": rep_hits[e]} for e, s in rep_eds.items() if len(s) >= 2),
                 key=lambda d: (-d["editions"], -d["hits"]))

    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:.1f}%" if b else "n/a"

    lines = [f"Housing at Noon clicks — {edition}"]
    if not ev:
        lines.append("No events for this edition yet (webhook events arrive as readers open and click).")
        return "\n".join(lines), []
    for t in ("free", "premium"):
        lines.append(f"{t}: delivered {len(delivered[t]):,} · clicked {len(clickers[t]):,} ({pct(len(clickers[t]), len(delivered[t]))})")
    if scanners:
        lines.append(f"excluded {len(scanners):,} addresses whose clicks look like mail-security link scanners ({scanner_clicks:,} clicks)")
    lines.append(f"free readers who hit the upgrade wall: {len(per_person):,} people, {len(wall):,} clicks "
                 f"({pct(len(per_person), len(delivered['free']))} of delivered free)")
    if per_person:
        dist = Counter(min(n, 3) for n in per_person.values())
        lines.append(f"  1 wall click: {dist.get(1, 0)} · 2: {dist.get(2, 0)} · 3+: {dist.get(3, 0)}")
    if by_link:
        lines.append("most-wanted sources behind the wall:")
        for lid, n in by_link.most_common(6):
            s = sources.get(lid, {})
            label = s.get("theme") or s.get("text") or lid
            lines.append(f"  {n:3d}  {label[:60]}" + (f" ({s['host']})" if s.get("host") else ""))
    if other_free:
        lines.append("other free-edition clicks:")
        for u, n in other_free.most_common(4):
            lines.append(f"  {n:3d}  {u[:70]}")
    if premium_links:
        lines.append("premium clicks, top sources:")
        for u, n in premium_links.most_common(4):
            lines.append(f"  {n:3d}  {(urlparse(u).netloc or '').removeprefix('www.')} {urlparse(u).path[:50]}")
    if bounced or complained:
        lines.append(f"bounced {bounced} · complained {complained}")
    lines.append(f"repeat wall-hitters (2+ editions in {days} days): {len(rep):,}")
    for r in rep[:5]:
        lines.append(f"  {r['email']}  {r['editions']} editions, {r['hits']} clicks")

    rows = []
    for email, n in per_person.most_common():
        wanted = [sources.get(r["link_id"], {}).get("theme") or sources.get(r["link_id"], {}).get("text") or (r["link_id"] or "")
                  for r in wall if r["email"] == email]
        rows.append({"edition": edition, "email": email, "wall_clicks": n, "wanted": " | ".join(dict.fromkeys(w for w in wanted if w))})
    return "\n".join(lines), rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=None, help="YYYY-MM-DD (default: today in New York)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    edition = a.edition or drafts.today_et()
    text, rows = build(edition, a.days)
    print(text)
    if rows:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / f"wall_hitters_{edition}.csv"
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["edition", "email", "wall_clicks", "wanted"])
            w.writeheader(); w.writerows(rows)
        print(f"wrote {p}")
    if a.telegram and TGNOTIFY.exists():
        subprocess.run([str(TGNOTIFY), text[:3900]], check=False)


if __name__ == "__main__":
    main()
