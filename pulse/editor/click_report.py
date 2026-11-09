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
import html
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
# Headless Chrome on a cloud server (owner, 26 Oct 2026: Eliot Brown's courtesy email was set off by ONE click on the
# upgrade link from AWS 3.229.6.145, 79 s after delivery, UA "Mozilla/5.0 (X11; Linux x86_64) ... Chrome/124").
# clicks.db stores the IP and user agent but no ASN or network owner, so a cloud address is recognised by the user
# agent: desktop Linux (not Android, not ChromeOS) or HeadlessChrome. Combined with the timing rule below, a real
# Linux reader is only affected if every click they make that day lands within two minutes of delivery.
CLOUD_UA = re.compile(r"\(X11; Linux x86_64\)|HeadlessChrome", re.I)


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def scanner_emails(ev) -> set[str]:
    """Recipients whose clicks are a mail-security link scanner, not a reader (owner, 21 Oct 2026: 616 of the first
    day's 720 "clickers" had opened ten or more links each). Flagged when any click comes from a non-browser user
    agent, when the address clicked SCANNER_MAX_LINKS or more distinct links in the edition, or when it clicked
    three or more distinct links within SCANNER_FAST_SECS of delivery. Also flagged (26 Oct 2026) when EVERY click the
    address made came within SCANNER_FAST_SECS of the delivery of that same email (matched on email_id) from a cloud
    user agent (CLOUD_UA): a single scanner click on the upgrade link no longer looks like a reader.
    Their events are dropped from every count."""
    delivered = {}
    delivered_id = {}   # (email, email_id) -> delivery time of that message
    for r in ev:
        if r["type"] == "email.delivered":
            t = _ts(r["created_at"])
            delivered[r["email"]] = min(delivered.get(r["email"], 1e18), t)
            k = (r["email"], r["email_id"])
            delivered_id[k] = min(delivered_id.get(k, 1e18), t)
    links: dict[str, set] = defaultdict(set)
    fast: dict[str, set] = defaultdict(set)
    early_cloud: dict[str, bool] = {}   # email -> every click so far was early and from a cloud user agent
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
        d = delivered_id.get((e, r["email_id"]), delivered.get(e))
        early = d is not None and _ts(r["created_at"]) - d <= SCANNER_FAST_SECS
        # 26 Oct 2026: timing alone. A proxy fleet (AWS first click, then Windows Chrome from 38.20x/212.103/23.8x)
        # hit ~30 of 49 wall-hitters with every click inside two minutes of delivery; the user-agent condition
        # missed it. Anyone whose every click that day landed within SCANNER_FAST_SECS of delivery is a scanner.
        early_cloud[e] = early_cloud.get(e, True) and early
    flagged |= {e for e, s in links.items() if len(s) >= SCANNER_MAX_LINKS}
    flagged |= {e for e, s in fast.items() if len(s) >= 3}
    flagged |= {e for e, v in early_cloud.items() if v}
    return flagged


def build(edition: str, days: int) -> tuple[list[str], list[dict]]:
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

    def esc(x) -> str:
        return html.escape(str(x), quote=False)

    def plural(n: int, word: str) -> str:
        return f"{n:,} {word}{'' if n == 1 else ('es' if word.endswith('s') else 's')}"

    def short_url(u: str) -> str:
        pu = urlparse(u)
        if "unsubscribe" in pu.path:
            return "Unsubscribe link"
        return ((pu.netloc or "").removeprefix("www.") + pu.path.rstrip("/"))[:70]

    # Owner, 9 Nov 2026: "Format it nicely like you do my new signups": Telegram HTML, an icon and bold header per
    # section, one short entry per line, no padded columns (Telegram's font is proportional) and no raw URLs.
    day = datetime.strptime(edition, "%Y-%m-%d")
    blocks = [f"<b>📊 Housing at Noon clicks: {day:%a} {day.day} {day:%b}</b>"]
    if not ev:
        blocks[0] += "\n<i>No events for this edition yet. They arrive as readers open and click.</i>"
        return blocks, []
    head = ["━━━━━━━━━━", "<b>📬 DELIVERED AND CLICKED</b>"]
    for t in ("free", "premium"):
        head.append(f"{t.capitalize()}: {len(delivered[t]):,} delivered, {len(clickers[t]):,} clicked "
                    f"({pct(len(clickers[t]), len(delivered[t]))})")
    head.append(f"Bounced: {bounced:,} · Marked as spam: {complained:,}")
    if scanners:
        head.append(f"<i>Left out: {plural(len(scanners), 'address')} whose clicks came from email security "
                    f"scanners ({scanner_clicks:,} clicks)</i>")
    blocks.append("\n".join(head))

    wall_b = ["━━━━━━━━━━", f"<b>🔒 HIT THE PAYWALL: {plural(len(per_person), 'free reader')}</b>"]
    if per_person:
        dist = Counter(min(n, 3) for n in per_person.values())
        wall_b.append(f"{len(wall):,} clicks. {dist.get(1, 0)} clicked one locked link, {dist.get(2, 0)} clicked two, "
                      f"{dist.get(3, 0)} clicked three or more.")
    if by_link:
        merged: Counter = Counter()
        for lid, n in by_link.items():
            src = sources.get(lid, {})
            label = src.get("theme") or src.get("text") or ""
            merged[(label, src.get("host") or "")] += n
        wall_b.append("\n<b>Most wanted locked sources</b>")
        for (label, host), n in merged.most_common(6):
            if label:
                wall_b.append(f"• {plural(n, 'click')}: {esc(label)}" + (f" ({esc(host)})" if host else ""))
            else:
                wall_b.append(f"• {plural(n, 'click')}: a link to {esc(host or 'an unknown site')}")
    blocks.append("\n".join(wall_b))

    if other_free:
        ob = ["━━━━━━━━━━", "<b>🔗 OTHER FREE-EDITION CLICKS</b>"]
        tally: Counter = Counter()
        for u, n in other_free.items():
            tally[short_url(u)] += n
        for u, n in tally.most_common(5):
            ob.append(f"• {plural(n, 'click')}: {esc(u)}")
        blocks.append("\n".join(ob))

    if premium_links:
        pb = ["━━━━━━━━━━", "<b>⭐ PREMIUM CLICKS</b>"]
        tally = Counter()
        for u, n in premium_links.items():
            tally[short_url(u)] += n
        for u, n in tally.most_common(5):
            pb.append(f"• {plural(n, 'click')}: {esc(u)}")
        blocks.append("\n".join(pb))

    rb = ["━━━━━━━━━━", f"<b>🔁 REPEAT PAYWALL HITTERS: {len(rep):,}</b>",
          f"<i>Free readers who hit the paywall in 2 or more editions in the last {days} days"
          + (f"; top {min(5, len(rep))} shown" if len(rep) > 5 else "") + "</i>"]
    blocks.append("\n".join(rb))
    for r in rep[:5]:
        blocks.append(f"<b>{esc(r['email'])}</b>\n<i>{r['editions']} editions · {plural(r['hits'], 'click')}</i>")

    rows = []
    for email, n in per_person.most_common():
        wanted = [sources.get(r["link_id"], {}).get("theme") or sources.get(r["link_id"], {}).get("text") or (r["link_id"] or "")
                  for r in wall if r["email"] == email]
        rows.append({"edition": edition, "email": email, "wall_clicks": n, "wanted": " | ".join(dict.fromkeys(w for w in wanted if w))})
    return blocks, rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=None, help="YYYY-MM-DD (default: today in New York)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--test-label", action="store_true", help="mark the Telegram message as a test")
    a = ap.parse_args()
    edition = a.edition or drafts.today_et()
    blocks, rows = build(edition, a.days)
    if a.test_label:
        blocks[0] = blocks[0].replace("<b>", "<b>(test of new format) ", 1)
    plain = re.sub(r"</?[bi]>", "", html.unescape("\n\n".join(blocks)))
    print(plain)
    if rows:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / f"wall_hitters_{edition}.csv"
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["edition", "email", "wall_clicks", "wanted"])
            w.writeheader(); w.writerows(rows)
        print(f"wrote {p}")
    if a.telegram:
        send_telegram(blocks)


def send_telegram(blocks: list[str]) -> None:
    """Telegram HTML, split between blocks under the 4,096-character cap; plain text through tgnotify if HTML fails."""
    import httpx
    chunks, cur = [], ""
    for block in blocks:
        if cur and len(cur) + len(block) + 2 > 3900:
            chunks.append(cur); cur = ""
        cur = (cur + "\n\n" + block) if cur else block
    chunks.append(cur)
    tg = Path.home() / ".claude" / "channels" / "telegram"
    tok = next(l.split("=", 1)[1].strip().strip('"').strip("'") for l in open(tg / ".env") if l.startswith("TELEGRAM_BOT_TOKEN="))
    chat = json.load(open(tg / "access.json"))["allowFrom"][0]
    for c in chunks:
        r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=20,
                       json={"chat_id": chat, "text": c, "parse_mode": "HTML", "disable_web_page_preview": True})
        print(f"telegram: {r.status_code}")
        if r.status_code != 200 and TGNOTIFY.exists():
            subprocess.run([str(TGNOTIFY), re.sub(r"</?[bi]>", "", html.unescape(c))], check=False)

if __name__ == "__main__":
    main()
