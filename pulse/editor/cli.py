"""Timer entry points.

  python cli.py ingest   every 10 min 11:00–15:59 UTC: create today's draft
                         from the synced brief and email the edit link once
  python cli.py send     12:15 ET: send today's draft unless held/sent
  python cli.py render --tier free --out x.html
  python cli.py test --tier premium
  python cli.py pdf [--date]   write the edition PDF (also runs after every send)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import paths
import auth
import ingest
import render
import sender
import drafts

paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(paths.LOG_DIR / "cli.log")],
)
logger = logging.getLogger("noon.cli")


def cmd_ingest(args) -> int:
    date = args.date or drafts.today_et()
    row = ingest.ingest(date, replace=args.replace)
    if row is None:
        logger.info(f"{date}: nothing to ingest yet")
        return 0
    if row.get("notified_at") is None and not args.no_notify:
        shown, total = render.free_count(row["json"])
        url = f"{paths.BASE_URL}/magic?d={date}&k={auth.magic_token(date)}"
        if sender.send_notification(date, url, shown, total):
            drafts.mark_notified(date)
    return 0


def cmd_send(args) -> int:
    date = args.date or drafts.today_et()
    row = drafts.get(date) or ingest.ingest(date)
    if row is None:
        logger.error(f"{date}: no draft and no stored brief — nothing sent")
        sender.send_alert(f"Nothing sent for {date}",
                          "No draft existed and no v4b brief for today had synced to the droplet "
                          "by 12:15 ET. Check the pulse-synth workflow and the Dropbox sync.")
        return 1
    if row["status"] == "sent":
        logger.info(f"{date}: already sent at {row['sent_at']}")
        return 0
    if row["status"] == "held" and not args.force:
        logger.info(f"{date}: held by the owner — not sending")
        return 0
    ok, line = sender.send_final(row["json"])
    if ok:
        drafts.set_status(date, "sent", send_log=f"timer: {line}")
        _pdf_after_send(row["json"])
        return 0
    logger.error(f"{date}: send failed — {line}")
    sender.send_alert(f"Send FAILED for {date}", line)
    return 1


def _pdf_after_send(draft: dict) -> None:
    try:
        import pdf
        pdf.publish_pdf(draft)
    except Exception as e:  # noqa: BLE001
        logger.error(f"pdf generation failed: {e}")
        sender.send_alert("PDF generation failed", str(e))


def cmd_pdf(args) -> int:
    import pdf
    date = args.date or drafts.today_et()
    row = drafts.get(date)
    if row is None:
        logger.error(f"{date}: no draft")
        return 1
    out = pdf.publish_pdf(row["json"], args.tier)
    print(out)
    return 0


def cmd_render(args) -> int:
    date = args.date or drafts.today_et()
    row = drafts.get(date)
    if row is None:
        logger.error(f"{date}: no draft")
        return 1
    html = render.preview(row["json"], args.tier)
    Path(args.out).write_text(html)
    print(f"wrote {args.out} ({len(html)} bytes)")
    return 0


def cmd_test(args) -> int:
    date = args.date or drafts.today_et()
    row = drafts.get(date)
    if row is None:
        logger.error(f"{date}: no draft")
        return 1
    return 0 if sender.send_test(row["json"], args.tier, args.to) else 1


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest"); a.add_argument("--date"); a.add_argument("--replace", action="store_true")
    a.add_argument("--no-notify", action="store_true"); a.set_defaults(fn=cmd_ingest)
    a = sub.add_parser("send"); a.add_argument("--date"); a.add_argument("--force", action="store_true")
    a.set_defaults(fn=cmd_send)
    a = sub.add_parser("render"); a.add_argument("--date"); a.add_argument("--tier", default="free")
    a.add_argument("--out", default="/tmp/noon_preview.html"); a.set_defaults(fn=cmd_render)
    a = sub.add_parser("pdf"); a.add_argument("--date"); a.add_argument("--tier", default="premium")
    a.set_defaults(fn=cmd_pdf)
    a = sub.add_parser("test"); a.add_argument("--date"); a.add_argument("--tier", default="free")
    a.add_argument("--to"); a.set_defaults(fn=cmd_test)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
