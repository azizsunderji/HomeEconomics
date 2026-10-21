"""Fresh charts for scheduled data releases (owner, 21 Oct 2026).

Owner's rule (21 Oct 2026): "One thing we should do is have you be able to generate new charts. For example pending home
sales, that's easily generatable as a chart. I would want to see, for example, the number, the rolling average, and
maybe a breakdown by region or whatever. That kind of thinking applies to all new data." Cover every release
Calculated Risk covers, the monthly Zillow release with a map, nonfarm payrolls, Apartment List rents and Cotality
home prices. A theme about a data release gets a chart of the fresh data, not an old callback chart.

How it works
  REGISTRY   one Release per scheduled release: key, name, trigger regex (matched against an entry's `trigger`
             and `title`), source, series, builder, intro template. A Release with builder=None is recognised
             but not built yet (wave 2); apply() logs "no-builder" for it.
  build()    build(key, as_of) -> {png_path, latest_period, title, caption, source_line, url} or None when the
             newest period is not newer than the period already hosted (release_chart_state.json). Data comes from
             the FRED data lake (/home/aziz/OVH/Data/FRED/<SERIES>.parquet); each series is refreshed from the
             FRED API when FRED's last_updated differs from the sidecar's (revisions included), and written back
             in parquet (ZSTD, sorted by date, ROW_GROUP_SIZE 100000) with a .source.json sidecar.
  hosting    ~/work/noon/images/releases/<key>/<period compact>_<YYYYMMDD>.png, public at
             https://noon.homeeconomics.us/images/releases/... Never overwritten (a _2 suffix is added).
  state      /home/aziz/OVH/Data/OwnPublications/release_chart_state.json: per key the last period charted,
             url, built_at, draft_date. A rerun for the same draft date reuses the hosted chart.
  log        ~/work/noon/logs/release_charts.jsonl, one line per attempt.
  apply()    apply(draft_obj, date): for each release, the highest entry whose title matches (else whose trigger
             matches) gets, appended to its summary, "\\n\\n<intro>:\\n\\n![alt](url)". One release chart per
             entry, one entry per release. Skipped when the trigger names months and none is the data's latest
             month (FRED not updated yet). Called by ingest.ingest() on every new draft.

CLI
    python release_charts.py --date 2026-10-21 [--dry] [--force KEY] [--out-dir DIR]
    --dry prints which entries would get which chart and writes the PNGs to --out-dir (default: the
    2026_10_21_ReleaseCharts project outputs folder); nothing is hosted, recorded or saved.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("noon.release_charts")

LAKE = Path(os.environ.get("NOON_FRED_LAKE", "/home/aziz/OVH/Data/FRED"))
STATE_PATH = Path(os.environ.get("NOON_RELEASE_STATE", "/home/aziz/OVH/Data/OwnPublications/release_chart_state.json"))
IMAGES = Path(os.environ.get("NOON_IMAGES_DIR", str(Path.home() / "work" / "noon" / "images"))) / "releases"
LOG_PATH = Path(os.environ.get("NOON_LOG_DIR", str(Path.home() / "work" / "noon" / "logs"))) / "release_charts.jsonl"
PUBLIC_BASE = os.environ.get("NOON_BASE_URL", "https://noon.homeeconomics.us").rstrip("/") + "/images/releases"
DRY_DIR = Path("/home/aziz/OVH/2026_10_21_ReleaseCharts/outputs")
FONT_DIR = "/home/aziz/.local/share/fonts"
LOGO = "/home/aziz/OVH/Brand assets/HomeEconomics-Logo-versions/HE-Large-Black.png"
SCALE = 1.25                      # 960 px canvas -> 1200 px PNG (the email's retina width)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
MON3 = [m[:3] for m in MONTHS]
QNAMES = ["first", "second", "third", "fourth"]


# ═══════════════════════════════════════════════════════════════════════
# data: FRED through the data lake
# ═══════════════════════════════════════════════════════════════════════
def _fred_key() -> str:
    k = os.environ.get("FRED_API_KEY")
    if k:
        return k
    try:
        m = re.search(r"FRED API key:\s*([0-9a-f]{32})", Path("/home/aziz/OVH/CLAUDE.md").read_text())
        if m:
            return m.group(1)
    except OSError:
        pass
    raise RuntimeError("no FRED API key (FRED_API_KEY or /home/aziz/OVH/CLAUDE.md)")


def _fred_get(path: str, **params) -> dict:
    params = dict(params, api_key=_fred_key(), file_type="json")
    url = f"https://api.stlouisfed.org/fred/{path}?{urlencode(params)}"
    last = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (HomeEconomics release charts)"}),
                         timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"FRED {path} {params.get('series_id')}: {last}")


_META: dict[str, dict] = {}
_SERIES: dict[str, "object"] = {}


def fred(sid: str):
    """The full history of one FRED series as a float pandas Series indexed by Timestamp (lake first)."""
    import duckdb
    import pandas as pd
    if sid in _SERIES:
        return _SERIES[sid]
    f = LAKE / f"{sid}.parquet"
    side = LAKE / f"{sid}.parquet.source.json"
    try:
        sc = json.loads(side.read_text())
    except (OSError, ValueError):
        sc = {}
    con = duckdb.connect()
    col = None
    if f.exists():
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM '{f}'").fetchall()]
        col = next((c for c in cols if c != "date"), None)
    try:
        meta = _META.get(sid) or _fred_get("series", series_id=sid)["seriess"][0]
        _META[sid] = meta
    except Exception as e:  # noqa: BLE001
        if not f.exists():
            raise
        logger.warning(f"{sid}: FRED unreachable ({e}); using the lake copy as it is")
        meta = None
    if meta and (not f.exists() or sc.get("last_updated") != meta["last_updated"]):
        obs = _fred_get("series/observations", series_id=sid)["observations"]
        rows = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
        df = pd.DataFrame(rows, columns=["date", "value"])
        df["date"] = pd.to_datetime(df["date"])
        col = col or "value"
        tmp = f.with_suffix(".tmp.parquet")
        con.register("fred_df", df)
        con.execute(f"COPY (SELECT CAST(date AS TIMESTAMP) AS date, CAST(value AS DOUBLE) AS \"{col}\" FROM fred_df "
                    f"ORDER BY date) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)")
        con.unregister("fred_df")
        tmp.replace(f)
        sc.update({"type": "manual", "source": "FRED API", "series": sid, "title": meta.get("title"),
                   "units": meta.get("units"), "frequency": meta.get("frequency_short"),
                   "seasonal_adjustment": meta.get("seasonal_adjustment_short"), "date_columns": ["date"],
                   "last_updated": meta["last_updated"], "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "last_obs": rows[-1][0] if rows else None, "refreshed_by": "pulse/editor/release_charts.py"})
        side.write_text(json.dumps(sc, indent=1))
        logger.info(f"lake {sid}: refreshed to {sc['last_obs']} (FRED last_updated {meta['last_updated']})")
    d = con.execute(f"SELECT date, \"{col}\" AS v FROM '{f}' ORDER BY date").df()
    s = pd.Series(d["v"].astype(float).values, index=pd.to_datetime(d["date"]), name=sid).dropna()
    _SERIES[sid] = s
    return s


def _reg(s):
    """Put a monthly or quarterly series on a regular calendar so a missing period (e.g. October 2025, when the
    shutdown cancelled CPI and the household survey) is a gap, not a silently skipped row: shifts and rolling
    windows are then by date, never by position."""
    if len(s) < 3:
        return s
    days = (s.index[1:] - s.index[:-1]).days
    med = sorted(days)[len(days) // 2]
    if 27 <= med <= 31:
        return s.asfreq("MS")
    if 88 <= med <= 93:
        return s.asfreq("QS")
    return s


def ma(s, n):
    return _reg(s).rolling(n).mean().dropna()


def yoy(s, n=None):
    r = _reg(s)
    n = n or (4 if getattr(r.index, "freqstr", "") == "QS-JAN" else 12)
    return (r / r.shift(n) - 1).dropna() * 100


def pchg(s):
    r = _reg(s)
    return (r / r.shift(1) - 1).dropna() * 100


def diff(s):
    return _reg(s).diff().dropna()


def since(s, start: str):
    return s[s.index >= start]


def period_of(ts, freq="M") -> str:
    return ts.strftime("%Y-%m-%d") if freq == "W" else ts.strftime("%Y-%m")


def period_name(period: str, freq="M") -> str:
    y, m = int(period[:4]), int(period[5:7])
    if freq == "W":
        return f"{MONTHS[m - 1]} {int(period[8:10])}"
    if freq == "Q":
        return f"{QNAMES[(m - 1) // 3]}-quarter"
    return MONTHS[m - 1]


def month_year(ts) -> str:
    return f"{MONTHS[ts.month - 1]} {ts.year}"


# ═══════════════════════════════════════════════════════════════════════
# chart engine (house style: chart_recipes.md + owner corrections)
# ═══════════════════════════════════════════════════════════════════════
FAM = "ABC Oracle Edu"
INK, GREY, GRID, BG = "#3D3733", "#7F7570", "#e1e2e3", "#F6F7F3"
BLUE, YELLOW, RED, GREEN = "#0BB4FF", "#FEC439", "#F4743B", "#67A275"
W, M = 960, 40
GL, GR = M, W - M


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _T(x, y, s, size, wt, fill, anchor="start", extra="") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAM}" font-size="{size}" font-weight="{wt}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{_esc(s)}</text>')


def _halo(x, y, s, size, wt, fill, anchor="start") -> str:
    return (f'<g><text x="{x:.1f}" y="{y:.1f}" font-family="{FAM}" font-size="{size}" font-weight="{wt}" fill="{BG}" '
            f'stroke="{BG}" stroke-width="6" stroke-linejoin="round" text-anchor="{anchor}">{_esc(s)}</text>'
            + _T(x, y, s, size, wt, fill, anchor) + '</g>')


def _tw(s, size, wt=400) -> float:
    """Rough text width for layout (Oracle averages ~0.52 em; bold ~0.56)."""
    return len(str(s)) * size * (0.56 if wt >= 500 else 0.50)


def nice_ticks(lo, hi, n=5, zero=False):
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = (hi - lo) or abs(hi) or 1.0
    plo = lo if (zero and lo == 0) else lo - span * 0.07
    phi = hi if (zero and hi == 0) else hi + span * 0.07
    raw = (phi - plo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m for m in (1, 2, 2.5, 5, 10) if m * mag >= raw) * mag
    t0, t1 = math.floor(plo / step + 1e-9) * step, math.ceil(phi / step - 1e-9) * step
    k = int(round((t1 - t0) / step))
    return [round(t0 + i * step, 10) for i in range(k + 1)]


def fmt_num(v, step=None) -> str:
    dec = 0
    if step is not None and step < 1:
        dec = min(2, max(1, -math.floor(math.log10(step) + 1e-9)))
        if abs(step * 10 ** dec - round(step * 10 ** dec)) > 1e-6:
            dec += 1
    elif step is not None and abs(step - round(step)) > 1e-9:
        dec = 1
    s = f"{v:,.{dec}f}"
    return "0" if s in ("-0", "-0.0", "-0.00") else s.replace("-", "−")


def _xdec(ts) -> float:
    return ts.year + (ts.dayofyear - 1) / (366 if ts.is_leap_year else 365)


@dataclass
class Line:
    s: object                     # pandas Series
    color: str = BLUE
    width: float = 2.5
    kind: str = "line"            # line | bar
    key: str | None = None        # name in the panel key
    end_label: str | None = None  # text at the line's end (multi-line panels)
    latest: bool = False          # dot + "Mon YYYY: value" on the last point


@dataclass
class Panel:
    lines: list
    heading: str = ""
    sub: str = ""                 # grey unit text after the heading
    ysuffix: str = ""             # unit on the top y label only
    yprefix: str = ""
    vfmt: Callable | None = None  # value format for the latest label
    zero: bool = False            # include 0 in the y range (bars)
    xmonths: bool = False         # month ticks (short windows)
    end_room: int = 0             # px reserved right of the data for end labels
    show_key: bool = True


def draw_panel(o: list, p: Panel, x0: float, x1: float, top: float, bot: float) -> None:
    """Heading at top-? (caller leaves 64 px above `top`); gridlines span x0..x1; data inset after the y labels."""
    import pandas as pd
    allv = [v for ln in p.lines for v in ln.s.values]
    ticks = nice_ticks(min(allv), max(allv), 5, zero=p.zero)
    step = ticks[1] - ticks[0]
    lo, hi = ticks[0], ticks[-1]
    labels = [p.yprefix + fmt_num(v, step) + (p.ysuffix if i == len(ticks) - 1 else "") for i, v in enumerate(ticks)]
    inset = max(_tw(lb, 20, 300) for lb in labels) + 22
    idx = sorted({t for ln in p.lines for t in ln.s.index})
    t0, t1 = _xdec(idx[0]), _xdec(idx[-1])
    freq_step = (t1 - t0) / max(len(idx) - 1, 1)
    narrow = (x1 - x0) < 330
    # latest-point label: right of the last point, period above, value below (reserved room, clear of the lines)
    lat = []
    for ln in p.lines:
        if ln.latest:
            t, v = ln.s.index[-1], ln.s.iloc[-1]
            vs = (p.vfmt or (lambda z: p.yprefix + fmt_num(z, step / 10) + p.ysuffix))(v)
            if freq_step < 0.05:
                per = f"{MON3[t.month - 1]} {t.day}"
            elif freq_step > 0.2:
                per = f"Q{(t.month - 1) // 3 + 1}" + ("" if narrow else f" {t.year}")
            else:
                per = MON3[t.month - 1] + ("" if narrow else f" {t.year}")
            lat.append((ln, t, v, per, vs))
    room = max([p.end_room] + [max(_tw(a[3], 20, 400), _tw(a[4], 20, 500)) + 14 for a in lat])
    px0, px1 = x0 + inset, x1 - room - 6
    t1p = t1 + freq_step * 0.5
    t0p = t0 - freq_step * 0.5
    X = lambda v: px0 + (v - t0p) / (t1p - t0p) * (px1 - px0)
    Y = lambda v: bot - (v - lo) / (hi - lo) * (bot - top)

    # heading + key
    if p.heading:
        o.append(_T(x0, top - 40, p.heading, 24, 500, INK, extra=' letter-spacing="-0.4"'))
        if p.sub:
            o.append(_T(x0 + _tw(p.heading, 24, 500) + 10, top - 40, p.sub, 20, 300, GREY))
    keyed = [ln for ln in p.lines if ln.key] if p.show_key else []
    key_w = sum(_tw(ln.key, 20, 500) + 22 for ln in keyed)
    head_w = (_tw(p.heading, 24, 500) + 10 + (_tw(p.sub, 20, 300) if p.sub else 0)) if p.heading else 0
    if keyed and x0 + head_w + key_w > x1:
        raise ValueError(f"panel '{p.heading}': heading, unit text and key do not fit in {x1 - x0:.0f}px")
    if keyed:
        xk = x1
        for ln in reversed(keyed):
            o.append(_T(xk, top - 40, ln.key, 20, 500, ln.color if ln.color != YELLOW else "#C9961A", "end"))
            xk -= _tw(ln.key, 20, 500) + 22
    # grid + y labels
    for v, lb in zip(ticks, labels):
        o.append(f'<line x1="{x0:.1f}" y1="{Y(v):.1f}" x2="{x1:.1f}" y2="{Y(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        o.append(_T(x0, Y(v) - 7, lb, 20, 300, "#333"))
    if p.zero and lo < 0 < hi:
        o.append(f'<line x1="{px0 - 4:.1f}" y1="{Y(0):.1f}" x2="{x1:.1f}" y2="{Y(0):.1f}" stroke="{INK}" stroke-width="0.75"/>')
    # x ticks
    ya, yb = idx[0].year, idx[-1].year
    if p.xmonths or (t1 - t0) < 2.2:
        months = [t for t in pd.date_range(idx[0], idx[-1], freq="MS") if t.month in (1, 4, 7, 10)]
        if len(months) > max(2, int((px1 - px0) / 95)):
            months = [t for t in months if t.month in (1, 7)]
        if len(months) > max(2, int((px1 - px0) / 95)):
            months = [t for t in months if t.month == 1]
        for t in months:
            xv = X(_xdec(t))
            o.append(f'<line x1="{xv:.1f}" y1="{bot}" x2="{xv:.1f}" y2="{bot + 10}" stroke="{INK}" stroke-width="0.5"/>')
            o.append(_T(xv, bot + 36, f"{MON3[t.month - 1]} ’{str(t.year)[2:]}", 20, 300, "#333", "middle"))
    else:
        span = yb - ya
        width = px1 - px0
        cands = [1, 2, 3, 4, 5, 10, 20]
        stepy = next((c for c in cands if (span // c + 1) * 56 <= width and span // c + 1 <= 8), 20)
        first = ya if idx[0].month == 1 and idx[0].day <= 7 else ya + 1
        years = list(range(first + ((-first) % stepy if stepy in (2, 5, 10) else 0), yb + 1, stepy))
        if len(years) < 2 and yb > first:
            years = [first, yb]
        for i, yr in enumerate(years):
            xv = X(float(yr))
            o.append(f'<line x1="{xv:.1f}" y1="{bot}" x2="{xv:.1f}" y2="{bot + 10}" stroke="{INK}" stroke-width="0.5"/>')
            lab = str(yr) if (i == 0 or yr % 100 == 0) else "’" + str(yr)[2:]
            o.append(_T(xv, bot + 36, lab, 20, 300, "#333", "middle"))
    # data
    for ln in p.lines:
        s = ln.s
        if ln.kind == "bar":
            bw = max(1.5, (X(t0 + freq_step) - X(t0)) * 0.72)
            for t, v in s.items():
                xv = X(_xdec(t))
                y_a, y_b = sorted((Y(0), Y(v)))
                o.append(f'<rect x="{xv - bw / 2:.1f}" y="{y_a:.1f}" width="{bw:.1f}" height="{max(y_b - y_a, 0.8):.1f}" '
                         f'fill="{ln.color}"/>')
        else:
            d = " ".join(("M" if i == 0 else "L") + f"{X(_xdec(t)):.1f},{Y(v):.1f}" for i, (t, v) in enumerate(s.items()))
            o.append(f'<path d="{d}" fill="none" stroke="{ln.color}" stroke-width="{ln.width}" stroke-linejoin="round" '
                     f'stroke-linecap="round" style="mix-blend-mode:multiply"/>')
    # end labels (de-overlapped)
    ends = [(Y(ln.s.iloc[-1]), ln) for ln in p.lines if ln.end_label]
    ends.sort(key=lambda e: e[0])
    placed = []
    for yv, ln in ends:
        yy = yv + 7
        if placed and yy - placed[-1] < 24:
            yy = placed[-1] + 24
        placed.append(yy)
        o.append(_halo(X(_xdec(ln.s.index[-1])) + 10, yy, ln.end_label, 20, 500,
                       ln.color if ln.color != YELLOW else "#C9961A"))
    # latest-point label (right of the point)
    for ln, t, v, per, vs in lat:
        xv, yv = X(_xdec(t)), Y(v)
        if ln.kind != "bar":
            o.append(f'<circle cx="{xv:.1f}" cy="{yv:.1f}" r="5" fill="{ln.color}" stroke="{BG}" stroke-width="1.5"/>')
        else:
            yv = Y(v) if abs(Y(v) - Y(0)) > 4 else Y(0)
        yc = max(top + 18, min(bot - 22, yv))
        o.append(_halo(xv + 12, yc - 4, per, 20, 400, INK))
        o.append(_halo(xv + 12, yc + 19, vs, 20, 500, INK))


def hbar_panel(o: list, items: list, x0, x1, top, heading, sub, vfmt, highlight=()) -> float:
    """Horizontal bars, one row per (label, value), sorted as given. Returns the bottom y."""
    o.append(_T(x0, top - 40, heading, 24, 500, INK, extra=' letter-spacing="-0.4"'))
    if sub:
        o.append(_T(x0 + _tw(heading, 24, 500) + 10, top - 40, sub, 20, 300, GREY))
    vals = [v for _, v in items]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    lab_w = max(_tw(n, 20, 400) for n, _ in items) + 18
    bx0, bx1 = x0 + lab_w + 70, x1 - 80
    span = (hi - lo) or 1
    X = lambda v: bx0 + (v - lo) / span * (bx1 - bx0)
    rh = 34
    for i, (name, v) in enumerate(items):
        y = top + i * rh
        col = INK if name in highlight else BLUE
        o.append(_T(x0, y + 22, name, 20, 500 if name in highlight else 400, INK))
        xa, xb = sorted((X(0), X(v)))
        o.append(f'<rect x="{xa:.1f}" y="{y + 6:.1f}" width="{max(xb - xa, 1):.1f}" height="22" fill="{col}"/>')
        o.append(_T((xb + 8) if v >= 0 else (xa - 8), y + 24, vfmt(v), 20, 400, INK, "start" if v >= 0 else "end"))
    yb = top + len(items) * rh
    o.append(f'<line x1="{X(0):.1f}" y1="{top:.1f}" x2="{X(0):.1f}" y2="{yb + 2:.1f}" stroke="{INK}" stroke-width="0.75"/>')
    return yb


@dataclass
class Row:
    panels: list                  # Panel objects, laid out side by side
    height: int = 360             # heading to x labels, inclusive
    widths: list | None = None    # fractions


def compose(title: str, rows: list, source_lines: list, extra: Callable | None = None, extra_height: int = 0) -> tuple[str, int]:
    """Stack rows of panels under a title; returns (svg, height)."""
    if _tw(title, 32, 700) > GR - GL:
        size = 28
    else:
        size = 32
    o = []
    y = 72
    o.append(_T(GL, y, title, size, 700, INK, extra=' letter-spacing="-0.7"'))
    y += 34                                  # end of title block
    gap = 30
    for r in rows:
        y += gap
        top = y + 64                          # heading line sits 40 above `top`
        bot = y + r.height - 52               # x labels 36 below `bot`
        n = len(r.panels)
        fr = r.widths or [1 / n] * n
        colgap = 44 if n > 1 else 0
        avail = (GR - GL) - colgap * (n - 1)
        x = GL
        for p, f in zip(r.panels, fr):
            w = avail * f
            draw_panel(o, p, x, x + w, top, bot)
            x += w + colgap
        y += r.height
    if extra:
        y += gap
        y = extra(o, y)
    # footer: logo then source lines
    lw = 130
    lh = lw * 0.3195
    y += 30
    o.append(f'<image xlink:href="{_logo_uri()}" x="{GR - lw}" y="{y:.1f}" width="{lw}" height="{lh:.1f}" opacity="0.6"/>')
    y += lh + 34
    spans = "".join(f'<tspan x="{GL}" dy="{0 if i == 0 else 24}">{_esc(t)}</tspan>' for i, t in enumerate(source_lines))
    o.append(f'<text x="{GL}" y="{y:.1f}" font-family="{FAM}" font-size="20" font-weight="200" fill="{INK}">{spans}</text>')
    H = int(y + 24 * (len(source_lines) - 1) + 40)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}"><rect x="0" y="0" width="{W}" height="{H}" fill="{BG}"/>')
    return head + "".join(o) + "</svg>", H


_LOGO_URI = None


def _logo_uri() -> str:
    global _LOGO_URI
    if _LOGO_URI is None:
        _LOGO_URI = "data:image/png;base64," + base64.b64encode(Path(LOGO).read_bytes()).decode()
    return _LOGO_URI


def wrap(text: str, n: int = 86) -> list:
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def render_png(svg: str, H: int, out: Path) -> Path:
    from playwright.sync_api import sync_playwright
    faces = "".join(f"@font-face{{font-family:'{FAM}';src:url('file://{FONT_DIR}/ABCOracle-{f}.otf');font-weight:{w};}}"
                    for f, w in [("Thin", 200), ("Light", 300), ("Book", 350), ("Regular", 400), ("Medium", 500),
                                 ("Bold", 700)])
    out.parent.mkdir(parents=True, exist_ok=True)
    html = out.with_suffix(".render.html")
    html.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{faces}body{{margin:0;background:#fff}}"
                    f"</style></head><body>{svg}</body></html>")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
            pg.goto(f"file://{html}")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(400)
            pg.locator("svg").screenshot(path=str(out))
            b.close()
    finally:
        html.unlink(missing_ok=True)
    return out


def cur(chg, s) -> float:
    """The change for the latest period of `s`, or NaN when that period has no comparison period (a missing month)."""
    return float(chg.iloc[-1]) if len(chg) and chg.index[-1] == s.index[-1] else float("nan")


def _signed(v, dec=1, suffix="%") -> str:
    if v != v:
        return "n/a"
    if round(v, dec) == 0:
        return f"{0:.{dec}f}{suffix}"
    return ("+" if v > 0 else "−") + f"{abs(v):,.{dec}f}{suffix}"


def _verb(v, up="rose", down="fell", flat="was unchanged") -> str:
    if v != v:
        return "changed"
    return up if v > 0.05 else down if v < -0.05 else flat


def _std_lines(s, start, n=3, avg_key="3-month average", latest_fmt=None):
    sw = since(s, start)
    return [Line(sw, BLUE, 2.5, key="Monthly", latest=True), Line(since(ma(s, n), start), INK, 2.5, key=avg_key)]


# ═══════════════════════════════════════════════════════════════════════
# builders: each returns dict(svg, H, latest (Timestamp), title, caption, source)
# ═══════════════════════════════════════════════════════════════════════
def b_housing_starts():
    h, sf, mf, pm = fred("HOUST"), fred("HOUST1F"), fred("HOUST5F"), fred("PERMIT")
    reg = [("Northeast", fred("HOUSTNE")), ("Midwest", fred("HOUSTMW")), ("South", fred("HOUSTS")), ("West", fred("HOUSTW"))]
    t = h.index[-1]
    ch = cur(pchg(h), h)
    k = lambda v: f"{v:,.0f}k"
    rows = [
        Row([Panel(_std_lines(h, "2016-01"), "Total starts", "thousands, annual rate", vfmt=k)], 380),
        Row([Panel([Line(since(sf, "2019-01"), BLUE, 2, latest=True), Line(since(ma(sf, 3), "2019-01"), INK, 2.5)],
                   "Single-family", vfmt=k, show_key=False),
             Panel([Line(since(mf, "2019-01"), BLUE, 2, latest=True), Line(since(ma(mf, 3), "2019-01"), INK, 2.5)],
                   "5+ units", vfmt=k, show_key=False),
             Panel([Line(since(pm, "2019-01"), BLUE, 2, latest=True), Line(since(ma(pm, 3), "2019-01"), INK, 2.5)],
                   "Permits", vfmt=k, show_key=False)], 300),
        Row([Panel([Line(since(s, "2019-01"), BLUE, 2, latest=True), Line(since(ma(s, 3), "2019-01"), INK, 2.5)],
                   name, vfmt=k, show_key=False) for name, s in reg], 290),
    ]
    yy = cur(yoy(h), h)
    title = f"Housing starts {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Starts {_verb(ch)} {abs(ch):.1f}% to a {h.iloc[-1]:,.0f}k annual rate in {month_year(t)} ({_signed(yy)} "
           f"from a year earlier); single-family {sf.iloc[-1]:,.0f}k, 5+ units {mf.iloc[-1]:,.0f}k, permits {pm.iloc[-1]:,.0f}k.")
    src = (f"Source: Census Bureau and HUD, New Residential Construction, via FRED. Seasonally adjusted annual rates; "
           f"regions are total starts. Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_new_home_sales():
    s, sup, px = fred("HSN1F"), fred("MSACSR"), fred("MSPNHSUS")
    t = s.index[-1]
    ch = cur(pchg(s), s)
    rows = [
        Row([Panel(_std_lines(s, "2016-01"), "New homes sold", "single-family, thousands, annual rate",
                   vfmt=lambda v: f"{v:,.0f}k")], 400),
        Row([Panel([Line(since(sup, "2019-01"), BLUE, 2, latest=True), Line(since(ma(sup, 3), "2019-01"), INK, 2.5)],
                   "Months' supply", vfmt=lambda v: f"{v:.1f}", show_key=False),
             Panel([Line(since(px / 1000, "2019-01"), BLUE, 2, latest=True),
                    Line(since(ma(px / 1000, 3), "2019-01"), INK, 2.5)],
                   "Median price", "$ thousands", yprefix="$", ysuffix="k", vfmt=lambda v: f"${v:,.0f}k",
                   show_key=False)], 320),
    ]
    title = f"New home sales {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"New single-family home sales were {s.iloc[-1]:,.0f}k at an annual rate in {month_year(t)} "
           f"({_signed(cur(yoy(s), s))} from a year earlier), with {sup.iloc[-1]:.1f} months' supply and a median "
           f"price of ${px.iloc[-1]:,.0f}.")
    src = (f"Source: Census Bureau and HUD, New Residential Sales, via FRED. Sales and supply seasonally adjusted; "
           f"median price not seasonally adjusted. Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_existing_home_sales():
    s, inv, px = fred("EXHOSLUSM495S") / 1e6, fred("HOSINVUSM495N") / 1e6, fred("HOSMEDUSM052N") / 1000
    t = s.index[-1]
    ch = cur(pchg(s), s) if len(s) > 1 else float("nan")
    rows = [
        Row([Panel([Line(s, BLUE, 2.5, key="Monthly", latest=True), Line(ma(s, 3), INK, 2.5, key="3-month average")],
                   "Existing homes sold", "millions, annual rate", vfmt=lambda v: f"{v:.2f}m", xmonths=True)], 380),
        Row([Panel([Line(inv, BLUE, 2, latest=True), Line(ma(inv, 3), INK, 2.5)], "Inventory", "millions",
                   vfmt=lambda v: f"{v:.2f}m", show_key=False, xmonths=True),
             Panel([Line(px, BLUE, 2, latest=True), Line(ma(px, 3), INK, 2.5)], "Median price", yprefix="$", ysuffix="k",
                   vfmt=lambda v: f"${v:,.0f}k", show_key=False, xmonths=True)], 320),
    ]
    title = f"Existing home sales {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Existing home sales were {s.iloc[-1]:.2f} million at an annual rate in {month_year(t)}; inventory "
           f"{inv.iloc[-1]:.2f} million, median price ${px.iloc[-1] * 1000:,.0f}.")
    src = (f"Source: National Association of Realtors via FRED, which carries only the latest 13 months. Sales "
           f"seasonally adjusted; inventory and price not. Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


CS_METROS = [("New York", "NYXRNSA"), ("Los Angeles", "LXXRNSA"), ("Chicago", "CHXRNSA"), ("Dallas", "DAXRNSA"),
             ("Miami", "MIXRNSA"), ("Atlanta", "ATXRNSA"), ("Washington", "WDXRNSA"), ("Boston", "BOXRNSA"),
             ("Phoenix", "PHXRNSA"), ("Seattle", "SEXRNSA"), ("San Francisco", "SFXRNSA"), ("Denver", "DNXRNSA"),
             ("Tampa", "TPXRNSA")]


def b_case_shiller():
    nat, c20 = fred("CSUSHPINSA"), fred("SPCS20RNSA")
    t = nat.index[-1]
    yn, y20 = yoy(nat), yoy(c20)
    rows = [Row([Panel([Line(since(yn, "2016-01"), INK, 2.5, end_label=f"National {_signed(cur(yn, nat))}"),
                        Line(since(y20, "2016-01"), BLUE, 2.5, end_label=f"20-city {_signed(cur(y20, c20))}")],
                       "Change from a year earlier", ysuffix="%", zero=True, end_room=150)], 400)]
    items = []
    for name, sid in CS_METROS:
        y = yoy(fred(sid))
        if y.index[-1] == t:
            items.append((name, y.iloc[-1]))
    items.append(("20-city composite", cur(y20, c20)))
    items.sort(key=lambda kv: -kv[1])
    month = month_year(t)

    def extra(o, y):
        top = y + 64
        return hbar_panel(o, items, GL, GR, top, f"Selected metros, {month}", "change from a year earlier",
                          lambda v: _signed(v), highlight=("20-city composite",)) + 10
    title = f"National home prices {_verb(cur(yn, nat), 'rose', 'fell', 'were flat')} {abs(cur(yn, nat)):.1f}% over the year to {MONTHS[t.month - 1]}"
    if _verb(cur(yn, nat)) == "was unchanged":
        title = f"National home prices were flat over the year to {MONTHS[t.month - 1]}"
    best, worst = items[0], items[-1]
    cap = (f"The S&P Cotality Case-Shiller national index was {_signed(cur(yn, nat))} from a year earlier in {month} and the "
           f"20-city composite {_signed(cur(y20, c20))}; among the metros shown, {best[0]} was strongest ({_signed(best[1])}) "
           f"and {worst[0]} weakest ({_signed(worst[1])}).")
    src = (f"Source: S&P Cotality Case-Shiller home price indices (not seasonally adjusted; each is a three-month "
           f"moving average), via FRED. Latest data: {month}.")
    return rows, extra, 64 + len(items) * 34, t, title, cap, src


def b_fhfa_hpi():
    s = fred("HPIPONM226S")
    t = s.index[-1]
    y, m = yoy(s), pchg(s)
    rows = [Row([Panel([Line(since(y, "2016-01"), BLUE, 2.5, latest=True)], "Change from a year earlier", ysuffix="%",
                       zero=True, vfmt=lambda v: _signed(v), show_key=False)], 380),
            Row([Panel([Line(since(m, "2023-01"), BLUE, kind="bar", key="Monthly change", latest=True),
                        Line(since(ma(m, 3), "2023-01"), INK, 2.5, key="3-month average")],
                       "Change from the previous month", ysuffix="%", zero=True, vfmt=lambda v: _signed(v, 2))], 340)]
    title = f"FHFA house prices {_verb(cur(y, s))} {abs(cur(y, s)):.1f}% over the year to {MONTHS[t.month - 1]}"
    cap = (f"The FHFA purchase-only index was {_signed(cur(y, s))} from a year earlier in {month_year(t)} and "
           f"{_signed(cur(m, s), 2)} from the month before (seasonally adjusted).")
    src = (f"Source: Federal Housing Finance Agency purchase-only house price index (seasonally adjusted), via FRED. "
           f"Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_payrolls():
    p, u, c = fred("PAYEMS"), fred("UNRATE"), fred("USCONS")
    t = p.index[-1]
    d = diff(p)
    dc = diff(c)
    rows = [Row([Panel([Line(since(d, "2023-01"), BLUE, kind="bar", key="Monthly change", latest=True),
                        Line(since(ma(d, 3), "2023-01"), INK, 2.5, key="3-month average")],
                       "Nonfarm payrolls", "change, thousands", zero=True,
                       vfmt=lambda v: ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):,.0f}k")], 400),
            Row([Panel([Line(since(u, "2016-01"), BLUE, 2.5, latest=True)], "Unemployment rate", ysuffix="%",
                       vfmt=lambda v: f"{v:.1f}%", show_key=False),
                 Panel([Line(since(c / 1000, "2016-01"), BLUE, 2.5, latest=True)], "Construction jobs", "millions",
                       vfmt=lambda v: f"{v:.2f}m", show_key=False)], 320)]
    v = cur(d, p)
    title = (f"Employers added {v * 1000:,.0f} jobs in {MONTHS[t.month - 1]}" if v >= 0 else
             f"Payrolls fell by {abs(v) * 1000:,.0f} jobs in {MONTHS[t.month - 1]}")
    cap = (f"Nonfarm payrolls changed by {v:+,.0f}k in {month_year(t)} (three-month average {ma(d, 3).iloc[-1]:+,.0f}k); "
           f"unemployment {u.iloc[-1]:.1f}%; construction employment {cur(dc, c):+,.0f}k on the month.")
    src = (f"Source: Bureau of Labor Statistics, Employment Situation, via FRED. Seasonally adjusted. Latest data: "
           f"{month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_cpi_shelter():
    hd, rent, oer = fred("CPIAUCSL"), fred("CUSR0000SEHA"), fred("CUSR0000SEHC")
    t = hd.index[-1]
    yh, yr, yo = yoy(hd), yoy(rent), yoy(oer)
    rows = [Row([Panel([Line(since(yh, "2016-01"), INK, 2.5, end_label=f"All items {_signed(cur(yh, hd))}"),
                        Line(since(yr, "2016-01"), BLUE, 2.5, end_label=f"Rent {_signed(cur(yr, rent))}"),
                        Line(since(yo, "2016-01"), YELLOW, 2.5, end_label=f"Owners' equiv. {_signed(cur(yo, oer))}")],
                       "Change from a year earlier", ysuffix="%", zero=True, end_room=210)], 400),
            Row([Panel([Line(since(pchg(s), "2023-01"), BLUE, kind="bar", latest=True),
                        Line(since(ma(pchg(s), 3), "2023-01"), INK, 2.5)],
                       name, ysuffix="%", zero=True, vfmt=lambda v: _signed(v, 2), show_key=False)
                 for name, s in [("Rent, monthly", rent), ("OER, monthly", oer), ("All items, monthly", hd)]], 300)]
    title = f"CPI rent {_verb(cur(yr, rent))} {abs(cur(yr, rent)):.1f}% over the year to {MONTHS[t.month - 1]}"
    cap = (f"In {month_year(t)} rent of primary residence was {_signed(cur(yr, rent))} from a year earlier "
           f"({_signed(cur(pchg(rent), rent), 2)} on the month), owners' equivalent rent {_signed(cur(yo, oer))} and "
           f"all items {_signed(cur(yh, hd))}.")
    src = (f"Source: Bureau of Labor Statistics, Consumer Price Index (seasonally adjusted), via FRED. Bars are monthly "
           f"changes; black lines their 3-month average. Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_mortgage_rates():
    r, g = fred("MORTGAGE30US"), fred("DGS10")
    t = r.index[-1]
    gw = g.resample("W-THU").mean().reindex(r.index).dropna()
    spread = (r.reindex(gw.index) - gw).dropna()
    rows = [Row([Panel([Line(since(r, "2022-01"), BLUE, 2.5, key="Weekly", latest=True),
                        Line(since(ma(r, 13), "2022-01"), INK, 2.5, key="13-week average")],
                       "30-year fixed mortgage rate", ysuffix="%", vfmt=lambda v: f"{v:.2f}%")], 400),
            Row([Panel([Line(since(spread, "2022-01"), BLUE, 2.5, latest=True),
                        Line(since(ma(spread, 13), "2022-01"), INK, 2.5)],
                       "Spread over the 10-year Treasury", "percentage points", vfmt=lambda v: f"{v:.2f}",
                       show_key=False)], 320)]
    title = f"The 30-year mortgage rate was {r.iloc[-1]:.2f}% on {MONTHS[t.month - 1]} {t.day}"
    wk = r.iloc[-1] - r.iloc[-2]
    cap = (f"Freddie Mac's 30-year fixed rate averaged {r.iloc[-1]:.2f}% in the week to {MONTHS[t.month - 1]} {t.day}, "
           f"{'unchanged' if round(wk, 2) == 0 else _signed(wk, 2, ' points')} on the week; the spread over the 10-year Treasury was {spread.iloc[-1]:.2f} points.")
    src = (f"Source: Freddie Mac Primary Mortgage Market Survey and Federal Reserve 10-year Treasury yield (weekly "
           f"average to Thursday), via FRED. Latest data: week of {MONTHS[t.month - 1]} {t.day}, {t.year}.")
    return rows, None, 0, t, title, cap, src


def b_construction_spending():
    s = fred("TLRESCONS") / 1000
    t = s.index[-1]
    ch, y = cur(pchg(s), s), yoy(s)
    rows = [Row([Panel(_std_lines(s, "2016-01"), "Residential construction", "$bn, annual rate",
                       yprefix="$", vfmt=lambda v: f"${v:,.0f}bn")], 420),
            Row([Panel([Line(since(y, "2016-01"), BLUE, 2.5, latest=True)], "Change from a year earlier", ysuffix="%",
                       zero=True, vfmt=lambda v: _signed(v), show_key=False)], 300)]
    title = f"Residential construction spending {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Residential construction spending was ${s.iloc[-1]:,.0f} billion at an annual rate in {month_year(t)}, "
           f"{_signed(ch)} on the month and {_signed(cur(y, s))} on the year (not adjusted for inflation).")
    src = (f"Source: Census Bureau, Construction Spending (seasonally adjusted annual rate, current dollars), via FRED. "
           f"Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_jolts():
    jo, q = fred("JTSJOL") / 1000, fred("JTSQUR")
    t = jo.index[-1]
    rows = [Row([Panel(_std_lines(jo, "2016-01"), "Job openings", "millions", vfmt=lambda v: f"{v:.2f}m")], 400),
            Row([Panel([Line(since(q, "2016-01"), BLUE, 2.5, latest=True), Line(since(ma(q, 3), "2016-01"), INK, 2.5)],
                       "Quits rate", "% of employment", ysuffix="%", vfmt=lambda v: f"{v:.1f}%", show_key=False)], 320)]
    ch = cur(diff(jo), jo)
    title = f"Job openings {_verb(ch, 'rose', 'fell', 'held')} to {jo.iloc[-1]:.1f} million in {MONTHS[t.month - 1]}"
    cap = (f"Job openings were {jo.iloc[-1]:.2f} million in {month_year(t)} ({ch * 1000:+,.0f}k on the month); the "
           f"quits rate was {q.iloc[-1]:.1f}%.")
    src = (f"Source: Bureau of Labor Statistics, Job Openings and Labor Turnover Survey (seasonally adjusted), via FRED. "
           f"Latest data: {month_year(t)}.")
    return rows, None, 0, t, title, cap, src


def b_jobless_claims():
    s = fred("ICSA") / 1000
    t = s.index[-1]
    rows = [Row([Panel([Line(since(s, "2023-01"), BLUE, 2, key="Weekly", latest=True),
                        Line(since(ma(s, 4), "2023-01"), INK, 2.5, key="4-week average")],
                       "Initial jobless claims", "thousands", vfmt=lambda v: f"{v:,.0f}k")], 520)]
    title = f"Initial jobless claims were {s.iloc[-1] * 1000:,.0f} in the week to {MONTHS[t.month - 1]} {t.day}"
    cap = (f"Initial claims for unemployment insurance were {s.iloc[-1]:,.0f}k in the week to {MONTHS[t.month - 1]} "
           f"{t.day} (4-week average {ma(s, 4).iloc[-1]:,.0f}k).")
    src = (f"Source: Department of Labor, weekly unemployment insurance claims (seasonally adjusted), via FRED. "
           f"Latest data: week to {MONTHS[t.month - 1]} {t.day}, {t.year}.")
    return rows, None, 0, t, title, cap, src


def b_hvs():
    ho, rv, hv = fred("RHORUSQ156N"), fred("RRVRUSQ156N"), fred("RHVRUSQ156N")
    t = ho.index[-1]
    pct = lambda v: f"{v:.1f}%"
    rows = [Row([Panel([Line(since(ho, "2010-01"), BLUE, 2, key="Quarterly", latest=True),
                        Line(since(ma(ho, 4), "2010-01"), INK, 2.5, key="4-quarter average")],
                       "Homeownership rate", ysuffix="%", vfmt=pct)], 380),
            Row([Panel([Line(since(rv, "2010-01"), BLUE, 2, latest=True), Line(since(ma(rv, 4), "2010-01"), INK, 2.5)],
                       "Rental vacancy rate", ysuffix="%", vfmt=pct, show_key=False),
                 Panel([Line(since(hv, "2010-01"), BLUE, 2, latest=True), Line(since(ma(hv, 4), "2010-01"), INK, 2.5)],
                       "Homeowner vacancy rate", ysuffix="%", vfmt=pct, show_key=False)], 320)]
    q = QNAMES[(t.month - 1) // 3]
    title = f"The homeownership rate was {ho.iloc[-1]:.1f}% in the {q} quarter"
    cap = (f"In the {q} quarter of {t.year} the homeownership rate was {ho.iloc[-1]:.1f}%, the rental vacancy rate "
           f"{rv.iloc[-1]:.1f}% and the homeowner vacancy rate {hv.iloc[-1]:.1f}%.")
    src = (f"Source: Census Bureau, Housing Vacancies and Homeownership (not seasonally adjusted), via FRED. "
           f"Latest data: Q{(t.month - 1) // 3 + 1} {t.year}.")
    return rows, None, 0, t, title, cap, src


# ═══════════════════════════════════════════════════════════════════════
# registry
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class Release:
    key: str
    name: str
    trigger: str                   # regex, case-insensitive, matched against entry trigger + title
    source: str
    series: list = field(default_factory=list)
    builder: Callable | None = None
    intro: str = "The {period} {name} release, charted:"
    freq: str = "M"                # M | W | Q
    rx: re.Pattern | None = None

    def __post_init__(self):
        self.rx = re.compile(self.trigger, re.I)


REGISTRY = [
    # wave 2 first where its pattern is more specific than a wave-1 neighbour (pending before existing)
    Release("pending_home_sales", "pending home sales", r"pending[- ]home[- ]sales|pending sales index|\bPHSI\b",
            "NAR pending home sales index (release page; not on FRED)"),
    Release("housing_starts", "housing starts", r"housing starts|building permits|residential construction|"
            r"\bstarts\b.{0,40}\bpermits\b", "FRED (Census/HUD New Residential Construction)",
            ["HOUST", "HOUST1F", "HOUST5F", "PERMIT", "HOUSTNE", "HOUSTMW", "HOUSTS", "HOUSTW"], b_housing_starts),
    Release("new_home_sales", "new home sales", r"new[- ]home sales|new single-family (home )?sales|sales of new (single-family )?homes",
            "FRED (Census/HUD New Residential Sales)", ["HSN1F", "MSACSR", "MSPNHSUS"], b_new_home_sales),
    Release("existing_home_sales", "existing home sales", r"existing[- ]home sales",
            "FRED (NAR, latest 13 months only)", ["EXHOSLUSM495S", "HOSINVUSM495N", "HOSMEDUSM052N"], b_existing_home_sales),
    Release("case_shiller", "Case-Shiller", r"case[- ]shiller", "FRED (S&P Cotality Case-Shiller)",
            ["CSUSHPINSA", "SPCS20RNSA"] + [s for _, s in CS_METROS], b_case_shiller,
            intro="The {period} Case-Shiller release, charted:"),
    Release("cotality_hpi", "Cotality home price index", r"\bcotality\b|\bcorelogic\b", "Cotality HPI (wave 2)"),
    Release("fhfa_hpi", "FHFA house price index", r"\bFHFA\b.{0,60}(price|HPI|index)|federal housing finance agency.{0,60}price",
            "FRED (FHFA purchase-only HPI)", ["HPIPONM226S"], b_fhfa_hpi, intro="The {period} FHFA house price release, charted:"),
    Release("zillow_zhvi", "Zillow home values", r"\bZHVI\b|zillow home value|zillow.{0,40}home (price|value)s?",
            "Zillow ZHVI with a map (wave 2)"),
    Release("zillow_zori", "Zillow rents", r"\bZORI\b|zillow observed rent|zillow.{0,40}rents?\b", "Zillow ZORI (wave 2)"),
    Release("apartment_list", "Apartment List rents", r"apartment list", "Apartment List national rent report (wave 2)"),
    Release("construction_spending", "construction spending", r"construction spending|construction outlays",
            "FRED (Census Construction Spending)", ["TLRESCONS"], b_construction_spending),
    Release("hvs", "housing vacancies and homeownership", r"homeownership rate|housing vacanc|vacancy rates?\b|\bHVS\b",
            "FRED (Census HVS)", ["RHORUSQ156N", "RRVRUSQ156N", "RHVRUSQ156N"], b_hvs, freq="Q",
            intro="The {period} homeownership and vacancy release, charted:"),
    Release("nahb_hmi", "NAHB builder confidence", r"\bNAHB\b|builder (confidence|sentiment)|housing market index",
            "NAHB/Wells Fargo HMI (wave 2)"),
    Release("payrolls", "jobs report", r"non-?farm payrolls?|jobs report|employment situation|\bpayrolls\b",
            "FRED (BLS Employment Situation)", ["PAYEMS", "UNRATE", "USCONS"], b_payrolls,
            intro="The {period} jobs report, charted:"),
    Release("jolts", "JOLTS", r"\bJOLTS\b|job openings", "FRED (BLS JOLTS)", ["JTSJOL", "JTSQUR"], b_jolts,
            intro="The {period} job openings release, charted:"),
    Release("jobless_claims", "jobless claims", r"jobless claims|initial claims|unemployment (insurance )?claims|weekly claims",
            "FRED (DOL weekly claims)", ["ICSA"], b_jobless_claims, freq="W",
            intro="The jobless claims report for the week to {period}, charted:"),
    Release("cpi_shelter", "CPI", r"\bCPI\b|consumer price index|shelter inflation|inflation report",
            "FRED (BLS CPI)", ["CPIAUCSL", "CUSR0000SEHA", "CUSR0000SEHC"], b_cpi_shelter,
            intro="The {period} CPI release, charted:"),
    Release("ice_mortgage_monitor", "ICE Mortgage Monitor", r"mortgage monitor|\bICE\b.{0,60}delinquen|ICE first look",
            "ICE Mortgage Monitor / First Look delinquencies (wave 2)"),
    Release("vehicle_sales", "vehicle sales", r"(light )?vehicle sales|auto sales", "BEA/Wards light vehicle sales (wave 2)"),
    Release("gdp", "GDP", r"\bGDP\b|gross domestic product", "BEA GDP (wave 2)"),
    Release("ism", "ISM", r"\bISM\b|purchasing managers", "ISM manufacturing and services (wave 2)"),
    Release("mortgage_rates", "mortgage rate survey", r"freddie mac.{0,60}rate|primary mortgage market survey|\bPMMS\b",
            "FRED (Freddie Mac PMMS)", ["MORTGAGE30US", "DGS10"], b_mortgage_rates, freq="W",
            intro="Freddie Mac's mortgage rate survey for the week of {period}, charted:"),
]
BY_KEY = {r.key: r for r in REGISTRY}


# ═══════════════════════════════════════════════════════════════════════
# state, log, build
# ═══════════════════════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    tmp.replace(STATE_PATH)


def _log(rec: dict) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _free_path(p: Path) -> Path:
    """Never overwrite an image: add _2, _3 ... (or bump a trailing _vN)."""
    if not p.exists():
        return p
    m = re.match(r"(.*)_v(\d+)$", p.stem)
    k = int(m.group(2)) + 1 if m else 2
    while True:
        q = p.with_name(f"{m.group(1)}_v{k}{p.suffix}" if m else f"{p.stem}_{k}{p.suffix}")
        if not q.exists():
            return q
        k += 1


def latest_period(key: str) -> str:
    rel = BY_KEY[key]
    s = fred(rel.series[0])
    return period_of(s.index[-1], rel.freq)


def build(key: str, as_of: date | str | None = None, force: bool = False, out_path: Path | str | None = None,
          record: bool = True, draft_date: str | None = None) -> dict | None:
    """Build the release chart. Hosted (and recorded in the state file) unless out_path is given.
    None when the newest period is not newer than the one already hosted (unless force)."""
    rel = BY_KEY[key]
    if rel.builder is None:
        raise NotImplementedError(f"{key}: no builder yet (wave 2)")
    as_of = as_of if isinstance(as_of, date) else (date.fromisoformat(as_of) if as_of else date.today())
    st = load_state()
    rows, extra, extra_h, t, title, cap, src = rel.builder()
    if "n/a" in title or "nan" in title:      # a latest change with no comparison period (missing month)
        title = f"{rel.name[0].upper()}{rel.name[1:]}: {period_name(period_of(t, rel.freq), rel.freq)} {t.year}"
    per = period_of(t, rel.freq)
    prev = st.get(key) or {}
    if not force and out_path is None and prev.get("period") and per <= prev["period"]:
        return None
    svg, H = compose(title, rows, wrap(src), extra=extra)
    if out_path is None:
        compact = per.replace("-", "")
        dest = _free_path(IMAGES / key / f"{compact}_{as_of.strftime('%Y%m%d')}.png")
    else:
        dest = _free_path(Path(out_path))
    render_png(svg, H, dest)
    out = {"key": key, "png_path": str(dest), "latest_period": per, "title": title, "caption": cap, "source_line": src,
           "period_name": period_name(per, rel.freq)}
    if out_path is None:
        out["url"] = f"{PUBLIC_BASE}/{key}/{dest.name}"
        if record:
            st[key] = {"period": per, "url": out["url"], "png": str(dest), "built_at":
                       datetime.now(timezone.utc).isoformat(timespec="seconds"), "draft_date": draft_date,
                       "title": title, "caption": cap}
            _save_state(st)
    return out


# ═══════════════════════════════════════════════════════════════════════
# apply to a draft
# ═══════════════════════════════════════════════════════════════════════
_MONTH_RX = re.compile(r"\b(" + "|".join(MONTHS) + r")\b", re.I)


def match_release(entry: dict) -> tuple[Release, bool] | None:
    """First registry release whose pattern matches the entry; (release, matched_in_title)."""
    title, trig = entry.get("title") or "", entry.get("trigger") or ""
    for rel in REGISTRY:
        in_title = bool(rel.rx.search(title))
        if in_title or rel.rx.search(trig):
            return rel, in_title
    return None


def _months_named(text: str) -> set[int]:
    return {MONTHS.index(m.capitalize()) + 1 for m in _MONTH_RX.findall(text or "")}


def apply(draft_obj: dict, d: date | str, dry: bool = False, force: set | None = None,
          out_dir: Path | str | None = None) -> list[dict]:
    """Append a fresh release chart to each matching entry. Returns one record per matched release."""
    ds = d.isoformat() if isinstance(d, date) else str(d)
    force = force or set()
    entries = draft_obj.get("entries") or []
    chosen: dict[str, tuple] = {}
    for pos, e in enumerate(entries):
        m = match_release(e)
        if not m:
            continue
        rel, in_title = m
        rank = (0 if in_title else 1, pos)
        if rel.key not in chosen or rank < chosen[rel.key][0]:
            chosen[rel.key] = (rank, e)
    results = []
    st = load_state()
    for key, (_, e) in chosen.items():
        rel = BY_KEY[key]
        rec = {"date": ds, "key": key, "entry": e.get("title"), "dry": dry}
        try:
            if rel.builder is None:
                rec.update(action="no-builder", note=f"{rel.name}: recognised, no builder yet (wave 2; {rel.source})")
                continue
            if "/images/releases/" in (e.get("summary") or ""):
                rec.update(action="already-present")
                continue
            per = latest_period(key)
            rec["period"] = per
            if rel.freq == "M":
                named = _months_named(e.get("trigger") or "") or _months_named(e.get("title") or "")
                if named and int(per[5:7]) not in named:
                    rec.update(action="data-not-updated",
                               note=f"entry names {sorted(MONTHS[k - 1] for k in named)}, latest FRED period is {per}")
                    continue
            prev = st.get(key) or {}
            if dry:
                od = Path(out_dir) if out_dir else DRY_DIR
                b = build(key, ds, force=True, out_path=od / f"{key}_dry_{ds.replace('-', '')}_v1.png")
                rec.update(action="would-add", png=b["png_path"], title=b["title"],
                           note=("already hosted for this period: " + prev.get("url", "")) if prev.get("period") == per else "")
                rec["intro"] = rel.intro.format(period=b["period_name"], name=rel.name)
            else:
                if key not in force and prev.get("period") == per and prev.get("draft_date") == ds and prev.get("url"):
                    b = {"url": prev["url"], "title": prev.get("title", rel.name), "caption": prev.get("caption", ""),
                         "period_name": period_name(per, rel.freq), "latest_period": per}
                    rec["reused"] = True
                else:
                    b = build(key, ds, force=key in force, draft_date=ds)
                if b is None:
                    rec.update(action="not-new", note=f"period {per} already charted ({prev.get('url')})")
                    continue
                intro = rel.intro.format(period=b["period_name"], name=rel.name)
                alt = re.sub(r"[\[\]()]", "", f"{b['title']}. {b['caption']}")
                e["summary"] = (e.get("summary") or "").rstrip() + f"\n\n{intro}\n\n![{alt}]({b['url']})"
                rec.update(action="added", url=b["url"], intro=intro)
        except Exception as ex:  # noqa: BLE001
            logger.exception(f"release chart {key} failed")
            rec.update(action="error", error=f"{type(ex).__name__}: {ex}")
        finally:
            results.append(rec)
            _log(rec)
    return results


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=None, help="draft date (default: today, US Eastern)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="append", default=[], metavar="KEY")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drafts
    ds = a.date or drafts.today_et()
    for attempt in range(5):
        row = drafts.get(ds)
        if not row:
            print(f"no draft for {ds}")
            return 1
        obj = row["json"]
        res = apply(obj, ds, dry=a.dry, force=set(a.force), out_dir=a.out_dir)
        for r in res:
            print(f"{r['key']:22} {r.get('action'):18} {r.get('period', ''):10} | {r.get('entry')}")
            for k in ("intro", "url", "png", "note", "error"):
                if r.get(k):
                    print(f"{'':22} {k}: {r[k]}")
        if a.dry or not any(r.get("action") == "added" for r in res):
            return 0
        try:
            new = drafts.save(ds, obj, row["version"])
            print(f"saved draft {ds} version {new['version']}")
            return 0
        except drafts.Conflict:
            print("draft changed while applying; reloading and retrying")
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
