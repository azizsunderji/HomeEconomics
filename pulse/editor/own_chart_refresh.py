"""Bring an old chart of the owner's up to date when a callback is about to show it (owner, 16 Oct 2026).

Owner's rule (16 Oct 2026): "You are putting in charts that are like years old and obviously need to be updated.
In some cases they don't really need to be updated. For example the second chart about us being short of carpenters
and brick masons doesn't change too much. The other two, the one showing outstanding mortgages by interest rate and
also government bond yields, are completely out of date. We need to integrate into the system an updater. It needs to
be clever to figure out whether that chart needs updating or not."

refresh(slug, n) decides and acts, at use time only (no nightly job):

  0. Not needed: the chart is static or unclear, or a time series whose last period is recent enough
     (analysis/own_callbacks.chart_needs_refresh). Status "current", the original is used.
  1. Already done: refreshed (or checked) in the last 7 days. The stored result is returned.
  2. A newer rendering exists: another chart in the corpus, or a PNG in the builder's project outputs/ folder newer
     than the piece, with the same printed title and a later, recent last period (read by the same Sonnet 5.5 call
     that classifies the corpus). It is hosted as it is.
  3. Re-run: a hand-written recipe (OwnPublications/chart_recipes.json: a command that writes the PNG to {out}) or
     else the matched builder. A server builder runs in a scratch copy of its project (scripts plus the data files
     the chain names; absolute paths to the project point at the copy), together with the project scripts that
     produce the files it reads, in order. A Mac builder is copied alone (scp) and run with Mac Dropbox paths mapped
     to the server's copy of Dropbox; it fails when its inputs exist only on the Mac. The new PNG is checked by
     Sonnet 5.5: same title, and a later last period ("refreshed") or the same one after a live data fetch
     ("checked-current": the source has nothing newer). It is hosted at images/own/<slug>/<n>_YYYYMMDD.png.
  4. Otherwise "stale-unrefreshable", with the reason. The callback then goes in without its chart.

The original image is never touched. Every attempt is appended to ~/work/noon/logs/chart_refresh.jsonl; the
result per chart is kept in OwnPublications/chart_refresh_state.json (current_url, refreshed_at, status) and
written into the corpus parquet and JSON. Hard cap: `timeout` seconds per chart (default 90).

    python own_chart_refresh.py SLUG N [--force] [--timeout 90]
    python own_chart_refresh.py --report-week          # the Sunday report line
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import own_chart_meta as meta_mod
from own_chart_meta import (DATA_DIR, MAC_ROOT, SERVER_ROOT, STATE_PATH, chart_record, classify_image, image_path,
                            load_meta, load_state, needs_refresh, norm, save_meta, sha1_file)

logger = logging.getLogger("own_chart_refresh")

RECIPES_PATH = DATA_DIR / "chart_recipes.json"
LOG_PATH = Path(os.environ.get("NOON_LOG_DIR", str(Path.home() / "work" / "noon" / "logs"))) / "chart_refresh.jsonl"
SCRATCH = Path.home() / "work" / "noon" / "chart_refresh_scratch"
PUBLIC_BASE = os.environ.get("NOON_BASE_URL", "https://noon.homeeconomics.us").rstrip("/") + "/images/own"
REUSE_DAYS = 7
DEFAULT_TIMEOUT = 90
OK_STATUSES = ("current", "refreshed", "checked-current")
_NET_RE = re.compile(r"\b(httpx|requests|urllib|urlopen|api\.stlouisfed|fredapi|sdmx|wget|curl|zipfile|download)\b")
_WRITE_HINT = re.compile(r"(to_parquet|to_csv|to_json|json\.dump|write_text|write_bytes|savefig|COPY .* TO|open\([^)]*['\"]w)")
_DATA_NAME_RE = re.compile(r"['\"/]([\w\-. ]+\.(?:json|csv|parquet|tsv|xlsx|txt|gz|zip|pkl|feather))['\"]")


# ── state ──────────────────────────────────────────────────────────────
def _save_state(st: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    tmp.replace(STATE_PATH)


def _log(rec: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_recipes() -> dict:
    """Hand recipes (chart_recipes.json), plus a recipe for every rebuilt builder of a chart that had none
    (chart_replicas.json, status replicated or approved; owner, 16 Oct 2026: "they were not built by script but still
    you can recognize what they are and rebuild them"). A hand recipe wins over a replica."""
    try:
        out = json.loads(RECIPES_PATH.read_text())
    except (OSError, ValueError):
        out = {}
    for key, rp in meta_mod.load_replicas().items():
        if key not in out and rp.get("status") in meta_mod.REPLICA_LIVE and rp.get("builder") and Path(rp["builder"]).exists():
            out[key] = {"cmd": ["python3", rp["builder"], "--out", "{out}"], "timeout": 80, "fetches": True,
                        "note": f"rebuilt builder {Path(rp['builder']).name} (chart replicator, {rp.get('status')})"}
    return out


def _pub(slug: str) -> dict:
    for p in meta_mod.load_pubs_from_parquet():
        if p["slug"] == slug:
            return p
    raise KeyError(slug)


def _title_match(a: dict, b: dict) -> float:
    ta, tb = norm(" ".join(a.get("title_lines") or [])), norm(" ".join(b.get("title_lines") or []))
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def _later(a: str | None, b: str | None) -> bool:
    return bool(a) and (not b or a > b)


def _host(png: Path, slug: str, n: int, today: str) -> str:
    """Copy (at most 1200 px wide) to images/own/<slug>/<n>_YYYYMMDD.png; never overwrites the original."""
    from own_publications import _to_png
    dest_name = f"{n}_{today.replace('-', '')}.png"
    dest = meta_mod.OWN_IMAGES / slug / dest_name
    k = 2
    while dest.exists() and sha1_file(dest) != sha1_file(png):
        dest = meta_mod.OWN_IMAGES / slug / f"{n}_{today.replace('-', '')}_{k}.png"
        k += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(_to_png(png.read_bytes()))
    return f"{PUBLIC_BASE}/{slug}/{dest.name}"


def _anthropic():
    import anthropic
    return anthropic.Anthropic()


def _verify(png: Path, orig: dict, pub: dict, client, cache: dict) -> dict:
    """Sonnet 5.5 reads the new image (cached by sha1). Returns the classification plus title_ratio."""
    h = sha1_file(png)
    rec = cache["classify"].get(h)
    if not rec:
        rec = classify_image(client, png, pub["title"], date.today().isoformat(), "")
        cache["classify"][h] = rec
        save_meta(cache)
    rec = dict(rec)
    rec["title_ratio"] = round(_title_match(rec, orig), 2)
    return rec


# ── step 2: a newer rendering already exists ───────────────────────────
def _corpus_newer(slug: str, n: int, orig: dict, cache: dict, as_of: str) -> dict | None:
    best = None
    for p in meta_mod.load_pubs_from_parquet():
        for c in p.get("charts") or []:
            if p["slug"] == slug and c["n"] == n:
                continue
            rec = chart_record(cache, p["slug"], c["n"])
            if rec.get("chart_kind") != "time-series" or _title_match(rec, orig) < 0.85:
                continue
            if _later(rec.get("last_period_date"), orig.get("last_period_date")) and not needs_refresh(rec, as_of, p["date"]):
                if best is None or rec["last_period_date"] > best[1]["last_period_date"]:
                    best = ((p["slug"], c["n"], c.get("hosted_url")), rec)
    if not best:
        return None
    (s2, n2, url), rec = best
    return {"url": url, "method": f"newer chart in the corpus ({s2} #{n2})", "verify": rec}


def _outputs_newer(builder: str | None, host: str | None, pub: dict, orig: dict, cache: dict, client, as_of: str,
                   deadline: float) -> dict | None:
    if not builder or host != "server":
        return None          # Mac outputs folders are not browsed at use time (one scp per candidate is too slow)
    proj = _project_root(Path(builder))
    out = proj / "outputs"
    if not out.is_dir():
        return None
    after = datetime.fromisoformat(pub["date"]).timestamp() + 86400
    cands = sorted((f for f in out.rglob("*.png") if f.stat().st_mtime > after), key=lambda f: -f.stat().st_mtime)
    for f in cands[:4]:
        if time.time() > deadline - 20:
            break
        rec = _verify(f, orig, pub, client, cache)
        if rec["title_ratio"] >= 0.85 and _later(rec.get("last_period_date"), orig.get("last_period_date")) \
                and not needs_refresh(rec, as_of):
            return {"png": f, "method": f"newer rendering in {out}", "verify": rec}
    return None


# ── step 3: re-run ─────────────────────────────────────────────────────
def _project_root(f: Path) -> Path:
    for p in [f] + list(f.parents):
        if re.match(r"\d{4}_\d{2}_\d{1,2}_", p.name) or (p.parent in (SERVER_ROOT,) and p != SERVER_ROOT):
            return p
    return f.parent.parent if f.parent.name == "scripts" else f.parent


def _data_names(text: str) -> set[str]:
    return {m.group(1).split("/")[-1] for m in _DATA_NAME_RE.finditer(text)}


def _writes(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        if _WRITE_HINT.search(line):
            out |= _data_names(line)
    return out


def builder_chain(builder: Path) -> list[Path]:
    """The builder plus the project scripts that produce the files it reads, recursively, in run order."""
    scripts = sorted(p for p in builder.parent.glob("*.py"))
    texts = {p: p.read_text(errors="ignore") for p in scripts}
    writes = {p: _writes(t) for p, t in texts.items()}
    chain: list[Path] = []
    seen: set[Path] = set()

    def need(s: Path, depth: int = 0):
        if s in seen or depth > 8:
            return
        seen.add(s)
        reads = _data_names(texts.get(s) or s.read_text(errors="ignore")) - writes.get(s, set())
        for f in sorted(reads):
            prods = [p for p in scripts if p != s and f in writes[p] and p.name <= s.name] or \
                    [p for p in scripts if p != s and f in writes[p]]
            if prods:
                need(prods[-1], depth + 1)      # the latest-numbered producer
        chain.append(s)

    need(builder)
    return chain


def _rewrite_paths(text: str, proj: Path, work: Path) -> str:
    for root in (str(proj), str(proj).replace(str(SERVER_ROOT), "/mnt/hedata/Dropbox/Home Economics"),
                 f"{MAC_ROOT}/{proj.name}"):
        text = text.replace(root, str(work))
    return text.replace(MAC_ROOT, str(SERVER_ROOT))


def _run_recipe(recipe: dict, work: Path, deadline: float) -> tuple[Path | None, str, bool]:
    out = work / "outputs" / "recipe.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [c.replace("{out}", str(out)).replace("{scratch}", str(work)) for c in recipe["cmd"]]
    left = max(5, int(deadline - time.time() - 15))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=min(left, recipe.get("timeout", left)))
    except subprocess.TimeoutExpired:
        return None, f"recipe timed out after {left}s", False
    if r.returncode or not out.exists():
        return None, f"recipe failed: {(r.stderr or r.stdout)[-300:]}", False
    return out, (r.stdout.strip().splitlines() or [""])[-1][:600], bool(recipe.get("fetches", True))


def _run_builder(builder: Path, host: str, work: Path, deadline: float) -> tuple[Path | None, str, bool]:
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if host == "mac":
        local = work / "scripts" / builder.name
        local.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["scp", "-q", f"mac:{builder}", str(local)], capture_output=True, text=True, timeout=40)
        if r.returncode:
            return None, f"could not copy the Mac builder: {r.stderr[-200:]}", False
        proj = Path(MAC_ROOT) / builder.relative_to(MAC_ROOT).parts[0]
        chain = [local]
    else:
        proj = _project_root(builder)
        chain_src = builder_chain(builder)
        shutil.copytree(builder.parent, work / "scripts", dirs_exist_ok=True)
        names = set()
        for s in chain_src:
            names |= _data_names(s.read_text(errors="ignore"))
        if (proj / "data").is_dir():
            for f in (proj / "data").rglob("*"):
                if f.is_file() and f.name in names and f.stat().st_size < 500_000_000:
                    dest = work / "data" / f.relative_to(proj / "data")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
        chain = [work / "scripts" / s.name for s in chain_src]
    (work / "outputs").mkdir(exist_ok=True)
    (work / "data").mkdir(exist_ok=True)
    fetched = False
    for s in chain:
        if s.suffix == ".ipynb":
            return None, f"{s.name} is a notebook; notebooks are not re-run automatically", False
        t = _rewrite_paths(s.read_text(errors="ignore"), proj, work)
        s.write_text(t)
        fetched = fetched or bool(_NET_RE.search(t))
    note = []
    for s in chain:
        left = int(deadline - time.time() - 15)
        if left < 5:
            return None, f"out of time before {s.name}", False
        try:
            r = subprocess.run(["python3", str(s)], cwd=work, capture_output=True, text=True, timeout=left)
        except subprocess.TimeoutExpired:
            return None, f"{s.name} timed out", False
        if r.returncode:
            return None, f"{s.name} failed: {(r.stderr or r.stdout).strip()[-300:]}", False
        note.append(s.name)
    pngs = sorted((f for f in work.rglob("*.png") if f.stat().st_mtime >= started), key=lambda f: -f.stat().st_mtime)
    if not pngs:
        return None, f"ran {', '.join(note)} but no PNG was written", fetched
    return pngs[0], "ran " + ", ".join(note), fetched


# ── main entry ─────────────────────────────────────────────────────────
def refresh(slug: str, n: int, as_of: str | None = None, timeout: int = DEFAULT_TIMEOUT, force: bool = False) -> dict:
    t0 = time.time()
    deadline = t0 + timeout
    as_of = as_of or date.today().isoformat()
    key = f"{slug}/{n}"
    cache = load_meta()
    orig = chart_record(cache, slug, n)
    orig_url = f"{PUBLIC_BASE}/{slug}/{n}.png"
    state = load_state()
    res: dict = {"key": key, "as_of": as_of, "chart_kind": orig.get("chart_kind"),
                 "orig_last_period": orig.get("last_period")}

    def finish(status: str, url: str | None, reason: str, **kw) -> dict:
        res.update({"status": status, "current_url": url, "reason": reason, "seconds": round(time.time() - t0, 1), **kw})
        if status not in ("current",):
            st = load_state()
            prev = st.get(key) or {}
            rec = {"status": status, "current_url": url if status in OK_STATUSES else prev.get("current_url"),
                   "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if status in ("refreshed", "checked-current") else prev.get("refreshed_at"),
                   "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason,
                   "method": kw.get("method"), "last_period": kw.get("last_period"),
                   "last_period_date": kw.get("last_period_date"), "caption": kw.get("caption")}
            st[key] = rec
            _save_state(st)
        _log(res | {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return res

    if not orig:
        return finish("stale-unrefreshable", None, "chart not classified")
    pub = _pub(slug)
    if not force and not needs_refresh(orig, as_of, pub["date"]):
        return finish("current", orig_url, f"{orig.get('chart_kind')}; last period {orig.get('last_period')}")
    prev = state.get(key) or {}
    if not force and prev.get("status") in ("refreshed", "checked-current") and prev.get("refreshed_at"):
        age = datetime.now(timezone.utc) - datetime.fromisoformat(prev["refreshed_at"])
        if age < timedelta(days=REUSE_DAYS):
            res.update({"status": prev["status"], "current_url": prev["current_url"], "reason": "reused (refreshed "
                        f"{age.days} days ago)", "caption": prev.get("caption"), "last_period": prev.get("last_period"),
                        "seconds": round(time.time() - t0, 1)})
            _log(res | {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            return res
    client = _anthropic()

    hit = _corpus_newer(slug, n, orig, cache, as_of)
    if hit:
        v = hit["verify"]
        return finish("refreshed", hit["url"], hit["method"], method=hit["method"], last_period=v.get("last_period"),
                      last_period_date=v.get("last_period_date"), caption=v.get("description"))
    b = cache["builders"].get(key) or {}
    hit = _outputs_newer(b.get("path"), b.get("host"), pub, orig, cache, client, as_of, deadline)
    if hit:
        v = hit["verify"]
        url = _host(hit["png"], slug, n, as_of)
        return finish("refreshed", url, hit["method"], method=hit["method"], last_period=v.get("last_period"),
                      last_period_date=v.get("last_period_date"), caption=v.get("description"))

    recipe = load_recipes().get(key)
    if not recipe and (meta_mod.load_replicas().get(key) or {}).get("status") == "needs-review":
        return finish("stale-unrefreshable", None, "a rebuilt builder exists but awaits the owner's approval "
                      "(chart_replicas.json status needs-review)")
    if not recipe and not b.get("path"):
        return finish("stale-unrefreshable", None, f"no builder found for a time series ending {orig.get('last_period')}")
    if not recipe and abs(float(b.get("lag_days") or 0)) > 45:
        return finish("stale-unrefreshable", None, f"builder {b['path']} is dated {b.get('lag_days')} days from the "
                      "piece; not re-run automatically (no newer rendering in its outputs/)")
    work = SCRATCH / f"{slug}_{n}_{datetime.now():%Y%m%d_%H%M%S}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        if recipe:
            png, note, fetched = _run_recipe(recipe, work, deadline)
            method = f"recipe: {recipe.get('note') or ' '.join(recipe['cmd'])[:120]}"
        else:
            png, note, fetched = _run_builder(Path(b["path"]), b.get("host") or "server", work, deadline)
            method = f"re-ran builder {b['path']}"
    except Exception as e:  # noqa: BLE001
        png, note, fetched, method = None, f"{type(e).__name__}: {e}"[:300], False, "re-run"
    if png is None:
        return finish("stale-unrefreshable", None, note, method=method)
    if time.time() > deadline - 8:
        return finish("stale-unrefreshable", None, f"re-run finished too late to verify ({note})", method=method)
    v = _verify(png, orig, pub, client, cache)
    if v["title_ratio"] < 0.6:
        return finish("stale-unrefreshable", None, f"the re-run drew a different chart (title match {v['title_ratio']}): "
                      f"{' '.join(v.get('title_lines') or [])[:80]}", method=method)
    if _later(v.get("last_period_date"), orig.get("last_period_date")):
        url = _host(png, slug, n, as_of)
        return finish("refreshed", url, note, method=method, last_period=v.get("last_period"),
                      last_period_date=v.get("last_period_date"), caption=v.get("description"))
    if fetched and v.get("last_period_date") == orig.get("last_period_date"):
        return finish("checked-current", orig_url, f"re-run with a live fetch found nothing newer than "
                      f"{orig.get('last_period')} ({note})", method=method, last_period=orig.get("last_period"),
                      last_period_date=orig.get("last_period_date"), caption=None)
    return finish("stale-unrefreshable", None, f"re-run did not extend the data (last period {v.get('last_period')}; "
                  f"fetch step: {fetched}; {note})", method=method)


def refresh_and_record(slug: str, n: int, **kw) -> dict:
    """refresh() and then rewrite the corpus parquet and JSON with the new state (best effort)."""
    res = refresh(slug, n, **kw)
    if res.get("status") not in ("current",) and not str(res.get("reason", "")).startswith("reused"):
        try:
            meta_mod.apply_to_outputs()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"corpus outputs not rewritten: {e}")
    return res


def week_report(days: int = 7) -> str:
    """One line for the Sunday own-publications log: time-series charts refreshed this week, and failures."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    done, failed = {}, {}
    try:
        lines = LOG_PATH.read_text().splitlines()
    except OSError:
        lines = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if datetime.fromisoformat(r.get("at", "1970-01-01T00:00:00+00:00")) < since:
            continue
        if r.get("status") in ("refreshed", "checked-current") and not str(r.get("reason", "")).startswith("reused"):
            done[r["key"]] = f"{r['key']} ({r.get('orig_last_period')} -> {r.get('last_period') or 'same'})"
            failed.pop(r["key"], None)
        elif r.get("status") == "stale-unrefreshable":
            failed[r["key"]] = f"{r['key']} ({str(r.get('reason'))[:90]})"
    return (f"chart refresh, last {days} days: {len(done)} time-series chart(s) refreshed"
            + (": " + "; ".join(done.values()) if done else "")
            + f". {len(failed)} failed" + (": " + "; ".join(failed.values()) if failed else "") + ".")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("--as-of")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--force", action="store_true", help="refresh even if not stale or refreshed recently")
    ap.add_argument("--report-week", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.report_week:
        print(week_report())
        return
    print(json.dumps(refresh_and_record(a.slug, a.n, as_of=a.as_of, timeout=a.timeout, force=a.force),
                     indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
