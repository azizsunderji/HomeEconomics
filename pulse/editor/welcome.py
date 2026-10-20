"""Welcome edition for a new free subscriber (owner, 20 Oct 2026): the most recent sent edition, free tier, with a
short note on top ("thanks for signing up, here is today's edition, the next one lands tomorrow at noon Eastern", or
"on Monday" over a weekend). Sent through Resend from the usual address.

    python welcome.py --to aziz@home-economics.us        # test send
    python welcome.py --to someone@x.com --html out.html # also write the HTML
"""
from __future__ import annotations
import argparse, logging, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(__file__))
import drafts, render, sender, paths

logger = logging.getLogger("noon.welcome")
ET = ZoneInfo("America/New_York")
INK, GREY, BLUE, SOFT = "#3D3733", "#7F7570", "#0BB4FF", "#F6F7F3"


def next_edition_phrase(now: datetime | None = None, latest_date: str | None = None) -> str:
    """'tomorrow at noon Eastern', 'today at noon Eastern', 'on Monday at noon Eastern', or 'on Tuesday ...'."""
    now = now or datetime.now(ET)
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    d = now.date()
    # the next weekday noon strictly after now, skipping the latest edition's own date if it already went out
    cand = d if (now < noon and d.weekday() < 5 and (latest_date or "") != d.isoformat()) else d + timedelta(days=1)
    while cand.weekday() >= 5:
        cand += timedelta(days=1)
    if cand == d:
        return "today at noon Eastern"
    if cand == d + timedelta(days=1):
        return "tomorrow at noon Eastern"
    return f"on {cand.strftime('%A')} at noon Eastern"


def banner(latest_date: str, when: str, is_today: bool) -> str:
    which = "today's edition" if is_today else f"the most recent edition, from {render.date_label(latest_date)}"
    return (
        f'<div style="max-width:600px;margin:0 auto;padding:22px 24px 18px;background:{SOFT};'
        f'font-family:Georgia,\'Times New Roman\',serif;color:{INK};">'
        f'<p style="margin:0 0 10px;font-size:22px;line-height:1.3;">Thanks for signing up.</p>'
        f'<p style="margin:0;font-size:16px;line-height:1.5;color:{INK};">Here is {which}, so you can see what Housing at Noon '
        f'looks like. The next edition lands in your inbox {when}. The free edition carries the day\'s first five themes; '
        f'<a href="https://homeeconomics.us/noon/upgrade" style="color:{BLUE};text-decoration:none;">premium</a> has every theme with a direct link to every source.</p>'
        f'<p style="margin:12px 0 0;font-size:14px;color:{GREY};">Aziz Sunderji</p>'
        f'</div><div style="height:18px;"></div>'
    )


def welcome_html(draft: dict, now: datetime | None = None, unsub_url: str | None = None) -> str:
    now = now or datetime.now(ET)
    _premium, free_html, _top = render.render_variants(draft)
    is_today = draft["date"] == now.date().isoformat()
    html = free_html
    i = html.lower().find("<body")
    j = html.find(">", i) + 1 if i >= 0 else 0
    html = html[:j] + banner(draft["date"], next_edition_phrase(now, draft["date"]), is_today) + html[j:]
    return render.with_footer(html, unsub_url)


def send_welcome(to: str, html_out: str | None = None) -> bool:
    row = drafts.latest_sent()
    if not row:
        logger.warning("welcome: no edition to send"); return False
    draft = row["json"] if isinstance(row["json"], dict) else __import__("json").loads(row["json"])
    unsub = None
    try:
        from delivery.subscribers import get_subscribers, make_unsubscribe_url
        me = next((u for u in get_subscribers() if u.get("email", "").lower() == to.lower()), None)
        unsub = make_unsubscribe_url(me["user_id"]) if me and me.get("user_id") else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"welcome: no unsubscribe link ({e})")
    html = welcome_html(draft, unsub_url=unsub)
    if html_out:
        open(html_out, "w").write(html)
    subject = f"Welcome to Housing at Noon: the {render.date_label(draft['date'])} edition"
    ok = sender._post_resend(sender._api_key(), "https://api.resend.com/emails",
                             {"from": render.EMAIL_FROM, "to": [to], "subject": subject, "html": html, "reply_to": sender.REPLY_TO})
    logger.info(f"welcome edition ({draft['date']}) to {to}: {'ok' if ok else 'FAILED'}")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(); ap.add_argument("--to", default=paths.OWNER_EMAIL); ap.add_argument("--html")
    a = ap.parse_args()
    print("sent" if send_welcome(a.to, a.html) else "FAILED")
