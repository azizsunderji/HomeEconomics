"""What each chart in the owner's own publications is, and where its builder lives (owner, 16 Oct 2026).

Owner's rule (16 Oct 2026): "You are putting in charts that are like years old and obviously need to be
updated. In some cases they don't really need to be updated. For example the second chart about us being
short of carpenters and brick masons doesn't change too much. The other two, the one showing outstanding
mortgages by interest rate and also government bond yields, are completely out of date. We need to
integrate into the system an updater. It needs to be clever to figure out whether that chart needs
updating or not."

Two jobs, both cached in OwnPublications/chart_meta.json so re-runs cost nothing:

  classify   One Sonnet 5.5 call per chart image (keyed by the PNG's sha1) with the piece's title, date and
             caption. Returns chart_kind (static | time-series | unclear), the chart's printed title and
             distinctive labels, the data sources named on the image, the last period on its x-axis (and that
             period's end date), how often the underlying data is published, and a one-line description.
  builders   For time-series charts: the script that drew it. A chart matches a script when its printed title
             (or two or more distinctive labels) appears in the script text; ties go to the script whose
             modification date is closest to (and not long after) the publication date. Server projects
             (/home/aziz/OVH/YYYY_MM_DD_*) are searched by reading the files; the owner's Mac
             (~/Dropbox/Home Economics, older projects) by Spotlight (mdfind) over ssh, one session.

`needs_refresh(chart, as_of)` is the staleness rule used by the callback stage and the updater.

    python own_chart_meta.py classify            # classify every hosted chart (cached)
    python own_chart_meta.py builders [--no-mac] # match time-series charts to builders (cached)
    python own_chart_meta.py apply               # write the fields into the parquet and the JSON
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("own_chart_meta")

DATA_DIR = Path(os.environ.get("OWN_PUBS_DIR", "/home/aziz/OVH/Data/OwnPublications"))
META_PATH = DATA_DIR / "chart_meta.json"
STATE_PATH = DATA_DIR / "chart_refresh_state.json"     # written by own_chart_refresh.py
IMAGES_ROOT = Path(os.environ.get("NOON_IMAGES_DIR", str(Path.home() / "work" / "noon" / "images")))
OWN_IMAGES = IMAGES_ROOT / "own"
MODEL = os.environ.get("OWN_CHART_META_MODEL", "claude-sonnet-5-5")
PROMPT_VERSION = "chartmeta-v1-2026-10-16"
SERVER_ROOT = Path("/home/aziz/OVH")                    # = /mnt/hedata/Dropbox/Home Economics (bind mount)
MAC_ROOT = "/Users/azizsunderji/Dropbox/Home Economics"
CODE_EXT = (".py", ".ipynb", ".js", ".R", ".r", ".jsx", ".ts")

# The staleness rule (3 months; 30 days daily/weekly, 60 days monthly, 14 months annual) lives in
# pulse/scripts/analysis/own_callbacks.py so the GitHub runner and this server use one copy.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analysis.own_callbacks import chart_max_age_days as max_age_days, chart_needs_refresh as needs_refresh  # noqa: E402

_LOCK = threading.Lock()
_USAGE = {"in": 0, "out": 0, "calls": 0}


# ── cache ──────────────────────────────────────────────────────────────
def load_meta() -> dict:
    try:
        m = json.loads(META_PATH.read_text())
    except (OSError, ValueError):
        m = {}
    m.setdefault("classify", {})
    m.setdefault("builders", {})
    return m


def save_meta(m: dict) -> None:
    tmp = META_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False))
    tmp.replace(META_PATH)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def image_path(slug: str, n: int) -> Path:
    return OWN_IMAGES / slug / f"{n}.png"


def sha1_file(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


# ── classification ─────────────────────────────────────────────────────
CLASSIFY_PROMPT = """This chart is from Home Economics, Aziz Sunderji's newsletter on US housing economics. His daily briefing now and then shows an old chart again, next to today's news, and must not show a chart whose data has gone out of date.

Piece: "{title}" (published {date})
Caption written for the chart: {caption}

Reply with JSON only:
{{"chart_kind": "static" | "time-series" | "unclear",
  "reason": "<one short sentence>",
  "title_lines": ["<the chart's printed title, one string per printed line, exactly as printed>"],
  "labels": ["<up to 6 distinctive strings printed on the chart, exactly as printed: axis titles, legend entries, annotations, row names; not bare numbers or years>"],
  "sources": ["<each data source named on the image, e.g. FRED, Zillow, Freddie Mac, Census ACS, BLS, FHFA, Redfin; [] if none is printed>"],
  "last_period": "<the last period the data covers, as shown on the x-axis or stated in the title/notes, e.g. 2026-08, 2022 Q4, 2023; null if the chart has no time dimension>",
  "last_period_date": "<YYYY-MM-DD, the END of that period, e.g. 2022-12-31 for 2022 Q4; null if none>",
  "update_frequency": "daily" | "weekly" | "monthly" | "quarterly" | "annual" | "irregular" | "none",
  "description": "<one line, at most 25 words: what the chart shows>"}}

chart_kind:
- "time-series": the data runs over time up to the latest reading available when the piece was written (statistics are published with lags of one to nine months), so newer data would change what the chart shows: a line, area or bar chart over time ending near the publication date, or a ranking/map of the latest reading of a regularly updated indicator (yields by country now, rents by metro this month).
- "static": a cross-section or structural fact that does not go out of date quickly: occupational or demographic shares, a one-off survey, a census-based snapshot, a model, schematic or calculation, a historical episode that deliberately ends long before the piece, or anything where a newer edition would barely change the picture.
- "unclear": you cannot tell.
update_frequency is how often the underlying source publishes new data ("none" for static charts that have no recurring source)."""


def _model_kwargs(model: str) -> dict:
    if model.startswith("claude-sonnet-5-5"):     # rejects thinking "disabled"; lowest setting
        return {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
    if model.startswith(("claude-sonnet-5", "claude-opus-5")):
        return {"thinking": {"type": "disabled"}}
    return {}


def _jpeg_b64(png_path: Path, max_w: int = 1000) -> str:
    from PIL import Image
    im = Image.open(png_path)
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def classify_image(client, png_path: Path, title: str, pub_date: str, caption: str, model: str = MODEL) -> dict:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(png_path)}},
               {"type": "text", "text": CLASSIFY_PROMPT.format(title=title, date=pub_date, caption=caption or "(none)")}]
    resp = client.messages.create(model=model, max_tokens=2000, messages=[{"role": "user", "content": content}],
                                  **_model_kwargs(model))
    with _LOCK:
        _USAGE["in"] += resp.usage.input_tokens
        _USAGE["out"] += resp.usage.output_tokens
        _USAGE["calls"] += 1
    text = "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")
    d = _parse_json(text)
    kind = d.get("chart_kind") if d.get("chart_kind") in ("static", "time-series", "unclear") else "unclear"
    lpd = d.get("last_period_date")
    if not (isinstance(lpd, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", lpd)):
        lpd = None
    freq = d.get("update_frequency")
    if freq not in ("daily", "weekly", "monthly", "quarterly", "annual", "irregular", "none"):
        freq = "irregular"
    as_list = lambda v: [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []  # noqa: E731
    return {"chart_kind": kind, "reason": str(d.get("reason") or "")[:300],
            "title_lines": as_list(d.get("title_lines"))[:4], "labels": as_list(d.get("labels"))[:6],
            "sources": as_list(d.get("sources"))[:6],
            "last_period": (str(d["last_period"]).strip() if d.get("last_period") not in (None, "", "null") else None),
            "last_period_date": lpd, "update_frequency": freq,
            "description": str(d.get("description") or "")[:240], "model": model, "prompt": PROMPT_VERSION}


def _usd() -> float:
    return (_USAGE["in"] * 2 + _USAGE["out"] * 10) / 1e6      # Sonnet 5.5: $2 in / $10 out per million


def iter_charts(pubs: list[dict]):
    for p in pubs:
        for c in p.get("charts") or []:
            yield p, c


def classify_all(pubs: list[dict], workers: int = 6, model: str = MODEL) -> dict:
    """Classify every hosted chart not yet in the cache. Returns the cache."""
    import anthropic
    client = anthropic.Anthropic()
    meta = load_meta()
    todo = []
    for p, c in iter_charts(pubs):
        f = image_path(p["slug"], c["n"])
        if not f.exists():
            continue
        h = sha1_file(f)
        rec = meta["classify"].get(h)
        if rec and rec.get("prompt") == PROMPT_VERSION:
            continue
        todo.append((p, c, f, h))
    logger.info(f"classify: {len(todo)} charts to classify ({model})")

    def one(t):
        p, c, f, h = t
        for attempt in range(3):
            try:
                return h, classify_image(client, f, p["title"], p["date"], c.get("caption") or "", model)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"classify {p['slug']}/{c['n']} attempt {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        return h, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for h, rec in ex.map(one, todo):
            if rec:
                meta["classify"][h] = rec
            done += 1
            if done % 25 == 0:
                save_meta(meta)
                logger.info(f"  {done}/{len(todo)} classified, about ${_usd():.2f}")
    save_meta(meta)
    logger.info(f"classify: {done} done; tokens in {_USAGE['in']:,} out {_USAGE['out']:,}; about ${_usd():.2f}")
    return meta


def chart_record(meta: dict, slug: str, n: int) -> dict:
    """Classification + builder for one hosted chart ({} when unknown)."""
    f = image_path(slug, n)
    if not f.exists():
        return {}
    rec = dict(meta["classify"].get(sha1_file(f)) or {})
    b = meta["builders"].get(f"{slug}/{n}")
    if b:
        rec["builder_path"] = b.get("path")
        rec["builder_host"] = b.get("host")
    return rec


# ── builder search ─────────────────────────────────────────────────────
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " "})


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_QUOTES).lower()).strip()


def probes_for(rec: dict) -> tuple[list[str], list[str]]:
    """(title probes, label probes): normalized strings worth searching for."""
    lines = [norm(t) for t in rec.get("title_lines") or [] if len(norm(t)) >= 10]
    full = norm(" ".join(rec.get("title_lines") or []))
    title = list(dict.fromkeys(([full] if len(full) >= 10 else []) + lines))
    labels = []
    for t in rec.get("labels") or []:
        t = norm(t)
        if len(t) >= 9 and not re.fullmatch(r"[\d\s.,%$'\-–()/]+", t) and not t.startswith("source"):
            labels.append(t)
    return title, list(dict.fromkeys(labels))


# projects that hold copies of the corpus text, or this updater's own rebuilds: never a piece's original builder
EXCLUDE_PROJECTS = {"2026_10_16_ChartRefresh", "2026_09_17_HousingCorpusBot", "NewsAtNoon"}


def server_projects() -> list[Path]:
    return sorted(p for p in SERVER_ROOT.glob("[0-9][0-9][0-9][0-9]_*") if p.is_dir() and p.name not in EXCLUDE_PROJECTS)


def build_server_index() -> list[tuple[str, float, str]]:
    """[(path, mtime, normalized text)] for every code file in the server's dated projects."""
    out = []
    for proj in server_projects():
        for f in proj.rglob("*"):
            if f.suffix not in CODE_EXT or not f.is_file() or "node_modules" in f.parts or ".git" in f.parts:
                continue
            try:
                if f.stat().st_size > 3_000_000:
                    continue
                txt = f.read_text(errors="ignore")
            except OSError:
                continue
            if f.suffix == ".ipynb":
                try:
                    txt = "\n".join("".join(c.get("source") or []) for c in json.loads(txt).get("cells") or [])
                except ValueError:
                    pass
            t = norm(txt.replace("\\n", " "))
            # labels split over list items or adjacent literals ("Post-Volcker","disinflation") also match
            t = t + " \x00 " + re.sub(r"['\"]\s*[,+]?\s*['\"]", " ", t)
            out.append((str(f), f.stat().st_mtime, t))
    logger.info(f"server index: {len(out)} code files in {len(server_projects())} projects")
    return out


MAC_HELPER = r'''
import json, subprocess, sys, os
root = sys.argv[1]
exts = tuple(sys.argv[2].split(","))
from concurrent.futures import ThreadPoolExecutor
probes = json.load(sys.stdin)
def one(p):
    q = '"' + p.replace('"', ' ').replace("\\", " ") + '"cd'
    try:
        r = subprocess.run(["mdfind", "-onlyin", root, q], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return p, None
    hits = []
    for line in r.splitlines():
        if line.endswith(exts) and "/." not in line:
            try:
                hits.append([line, os.stat(line).st_mtime])
            except OSError:
                pass
    return p, hits[:40]
with ThreadPoolExecutor(8) as ex:
    out = {p: h for p, h in ex.map(one, probes) if h is not None}
print(json.dumps(out))
'''


def mac_search(probes: list[str]) -> dict[str, list]:
    """Spotlight search on the owner's Mac for each probe phrase; {probe: [[path, mtime], ...]}."""
    if not probes:
        return {}
    helper = DATA_DIR / "_mac_mdfind_helper.py"
    helper.write_text(MAC_HELPER)
    try:
        subprocess.run(["scp", "-q", str(helper), "mac:/tmp/_he_mdfind_helper.py"], check=True, timeout=60)
        r = subprocess.run(["ssh", "mac", f"python3 /tmp/_he_mdfind_helper.py {shlex.quote(MAC_ROOT)} "
                            f"{','.join(CODE_EXT)}"], input=json.dumps(probes), capture_output=True, text=True,
                           timeout=120 + len(probes))
        return json.loads(r.stdout or "{}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"mac search failed: {e}")
        return {}


def _score(path_hits: dict[str, dict], pub_date: str) -> dict | None:
    """Pick the best builder among {path: {"mtime", "title", "labels", "host"}}."""
    if not path_hits:
        return None
    pd_ts = datetime.fromisoformat(pub_date).replace(tzinfo=timezone.utc).timestamp()
    best = None
    for path, h in path_hits.items():
        lag_days = (h["mtime"] - pd_ts) / 86400
        if not h["title"] and not (h["labels"] >= 2 and -120 <= lag_days <= 7):
            continue
        if h["title"] and not h["labels"] and abs(lag_days) > 45:
            continue
        prox = 0 if -2 <= lag_days <= 2 else abs(lag_days)
        s = 3 * h["title"] + h["labels"] - min(prox, 400) / 50 + max(min(lag_days, 2), -2) / 100
        if re.search(r"(render|chart|plot|build|draw|viz|fig)", Path(path).name, re.I):
            s += 0.5
        if best is None or s > best["score"]:
            best = {"path": path, "host": h["host"], "score": round(s, 3), "title_hits": h["title"],
                    "label_hits": h["labels"], "mtime": datetime.fromtimestamp(h["mtime"], timezone.utc).date().isoformat(),
                    "lag_days": round(lag_days, 1)}
    return best


def match_builders(pubs: list[dict], use_mac: bool = True, kinds=("time-series", "unclear"),
                   force: bool = False) -> dict:
    meta = load_meta()
    targets = []
    for p, c in iter_charts(pubs):
        key = f"{p['slug']}/{c['n']}"
        rec = chart_record(meta, p["slug"], c["n"])
        if rec.get("chart_kind") not in kinds:
            continue
        if not force and key in meta["builders"]:
            continue
        targets.append((p, c, key, rec))
    logger.info(f"builders: {len(targets)} charts to match")
    if not targets:
        return meta
    index = build_server_index()
    probes_all: set[str] = set()
    per = {}
    for p, c, key, rec in targets:
        tp, lp = probes_for(rec)
        per[key] = (p, tp, lp)
        probes_all.update(tp + lp)
    mac = {}
    if use_mac:
        # Mac only for pieces before the server's projects begin, or whose title is not on the server
        mac_probes = []
        for key, (p, tp, lp) in per.items():
            on_server = any(t in txt for t in tp for _, _, txt in index) if tp else False
            if not on_server:
                mac_probes.extend(tp + lp)
        mac_probes = sorted(set(mac_probes))
        logger.info(f"builders: {len(mac_probes)} phrases to search on the Mac")
        cached = meta.setdefault("mac_search", {})
        todo = [x for x in mac_probes if x not in cached]
        logger.info(f"builders: {len(todo)} of them not searched before")
        for i in range(0, len(todo), 200):
            got = mac_search(todo[i:i + 200])
            cached.update(got)
            save_meta(meta)
            logger.info(f"  mac: {min(i + 200, len(todo))}/{len(todo)} phrases searched")
        mac = {x: cached.get(x) or [] for x in mac_probes}
    for key, (p, tp, lp) in per.items():
        hits: dict[str, dict] = {}
        for path, mtime, txt in index:
            t = sum(1 for x in tp if x in txt)
            lab = sum(1 for x in lp if x in txt)
            if t or lab:
                hits[path] = {"mtime": mtime, "title": min(t, 1), "labels": lab, "host": "server"}
        for x in tp + lp:
            for path, mtime in mac.get(x, []):
                if any(f"/{e}/" in path for e in EXCLUDE_PROJECTS):
                    continue
                h = hits.setdefault(path, {"mtime": mtime, "title": 0, "labels": 0, "host": "mac"})
                if x in tp:
                    h["title"] = 1
                else:
                    h["labels"] += 1
        best = _score(hits, p["date"])
        meta["builders"][key] = best or {"path": None, "host": None,
                                         "probes": (tp + lp)[:6], "checked_at": date.today().isoformat()}
    save_meta(meta)
    return meta


# ── writing the fields into the corpus outputs ─────────────────────────
FIELDS = ("chart_kind", "last_period", "last_period_date", "update_frequency", "sources", "description",
          "builder_path", "current_url", "refreshed_at", "refresh_status")


def annotate(pubs: list[dict]) -> list[dict]:
    """Adds FIELDS to every chart dict in place (from the cache and the refresh state)."""
    meta = load_meta()
    state = load_state()
    for p, c in iter_charts(pubs):
        rec = chart_record(meta, p["slug"], c["n"])
        st = state.get(f"{p['slug']}/{c['n']}") or {}
        c["chart_kind"] = rec.get("chart_kind")
        c["last_period"] = rec.get("last_period")
        c["last_period_date"] = rec.get("last_period_date")
        c["update_frequency"] = rec.get("update_frequency")
        c["sources"] = rec.get("sources") or []
        c["description"] = rec.get("description")
        c["builder_path"] = rec.get("builder_path")
        c["current_url"] = st.get("current_url")
        c["refreshed_at"] = st.get("refreshed_at")
        c["refresh_status"] = st.get("status")
    return pubs


def load_pubs_from_parquet() -> list[dict]:
    import duckdb
    from own_publications import PARQUET
    con = duckdb.connect()
    cols = [d[0] for d in con.execute(f"SELECT * FROM '{PARQUET}' LIMIT 0").description]
    rows = con.execute(f"SELECT * FROM '{PARQUET}' ORDER BY date DESC").fetchall()
    out = []
    for r in rows:
        p = dict(zip(cols, r))
        p["date"] = str(p["date"])[:10]
        p["charts"] = [dict(c) for c in (p.get("charts") or [])]
        out.append(p)
    return out


def apply_to_outputs() -> None:
    """Rewrite the parquet and the JSON with the current classification, builders and refresh state."""
    from own_publications import write_outputs
    pubs = annotate(load_pubs_from_parquet())
    write_outputs(pubs)


def summary(pubs: list[dict]) -> dict:
    meta = load_meta()
    kinds: dict[str, int] = {}
    ts = ts_b = 0
    for p, c in iter_charts(pubs):
        rec = chart_record(meta, p["slug"], c["n"])
        k = rec.get("chart_kind") or "unclassified"
        kinds[k] = kinds.get(k, 0) + 1
        if k == "time-series":
            ts += 1
            ts_b += bool(rec.get("builder_path"))
    return {"kinds": kinds, "time_series": ts, "time_series_with_builder": ts_b}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["classify", "builders", "apply", "summary"])
    ap.add_argument("--no-mac", action="store_true")
    ap.add_argument("--force", action="store_true", help="builders: re-match charts already matched")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pubs = load_pubs_from_parquet()
    if a.cmd == "classify":
        classify_all(pubs, a.workers)
    elif a.cmd == "builders":
        match_builders(pubs, use_mac=not a.no_mac, force=a.force)
    elif a.cmd == "apply":
        apply_to_outputs()
    print(json.dumps(summary(pubs), indent=1))


if __name__ == "__main__":
    main()
