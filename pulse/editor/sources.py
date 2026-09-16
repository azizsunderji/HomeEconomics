"""LinkedIn sources page: which accounts the collector reads, and what they post.

Three inputs, merged by account key (``in/<slug>`` or ``company/<slug>``):
  * the live target list, ``pulse/scripts/collectors/linkedin_targets.json``
    (in git; the GitHub Actions collector reads it from the repo);
  * candidates, the owner's LinkedIn interactions list from 2026-09-03
    (``NOON_LINKEDIN_CANDIDATES``, a CSV in Dropbox, deliberately not in the
    public repo), plus any account removed from the targets on this page;
  * recent activity: LinkedIn posts already collected into the synced
    ``pulse.db`` (read-only, free), and on-demand previews fetched from Apify
    for accounts that are not collected yet (about $0.0015 a post, cached in
    ``NOON_LINKEDIN_PREVIEWS``).

Including or excluding an account rewrites the targets file in the working
clone right away; ``publish`` commits that one file and pushes it so the next
collection run uses it.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import paths

logger = logging.getLogger("noon.sources")

REPO_DIR = paths.PULSE_DIR.parent
TARGETS_PATH = paths.SCRIPTS_DIR / "collectors" / "linkedin_targets.json"
CANDIDATES_CSV = Path(os.environ.get(
    "NOON_LINKEDIN_CANDIDATES",
    "/home/aziz/Dropbox/Home Economics/2026_09_03_LinkedIn_HousingVoices/outputs/housing_contacts.csv"))
NOON_DIR = Path.home() / "work" / "noon"
PREVIEWS_PATH = Path(os.environ.get("NOON_LINKEDIN_PREVIEWS", str(NOON_DIR / "linkedin_previews.json")))
REMOVED_PATH = NOON_DIR / "linkedin_removed.json"

MAX_TARGETS = int(os.environ.get("LINKEDIN_MAX_TARGETS", "80"))  # same cap as the collector
ACTIVITY_DAYS = 30
FETCH_BATCH_MAX = 25        # accounts per Apify run from this page
FETCH_POSTS = 5             # posts per account, last month
COST_PER_POST_USD = 0.0015  # harvestapi/linkedin-profile-posts pay-per-event

_lock = threading.Lock()


# ── keys and files ──────────────────────────────────────────────────────

def account_key(url: str) -> str:
    """https://www.linkedin.com/in/Slug/?x=1 -> 'in/Slug' (IDs are case-sensitive)."""
    parts = [p for p in urlsplit((url or "").strip()).path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("in", "company"):
        return f"{parts[0]}/{parts[1]}"
    return ""


def _url_for(key: str) -> str:
    return f"https://www.linkedin.com/{key}/"


def _same_name(a: str | None, b: str | None) -> bool:
    norm = lambda s: " ".join((s or "").split(",")[0].lower().split())  # noqa: E731  ("Jane Doe, PhD")
    return bool(norm(a)) and norm(a) == norm(b)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _targets_doc() -> dict:
    doc = _read_json(TARGETS_PATH, {"targets": []})
    if isinstance(doc, list):
        doc = {"targets": doc}
    return doc


def _candidates() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if CANDIDATES_CSV.exists():
        with CANDIDATES_CSV.open(newline="") as f:
            for r in csv.DictReader(f):
                key = account_key(r.get("profile_url") or "")
                if not key:
                    continue
                posts = [u for u in (r.get("posts") or "").split(";") if u]
                out[key] = {
                    "name": r.get("name") or "", "headline": r.get("headline") or "",
                    "company": r.get("company") or "",
                    "interactions": int(r.get("interaction_count") or 0),
                    "interaction_types": [t for t in (r.get("interaction_types") or "").split(";") if t],
                    "interacted_posts": posts[-5:][::-1],
                }
    return out


# ── activity ────────────────────────────────────────────────────────────

def _corpus_activity() -> dict[str, dict]:
    """LinkedIn posts collected in the last ACTIVITY_DAYS, grouped by target key."""
    since = (datetime.now(timezone.utc) - timedelta(days=ACTIVITY_DAYS)).isoformat()
    out: dict[str, dict] = {}
    try:
        con = sqlite3.connect(f"file:{paths.PULSE_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            """SELECT json_extract(engagement_raw, '$.target_url'), published_at, url,
                      substr(body, 1, 400), score, num_comments
               FROM items WHERE source = 'linkedin' AND published_at >= ?
               ORDER BY published_at DESC""", (since,)).fetchall()
        con.close()
    except sqlite3.Error as e:
        logger.warning(f"corpus activity unavailable: {e}")
        return out
    for target_url, published, url, text, score, comments in rows:
        key = account_key(target_url or "")
        if not key:
            continue
        a = out.setdefault(key, {"count": 0, "posts": []})
        a["count"] += 1
        if len(a["posts"]) < 5:
            a["posts"].append({"date": (published or "")[:10], "url": url, "text": text or "",
                               "likes": score or 0, "comments": comments or 0})
    return out


def overview() -> dict:
    with _lock:
        targets = _targets_doc().get("targets", [])
        removed = _read_json(REMOVED_PATH, {})
        previews = _read_json(PREVIEWS_PATH, {})
    cands = _candidates()
    corpus = _corpus_activity()

    accounts: dict[str, dict] = {}
    for key, c in cands.items():
        accounts[key] = {"key": key, "url": _url_for(key), "included": False, **c}
    for key, t in removed.items():
        accounts.setdefault(key, {"key": key, "url": t.get("url") or _url_for(key),
                                  "included": False, "name": t.get("name") or "",
                                  "headline": t.get("note") or "", "interactions": 0})
    for t in targets:
        key = account_key(t.get("url") or "")
        if not key:
            continue
        if key not in accounts:
            # Targets added by hand use the public slug, the interactions list the
            # internal ID, so the same person can arrive under two keys: merge by name.
            twin = next((k for k, c in accounts.items() if not c["included"]
                         and _same_name(c.get("name"), t.get("name"))), None)
            if twin:
                accounts[key] = {**accounts.pop(twin), "key": key}
        a = accounts.setdefault(key, {"key": key, "interactions": 0, "headline": t.get("note") or ""})
        a.update({"url": t["url"], "included": True, "name": t.get("name") or a.get("name") or ""})
        a["company_page"] = key.startswith("company/")

    for key, a in accounts.items():
        a["corpus"] = corpus.get(key, {"count": 0, "posts": []})
        p = previews.get(key)
        if p:
            a["preview"] = p

    return {
        "accounts": sorted(accounts.values(),
                           key=lambda a: (not a["included"], -a.get("interactions", 0), a.get("name", ""))),
        "included": sum(1 for a in accounts.values() if a["included"]),
        "max_targets": MAX_TARGETS,
        "activity_days": ACTIVITY_DAYS,
        "fetch_batch_max": FETCH_BATCH_MAX,
        "cost_per_account_max_usd": round(FETCH_POSTS * COST_PER_POST_USD, 4),
        "unpublished": unpublished(),
    }


# ── include / exclude ───────────────────────────────────────────────────

def set_included(key: str, include: bool) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        doc = _targets_doc()
        targets = doc.get("targets", [])
        idx = next((i for i, t in enumerate(targets) if account_key(t.get("url") or "") == key), None)
        removed = _read_json(REMOVED_PATH, {})
        if include and idx is None:
            if len(targets) >= MAX_TARGETS:
                raise ValueError(f"The collector reads at most {MAX_TARGETS} accounts per run; "
                                 "remove one first or raise LINKEDIN_MAX_TARGETS.")
            c = _candidates().get(key) or {}
            old = removed.pop(key, None) or {}
            name = c.get("name") or old.get("name") or key.split("/", 1)[1]
            note = old.get("note") or f"added on the editor Sources page {today}; {c.get('headline', '')}".rstrip("; ")
            targets.append({"url": old.get("url") or _url_for(key), "name": name, "note": note[:160]})
        elif not include and idx is not None:
            removed[key] = targets.pop(idx)
        doc["targets"] = targets
        _write_json(TARGETS_PATH, doc)
        _write_json(REMOVED_PATH, removed)
    return {"included": len(targets), "unpublished": unpublished()}


# ── previews from Apify ─────────────────────────────────────────────────

def fetch_previews(keys: list[str]) -> dict:
    keys = [k for k in dict.fromkeys(keys) if account_key(_url_for(k)) == k][:FETCH_BATCH_MAX]
    if not keys:
        return {"fetched": 0, "posts": 0}
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        raise RuntimeError("APIFY_API_KEY is not set in ~/.noon_env")
    from collectors.linkedin_apify import _run_actor  # noqa: PLC0415  (pulse/scripts on sys.path)

    with _lock:
        targets = {account_key(t.get("url") or ""): t["url"] for t in _targets_doc().get("targets", [])}
    urls = {k: targets.get(k) or _url_for(k) for k in keys}
    posts = _run_actor(api_key, list(urls.values()), "month", FETCH_POSTS)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    got: dict[str, dict] = {k: {"fetched_at": now, "posts": []} for k in keys}
    for p in posts:
        key = account_key((p.get("query") or {}).get("targetUrl") or "")
        if key not in got:
            continue
        author = p.get("author") or {}
        eng = p.get("engagement") or {}
        got[key]["public_id"] = author.get("publicIdentifier") or author.get("universalName") or ""
        got[key]["info"] = author.get("info") or ""
        got[key]["posts"].append({
            "date": ((p.get("postedAt") or {}).get("date") or "")[:10],
            "url": p.get("linkedinUrl") or "",
            "text": (p.get("content") or "")[:400],
            "likes": int(eng.get("likes") or 0), "comments": int(eng.get("comments") or 0),
        })
    with _lock:
        cache = _read_json(PREVIEWS_PATH, {})
        cache.update(got)
        _write_json(PREVIEWS_PATH, cache)
    logger.info(f"sources: previewed {len(keys)} accounts, {len(posts)} posts "
                f"(~${len(posts) * COST_PER_POST_USD:.2f})")
    return {"fetched": len(keys), "posts": len(posts),
            "cost_usd": round(len(posts) * COST_PER_POST_USD, 3), "previews": got}


# ── publish ─────────────────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, timeout=120)


def _rel() -> str:
    return str(TARGETS_PATH.relative_to(REPO_DIR))


def unpublished() -> bool:
    return _git("diff", "--quiet", "HEAD", "--", _rel()).returncode != 0


def publish() -> dict:
    """Commit only the targets file and push it to origin/main."""
    with _lock:
        if not unpublished():
            return {"ok": True, "message": "Nothing to publish."}
        n = len(_targets_doc().get("targets", []))
        msg = (f"LinkedIn targets: owner's selection on the editor Sources page ({n} accounts)\n\n"
               "The owner chooses which LinkedIn accounts the collector reads; this commit\n"
               "records that choice.")
        c = _git("commit", "-m", msg, "--", _rel())
        if c.returncode != 0:
            raise RuntimeError(f"git commit failed: {c.stderr.strip() or c.stdout.strip()}")
        p = _git("push", "origin", "HEAD:main")
        if p.returncode != 0:
            r = _git("pull", "--rebase", "--autostash", "origin", "main")
            if r.returncode != 0:
                raise RuntimeError(f"git pull --rebase failed: {r.stderr.strip()}")
            p = _git("push", "origin", "HEAD:main")
            if p.returncode != 0:
                raise RuntimeError(f"git push failed: {p.stderr.strip()}")
        sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    logger.info(f"sources: published {n} LinkedIn targets as {sha}")
    return {"ok": True, "message": f"Published {n} accounts ({sha}). The next collection run uses them.",
            "unpublished": False}

