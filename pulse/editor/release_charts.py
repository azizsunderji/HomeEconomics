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
    # after one failed series, later calls in the same run try once (FRED's maintenance page answered every request
    # on 21 Oct 2026; without this each of the dozens of series waited through three retries)
    for attempt in range(1 if _FRED_DOWN else 3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (HomeEconomics release charts)"}),
                         timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            if not _FRED_DOWN:
                time.sleep(1.5 * (attempt + 1))
    _FRED_DOWN.append(str(last))
    raise RuntimeError(f"FRED {path} {params.get('series_id')}: {last}")


_FRED_DOWN: list = []


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
# chart engine (house style: chart_recipes.md + the owner's grid rules of 21 Oct 2026)
# Owner, 21 Oct 2026, on the pending-sales reference (OVH/2026_10_21_ReleaseCharts/scripts/90_phsi_chart_grid.py):
# "pay more attention to the charts ... taking care to align typography, alignment". Everything is left-aligned to
# the title; y-axis numbers sit at the panel's left edge (text-anchor start) and the plot starts after them; panels
# side by side have identical width and height; each panel has its own one-line label (20 px, muted) whose baseline
# is 36 px above the panel top; line stroke 3.4; bars brand blue whatever the sign; horizontal bars are thick and
# almost touch, extend from an axis line, carry one bold value each, with the category names right of the axis;
# about 60 px from the charts to a short source line; text widths are measured with the Oracle font files.
# ═══════════════════════════════════════════════════════════════════════
FAM = "ABC Oracle Edu"
INK, GREY, GRID, BG = "#3D3733", "#8A8580", "#DADFCE", "#F6F7F3"
BLUE, YELLOW, RED, GREEN = "#0BB4FF", "#FEC439", "#F4743B", "#67A275"
W = 960
GL, GR = 36, 944                  # left margin = the title's x; right margin 16 (reference design)
LINE_SCALE = 3.4 / 2.5            # builders pass 2.5 (or 2) for a line; the owner's stroke is 3.4
LABEL_GAP = 36                    # panel label baseline -> panel top
XLAB = 30                         # panel bottom -> x-axis label baseline
COLGAP = 20                       # between panels side by side
ROWGAP = 40                       # between rows
SRC_GAP = 50                      # last x-axis label row (+10) -> source baseline (about 60 px of space)

_FONTS: dict = {}
_FONT_FILES = {200: "Thin", 300: "Light", 350: "Book", 400: "Regular", 500: "Medium", 700: "Bold"}


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _T(x, y, s, size, wt, fill, anchor="start", extra="") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAM}" font-size="{size}" font-weight="{wt}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{_esc(s)}</text>')


def _halo(x, y, s, size, wt, fill, anchor="start") -> str:
    return (f'<g><text x="{x:.1f}" y="{y:.1f}" font-family="{FAM}" font-size="{size}" font-weight="{wt}" fill="{BG}" '
            f'stroke="{BG}" stroke-width="6" stroke-linejoin="round" text-anchor="{anchor}">{_esc(s)}</text>'
            + _T(x, y, s, size, wt, fill, anchor) + '</g>')


def _tw(s, size, wt=400, spacing=0.0) -> float:
    """Rendered text width in the Oracle font (PIL), falling back to an estimate if the font cannot be loaded."""
    s = str(s)
    try:
        from PIL import ImageFont
        k = (size, _FONT_FILES.get(wt, "Regular"))
        if k not in _FONTS:
            _FONTS[k] = ImageFont.truetype(f"{FONT_DIR}/ABCOracle-{k[1]}.otf", size)
        return _FONTS[k].getlength(s) + spacing * len(s)
    except Exception:  # noqa: BLE001
        return len(s) * size * (0.56 if wt >= 500 else 0.50) + spacing * len(s)


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
    width: float = 2.5            # drawn at width * LINE_SCALE (2.5 -> 3.4)
    kind: str = "line"            # line | bar
    key: str | None = None        # name in the panel key (right end of the label line)
    end_label: str | None = None  # text at the line's end (multi-line panels)
    latest: bool | str = False    # True: dot + "Mon YYYY / value" on the last point; "dot": the dot only
    shade: bool = False           # light fill between the line and the plot bottom (level lines)


@dataclass
class Panel:
    lines: list
    heading: str = ""             # the panel's one-line label is "heading, sub"
    sub: str = ""
    ysuffix: str = ""             # unit on the top y label only
    yprefix: str = ""
    vfmt: Callable | None = None  # value format for the latest label
    zero: bool = False            # include 0 in the y range (bars)
    xmonths: bool = False         # month ticks (short windows)
    end_room: int = 0             # px reserved right of the data for end labels
    show_key: bool = True
    ref: float | None = None      # a reference level drawn as a dark line (e.g. 50 on a diffusion index)

    @property
    def label(self) -> str:
        return self.heading + (f", {self.sub}" if self.heading and self.sub else self.sub)


@dataclass
class Prebuilt:
    """A builder that lays out its own SVG (an owner-approved bespoke design) returns this in place of rows."""
    svg: str
    H: int


def _xlabel(o, xv, text, px0, x1, y):
    """An x-axis label centred on xv, kept inside the plot (start-anchored at the left end, end-anchored at the right)."""
    w = _tw(text, 20)
    if xv - w / 2 < px0 - 2:
        o.append(_T(xv, y, text, 20, 400, GREY, "start"))
    elif xv + w / 2 > x1 + 2:
        o.append(_T(xv, y, text, 20, 400, GREY, "end"))
    else:
        o.append(_T(xv, y, text, 20, 400, GREY, "middle"))


def draw_panel(o: list, p: Panel, x0: float, x1: float, top: float, bot: float) -> None:
    """Label baseline LABEL_GAP above `top`; y numbers start at x0; gridlines and data run from after them to x1."""
    import pandas as pd
    allv = [v for ln in p.lines for v in ln.s.values] + ([p.ref] if p.ref is not None else [])
    # y domain = data range + 7% padding (as in the pending-sales reference, whose axis runs 55..135 with gridlines
    # 60..120), not stretched to the next whole tick: a tick band with no data in it (e.g. -10% under a line that
    # bottoms at -0.3%) is dropped. Gridlines are the nice ticks inside the domain (at least 4 where possible).
    dlo, dhi = min(allv), max(allv)
    if p.zero:
        dlo, dhi = min(dlo, 0.0), max(dhi, 0.0)
    dspan = (dhi - dlo) or abs(dhi) or 1.0
    lo = dlo if (p.zero and dlo == 0) else dlo - dspan * 0.07
    hi = dhi if (p.zero and dhi == 0) else dhi + dspan * 0.07
    cands = []
    for n_t in (5, 6, 7):
        full = nice_ticks(min(allv), max(allv), n_t, zero=p.zero)
        cands.append((full, [v for v in full if lo - 1e-9 <= v <= hi + 1e-9]))

    def _round_step(tk):          # 1, 2 or 5 x 10^k: no "12.5"-style labels from a 2.5 step
        st = tk[1] - tk[0]
        return abs(st / 10 ** math.floor(math.log10(st)) - 2.5) > 1e-6
    pick = (next((c for c in cands if len(c[1]) >= 4 and _round_step(c[1])), None)
            or next((c for c in cands if len(c[1]) >= 3), None))
    if pick:
        ticks = pick[1]
    else:
        ticks = cands[0][0]
        lo, hi = ticks[0], ticks[-1]
    step = ticks[1] - ticks[0]
    labels = [p.yprefix + fmt_num(v, step) + (p.ysuffix if i == len(ticks) - 1 else "") for i, v in enumerate(ticks)]
    px0 = x0 + max(_tw(lb, 20) for lb in labels) + 16
    idx = sorted({t for ln in p.lines for t in ln.s.index})
    t0, t1 = _xdec(idx[0]), _xdec(idx[-1])
    freq_step = (t1 - t0) / max(len(idx) - 1, 1)
    narrow = (x1 - x0) < 330
    bars = any(ln.kind == "bar" for ln in p.lines)
    lat = []
    for ln in p.lines:
        if ln.latest is True:
            t, v = ln.s.index[-1], ln.s.iloc[-1]
            vs = (p.vfmt or (lambda z: p.yprefix + fmt_num(z, step / 10) + p.ysuffix))(v)
            if freq_step < 0.05:
                per = f"{MON3[t.month - 1]} {t.day}"
            elif freq_step > 0.2:
                per = f"Q{(t.month - 1) // 3 + 1}" + ("" if narrow else f" {t.year}")
            else:
                per = MON3[t.month - 1] + ("" if narrow else f" {t.year}")
            lat.append((ln, t, v, per, vs))
    room = max([p.end_room] + [max(_tw(a[3], 20, 400), _tw(a[4], 20, 500)) + 14 for a in lat]
               + [6 for ln in p.lines if ln.latest == "dot"])
    px1 = x1 - room
    t0p, t1p = (t0 - freq_step * 0.5, t1 + freq_step * 0.5) if bars else (t0, t1)
    X = lambda v: px0 + (v - t0p) / ((t1p - t0p) or 1) * (px1 - px0)
    Y = lambda v: bot - (v - lo) / (hi - lo) * (bot - top)

    # label + key on one line
    if p.label:
        o.append(_T(x0, top - LABEL_GAP, p.label, 20, 400, GREY))
    keyed = [ln for ln in p.lines if ln.key] if p.show_key else []
    key_w = sum(_tw(ln.key, 20, 500) for ln in keyed) + 22 * max(len(keyed) - 1, 0)
    if keyed and x0 + (_tw(p.label, 20) + 24 if p.label else 0) + key_w > x1:
        raise ValueError(f"panel '{p.label}': label and key do not fit in {x1 - x0:.0f}px")
    xk = x1
    for ln in reversed(keyed):
        o.append(_T(xk, top - LABEL_GAP, ln.key, 20, 500, ln.color if ln.color != YELLOW else "#C9961A", "end"))
        xk -= _tw(ln.key, 20, 500) + 22
    # grid + y labels (numbers left-aligned at x0, vertically centred on their gridline)
    for v, lb in zip(ticks, labels):
        o.append(f'<line x1="{px0:.1f}" y1="{Y(v):.1f}" x2="{x1:.1f}" y2="{Y(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        o.append(_T(x0, Y(v) + 7, lb, 20, 400, GREY))
    if p.zero and lo < 0 < hi:
        o.append(f'<line x1="{px0:.1f}" y1="{Y(0):.1f}" x2="{x1:.1f}" y2="{Y(0):.1f}" stroke="{INK}" stroke-width="1"/>')
    if p.ref is not None:
        o.append(f'<line x1="{px0:.1f}" y1="{Y(p.ref):.1f}" x2="{x1:.1f}" y2="{Y(p.ref):.1f}" stroke="{INK}" '
                 f'stroke-width="1.2"/>')
    # x labels (no tick marks)
    ya, yb = idx[0].year, idx[-1].year
    if p.xmonths or (t1 - t0) < 2.2:
        months = [t for t in pd.date_range(idx[0], idx[-1], freq="MS") if t.month in (1, 4, 7, 10)]
        for keep in ((1, 7), (1,)):
            if len(months) > max(2, int((px1 - px0) / 110)):
                months = [t for t in months if t.month in keep]
        for t in months:
            _xlabel(o, X(_xdec(t)), f"{MON3[t.month - 1]} ’{str(t.year)[2:]}", px0, x1, bot + XLAB)
    else:
        first = ya if idx[0].month == 1 and idx[0].day <= 7 else ya + 1

        def extents(yrs):
            out = []
            for yr in yrs:
                xv, w = X(float(yr)), _tw(str(yr), 20)
                a = xv if xv - w / 2 < px0 - 2 else (xv - w if xv + w / 2 > x1 + 2 else xv - w / 2)
                out.append((a, a + w))
            return out

        years = [first]
        for c in (1, 2, 4, 5, 10, 20):
            cand = list(range(first, yb + 1, c))
            ex = extents(cand)
            if len(cand) <= 7 and all(b[0] - a[1] >= 32 for a, b in zip(ex, ex[1:])):
                years = cand
                break
        for yr in years:
            _xlabel(o, X(float(yr)), str(yr), px0, x1, bot + XLAB)
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
            if ln.shade:
                xa, xb = X(_xdec(s.index[0])), X(_xdec(s.index[-1]))
                o.append(f'<path d="{d} L{xb:.1f},{bot:.1f} L{xa:.1f},{bot:.1f} Z" fill="{ln.color}" '
                         f'fill-opacity="{SHADE_OPACITY}" stroke="none"/>')
            o.append(f'<path d="{d}" fill="none" stroke="{ln.color}" stroke-width="{ln.width * LINE_SCALE:.2f}" '
                     f'stroke-linejoin="round" stroke-linecap="round" style="mix-blend-mode:multiply"/>')
    # end labels (de-overlapped)
    ends = sorted(((Y(ln.s.iloc[-1]), ln) for ln in p.lines if ln.end_label), key=lambda e: e[0])
    placed = []
    for yv, ln in ends:
        yy = yv + 7
        if placed and yy - placed[-1] < 24:
            yy = placed[-1] + 24
        placed.append(yy)
        o.append(_halo(X(_xdec(ln.s.index[-1])) + 10, yy, ln.end_label, 20, 500,
                       ln.color if ln.color != YELLOW else "#C9961A"))
    # latest point: dot only
    for ln in p.lines:
        if ln.latest == "dot" and ln.kind != "bar":
            o.append(f'<circle cx="{X(_xdec(ln.s.index[-1])):.1f}" cy="{Y(ln.s.iloc[-1]):.1f}" r="5" fill="{ln.color}"/>')
    # latest-point label (right of the point)
    for ln, t, v, per, vs in lat:
        xv, yv = X(_xdec(t)), Y(v)
        if ln.kind != "bar":
            o.append(f'<circle cx="{xv:.1f}" cy="{yv:.1f}" r="5" fill="{ln.color}" stroke="{BG}" stroke-width="1.5"/>')
        else:
            yv = Y(v) if abs(Y(v) - Y(0)) > 4 else Y(0)
        yc = max(top + 18, min(bot - 22, yv))
        o.append(_halo(xv + 12, yc - 4, per, 20, 400, INK))
        o.append(_halo(xv + 12, yc + 19, vs, 20, 700, INK))


def hbar_panel(o: list, items: list, x0, x1, top, heading, sub, vfmt, highlight=(), height: float | None = None) -> float:
    """Horizontal bars, one row per (label, value) in the order given: bars extend from an axis line at zero, one bold
    value at each bar's end, category names in a column right of the axis (and of any positive bars). Thick bars that
    almost touch. `height` (optional) is the panel height to fill. Returns the bottom y."""
    label = heading + (f", {sub}" if heading and sub else sub)
    if label:
        o.append(_T(x0, top - LABEL_GAP, label, 20, 400, GREY))
    n = len(items)
    vals = [v for _, v in items]
    neg, pos = min(0.0, min(vals)), max(0.0, max(vals))
    name_w = max(_tw(nm, 20, 500 if nm in highlight else 400) for nm, _ in items)
    val_w = max(_tw(vfmt(v), 20, 700) for v in vals) + 10
    if neg < 0:
        names_x = x1 - name_w
        b0 = x0 + val_w
        b1 = names_x - 12 - (val_w if pos > 0 else 0)
        scale = (b1 - b0) / ((pos - neg) or 1)
        ax = b0 + (-neg) * scale
    else:
        # all values >= 0: the mirror of the pending-sales panel -- names in a column at the left margin, the axis
        # line right of them, bars extending right with the value at each bar's end (no gap between value and name)
        names_x = x0
        ax = x0 + name_w + 12
        scale = (x1 - val_w - ax) / (pos or 1)
    if height:
        bh = min(80.0, height / n * 0.86)
        stepy = (height - bh) / max(n - 1, 1)
    else:
        bh, stepy = (28.0, 33.0) if n > 6 else (60.0, 70.0)
    for i, (name, v) in enumerate(items):
        y = top + i * stepy
        col = BLUE  # owner, 21 Oct 2026: benchmark bar blue like the rest; the bold name marks it
        w = abs(v) * scale
        o.append(f'<rect x="{(ax - w) if v < 0 else ax:.1f}" y="{y:.1f}" width="{max(w, 1):.1f}" height="{bh:.1f}" '
                 f'fill="{col}"/>')
        ty = y + bh / 2 + 7
        if v < 0:
            o.append(_T(ax - w - 10, ty, vfmt(v), 20, 700, INK, "end"))
        else:
            o.append(_T(ax + w + 10, ty, vfmt(v), 20, 700, INK))
        nx = x0 if neg >= 0 else (names_x if pos > 0 else max(names_x, ax + 12))
        o.append(_T(nx, ty, name, 20, 500 if name in highlight else 400, INK))
    yb = top + (n - 1) * stepy + bh
    o.append(f'<line x1="{ax:.1f}" y1="{top:.1f}" x2="{ax:.1f}" y2="{yb:.1f}" stroke="{INK}" stroke-width="1.2"/>')
    return yb


@dataclass
class HBars:
    """A horizontal-bar panel placed in a Row beside a line panel (the pending-sales layout): same width as its
    neighbour, bars running from the panel top down to the neighbour's x-label baseline."""
    items: list                   # (name, value), drawn in the order given
    heading: str = ""
    sub: str = ""
    vfmt: Callable = None
    highlight: tuple = ()


# ── bubble strip (owner, 21 Oct 2026, on the Case-Shiller metro bars: "why don't you make them bubbles on a
# left-to-right axis where the bubble size is population? You can even color them regionally, like Northeast, South,
# etc."). One row of circles on a horizontal value axis; area proportional to 2024 metro population; fill = Census
# region; dodged vertically so bubbles do not sit on top of one another; labels on the notable bubbles only (largest,
# extremes) and a vertical ink line for the national benchmark. Leader lines are black circular arcs ending on the
# bubble edge (house style).
GEO_JSON = Path(__file__).resolve().parent / "geo" / "us_metro_map_geo.json"
CENSUS_REGION = {**{s: "Northeast" for s in "CT ME MA NH RI VT NJ NY PA".split()},
                 **{s: "Midwest" for s in "IL IN MI OH WI IA KS MN MO NE ND SD".split()},
                 **{s: "South" for s in "DE FL GA MD NC SC VA DC WV AL KY MS TN AR LA OK TX".split()},
                 **{s: "West" for s in "AZ CO ID MT NV NM UT WY AK CA HI OR WA".split()}}
# four strong palette colours; red (the loudest) goes to the region with the fewest large metros
REGION_COL = {"Northeast": BLUE, "Midwest": RED, "South": GREEN, "West": YELLOW}
STRIP_DROPPED: list = []          # (chart, metro, reason) for metros left off a strip (no population): reported
SHADE_OPACITY = 0.13
_CBSA: dict | None = None


def cbsa_info(code: str) -> dict | None:
    """{name, pop, state, region} for a CBSA code from the geometry JSON (2024 population summed over 2023
    delineation counties); None when the code is missing or has no population."""
    global _CBSA
    if _CBSA is None:
        _CBSA = {c["cbsa"]: c for c in json.loads(GEO_JSON.read_text())["cbsa"]}
    c = _CBSA.get(str(code).zfill(5))
    if not c or not c.get("pop"):
        return None
    st = c["name"].rpartition(", ")[2].split("-")[0][:2]
    return {"name": c["name"], "pop": float(c["pop"]), "state": st, "region": CENSUS_REGION.get(st)}


@dataclass
class Strip:
    """A bubble-strip panel (full row width). items: (name, value, population, region)."""
    items: list
    heading: str = ""
    sub: str = ""
    bench: tuple | None = None    # (label, value): vertical ink line + label, e.g. ("National +1.9%", 1.9)
    suffix: str = "%"             # unit on the right-most axis label only
    n_big: int = 3                # label the largest n ...
    n_ext: int = 1                # ... and the n highest and n lowest values
    max_labels: int = 10
    rmax: float = 30.0


def _arc_geom(px, py, cx, cy, r, sweep_deg=40.0):
    """End point on the circle edge, arc radius, SVG sweep flag and 13 points along the arc from (px, py)."""
    d = math.hypot(px - cx, py - cy) or 1.0
    ex, ey = cx + r * (px - cx) / d, cy + r * (py - cy) / d
    chord = math.hypot(ex - px, ey - py) or 1.0
    rad = chord / (2 * math.sin(math.radians(sweep_deg) / 2))
    sweep = 1 if ex > px else 0
    ux, uy = (ex - px) / chord, (ey - py) / chord
    nx, ny = (-uy, ux) if sweep else (uy, -ux)          # towards the arc's centre (screen coordinates, y down)
    h = math.sqrt(max(rad * rad - chord * chord / 4, 0))
    ox, oy = (px + ex) / 2 + nx * h, (py + ey) / 2 + ny * h
    a0, a1 = math.atan2(py - oy, px - ox), math.atan2(ey - oy, ex - ox)
    da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    pts = [(ox + rad * math.cos(a0 + da * k / 12), oy + rad * math.sin(a0 + da * k / 12)) for k in range(13)]
    return ex, ey, rad, sweep, pts


def _arc(px, py, cx, cy, r, sweep_deg=40.0) -> str:
    """A circular-arc leader from (px, py) to the edge of the circle (cx, cy, r), black, thin (house style)."""
    ex, ey, rad, sweep, _ = _arc_geom(px, py, cx, cy, r, sweep_deg)
    return (f'<path d="M{px:.1f},{py:.1f} A{rad:.1f},{rad:.1f} 0 0 {sweep} {ex:.1f},{ey:.1f}" fill="none" '
            f'stroke="{INK}" stroke-width="1"/>')


def bubble_strip(o: list, st: Strip, x0: float, x1: float, ytop: float, ybot: float) -> float:
    """Draw the strip in the band ytop..ybot (the row's full height, like a Row of panels): label line (baseline
    ytop+20) with the region legend right-aligned on it, a label lane, the swarm, a lower label lane, axis numbers
    (baseline ybot-10). Returns ybot."""
    label = st.heading + (f", {st.sub}" if st.heading and st.sub else st.sub)
    regs = [r for r in REGION_COL if any(it[3] == r for it in st.items)]
    leg_w = sum(16 + 8 + _tw(r, 20) for r in regs) + 18 * max(len(regs) - 1, 0)
    lab_y = ytop + 20
    two_lines = label and _tw(label, 20) + 28 + leg_w > x1 - x0
    if label:
        o.append(_T(x0, lab_y, label, 20, 400, GREY))
    lx = (x0 if two_lines else x1 - leg_w)
    ly = lab_y + (30 if two_lines else 0)
    for r in regs:
        o.append(f'<circle cx="{lx + 8:.1f}" cy="{ly - 7:.1f}" r="8" fill="{REGION_COL[r]}" fill-opacity="0.85"/>')
        o.append(_T(lx + 24, ly, r, 20, 400, INK))
        lx += 24 + _tw(r, 20) + 18
    lane_t = ly + 36                                  # benchmark label baseline
    zt = lane_t + 8                                   # swarm zone: from under the benchmark label ...
    zb = ybot - 36                                    # ... to just above the axis numbers
    yc = (zt + zb) / 2
    half = (zb - zt) / 2
    vals = [it[1] for it in st.items] + ([st.bench[1]] if st.bench else [])
    lo, hi = min(vals), max(vals)
    ticks = nice_ticks(lo, hi, 6, zero=lo < 0 < hi)
    ticks = [t for t in ticks if lo - (hi - lo) * 0.12 <= t <= hi + (hi - lo) * 0.12] or ticks
    dlo, dhi = min(ticks[0], lo), max(ticks[-1], hi)
    pmax = max(it[2] for it in st.items)
    R = lambda p: max(5.0, st.rmax * math.sqrt(p / pmax))
    pad = st.rmax + 2
    px0, px1 = x0 + pad, x1 - pad
    X = lambda v: px0 + (v - dlo) / ((dhi - dlo) or 1) * (px1 - px0)
    # grid + axis numbers (unit on the right-most number only)
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for i, tv in enumerate(ticks):
        xv = X(tv)
        o.append(f'<line x1="{xv:.1f}" y1="{zt - 4:.1f}" x2="{xv:.1f}" y2="{zb + 4:.1f}" stroke="{GRID}" stroke-width="1"/>')
        lb = fmt_num(tv, step) + (st.suffix if i == len(ticks) - 1 else "")
        _xlabel(o, xv, lb, x0, x1, ybot - 10)
    if lo < 0 < hi:
        o.append(f'<line x1="{X(0):.1f}" y1="{zt - 4:.1f}" x2="{X(0):.1f}" y2="{zb + 4:.1f}" stroke="{GREY}" '
                 f'stroke-width="1"/>')
    # dodge: largest first at the centre line, then each bubble at the smallest vertical offset clear of the others
    placed = []                                       # [cx, cy, r, item]
    for it in sorted(st.items, key=lambda it: -it[2]):
        cx, r = X(it[1]), R(it[2])
        lim = max(half - r, 0)
        best = None
        for k in range(0, int(lim) + 1):
            for dy in ((k, -k) if k else (0,)):
                cy = yc + dy
                ov = max([0.0] + [(r + q[2] + 1.5) - math.hypot(cx - q[0], cy - q[1]) for q in placed])
                if best is None or ov < best[0] - 1e-6:
                    best = (ov, cy)
                if ov <= 0:
                    break
            if best[0] <= 0:
                break
        placed.append([cx, best[1], r, it])
    for cx, cy, r, it in placed:
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{REGION_COL.get(it[3], GREY)}" '
                 f'fill-opacity="0.78"/>')
    boxes = []
    frame = (x0, ly + 6, x1, ybot - 32)
    if st.bench:
        bx = X(st.bench[1])
        o.append(f'<line x1="{bx:.1f}" y1="{lane_t - 18:.1f}" x2="{bx:.1f}" y2="{zb + 6:.1f}" stroke="{INK}" '
                 f'stroke-width="1.6"/>')
        w = _tw(st.bench[0], 20, 500)
        ax, anchor = (bx + 6, "start") if bx + 6 + w <= x1 else (bx - 6, "end")
        bx0 = ax if anchor == "start" else ax - w
        o.append(_halo(ax, lane_t, st.bench[0], 20, 500, INK, anchor))
        boxes.append((bx0 - 2, lane_t - 17, bx0 + w + 2, lane_t + 5))
    # label candidates in priority order
    by_pop = sorted(placed, key=lambda q: -q[3][2])
    by_val = sorted(placed, key=lambda q: -q[3][1])
    order, seen = [], set()
    for q in by_pop[:st.n_big] + by_val[:st.n_ext] + by_val[::-1][:st.n_ext] + by_pop[st.n_big:]:
        if id(q) not in seen:
            seen.add(id(q))
            order.append(q)

    def clear(b):
        if b[0] < frame[0] or b[2] > frame[2] or b[1] < frame[1] or b[3] > frame[3]:
            return False
        if any(not (b[2] + 3 <= q[0] or q[2] + 3 <= b[0] or b[3] + 2 <= q[1] or q[3] + 2 <= b[1]) for q in boxes):
            return False
        for qx, qy, qr, _ in placed:
            nx, ny = min(max(qx, b[0]), b[2]), min(max(qy, b[1]), b[3])
            if (nx - qx) ** 2 + (ny - qy) ** 2 < (qr + 1) ** 2:
                return False
        return True
    def arc_hits(pts, skip):
        """Does the arc (sampled points) pass through a bubble other than `skip`, or through a placed label?"""
        for ax_, ay_ in pts[1:-1]:
            for qx, qy, qr, qi in placed:
                if qi is not skip and math.hypot(ax_ - qx, ay_ - qy) < qr + 1:
                    return True
            for b in boxes:
                if b[0] <= ax_ <= b[2] and b[1] <= ay_ <= b[3]:
                    return True
        return False

    n_lab = 0
    for cx, cy, r, it in order:
        if n_lab >= st.max_labels:
            break
        txt = it[0]
        w = _tw(txt, 20, 500)
        d = r * 0.72
        # (anchor, x, baseline, leader?) -- beside the bubble first (no leader), then a little further out with a
        # short arc leader; the leader may not cross another bubble or label
        opts = [("middle", cx, cy - r - 7, False), ("middle", cx, cy + r + 21, False),
                ("start", cx + r + 6, cy + 7, False), ("end", cx - r - 6, cy + 7, False),
                ("start", cx + d + 4, cy - d - 4, False), ("end", cx - d - 4, cy - d - 4, False),
                ("start", cx + d + 4, cy + d + 18, False), ("end", cx - d - 4, cy + d + 18, False)]
        for dist in (26, 44, 62, 85, 110):
            for dx, dy in ((0, -1), (0, 1), (0.7, -0.7), (-0.7, -0.7), (0.7, 0.7), (-0.7, 0.7), (1, 0), (-1, 0)):
                lx_, ly_ = cx + dx * (r + dist), cy + dy * (r + dist)
                anchor = "middle" if dx == 0 else ("start" if dx > 0 else "end")
                opts.append((anchor, lx_, ly_ + (7 if dy == 0 else -2 if dy < 0 else 16), True))
        # last resort: the strip's top row (beside the benchmark label) or bottom row, shifted sideways
        for dx in range(0, 210, 15):
            for row in ((lane_t, ybot - 37) if cy <= yc else (ybot - 37, lane_t)):
                for sx in ((dx, -dx) if dx else (0,)):
                    opts.append(("middle", cx + sx, row, True))
        done = False
        for anchor, tx, ty, lead in opts:
            bx0 = tx if anchor == "start" else tx - w if anchor == "end" else tx - w / 2
            b = (bx0, ty - 17, bx0 + w, ty + 5)
            if not clear(b):
                continue
            if lead:
                # from the point of the label box nearest the bubble to the bubble edge
                pxl, pyl = min(max(cx, b[0] + 3), b[2] - 3), min(max(cy, b[1] - 2), b[3] + 2)
                gap = math.hypot(pxl - cx, pyl - cy) - r
                if gap < 8 or gap > 120:
                    continue
                if arc_hits(_arc_geom(pxl, pyl, cx, cy, r)[4], it):
                    continue
                o.append(_arc(pxl, pyl, cx, cy, r))
            boxes.append(b)
            o.append(_halo(tx, ty, txt, 20, 500, INK, anchor))
            n_lab += 1
            done = True
            break
        if not done:
            logger.info(f"strip label skipped (no room): {txt}")
    return ybot


@dataclass
class Row:
    panels: list                  # Panel objects, laid out side by side at identical width and height
    height: int = 360             # label line to x labels, inclusive
    widths: list | None = None    # fractions (default: equal)


def compose(title: str, rows: list, source_lines: list, extra: Callable | None = None, extra_height: int = 0) -> tuple[str, int]:
    """Title at the left margin; rows of panels (each row: label at y+20, plot y+56 .. y+height-40, x labels 30 below);
    `extra(o, y)` draws more below (a bar panel: label baseline at y+20, i.e. top = y+56) and returns its bottom;
    then the footer. Returns (svg, height)."""
    size = 34
    while size > 26 and _tw(title, size, 700, -0.7) > GR - GL:
        size -= 2
    o = [_T(GL, 54, title, size, 700, INK, extra=' letter-spacing="-0.7"')]
    y = 84
    for r in rows:
        top, bot = y + 56, y + r.height - 40
        n = len(r.panels)
        fr = r.widths or [1 / n] * n
        avail = (GR - GL) - COLGAP * (n - 1)
        x = GL
        for p, f in zip(r.panels, fr):
            w = avail * f
            if isinstance(p, HBars):
                hbar_panel(o, p.items, x, x + w, top, p.heading, p.sub, p.vfmt or _signed, p.highlight,
                           height=bot + XLAB - top)
            elif isinstance(p, Strip):
                bubble_strip(o, p, x, x + w, y, y + r.height)
            else:
                draw_panel(o, p, x, x + w, top, bot)
            x += w + COLGAP
        y += r.height + ROWGAP
    y -= ROWGAP if rows else 0
    if extra:
        y = extra(o, y + ROWGAP if rows else y)
    # footer (owner, 21 Oct 2026: "the source lines should not be below the logo. They should be at the same
    # horizontal level, making sure not to let the two overlap"): source bottom-left, logo bottom-right on one band,
    # bottom edges aligned; the source is wrapped to the width left of the logo.
    src_w = (GR - LOGO_W - 30) - GL
    words, lines, curl = " ".join(source_lines).split(), [], ""
    for wd in words:
        if curl and _tw(f"{curl} {wd}", 20) > src_w:
            lines.append(curl)
            curl = wd
        else:
            curl = f"{curl} {wd}".strip()
    if curl:
        lines.append(curl)
    first_base = y + SRC_GAP
    last_base = first_base + 24 * (len(lines) - 1)
    o.append(_logo_tag(last_base))
    spans = "".join(f'<tspan x="{GL}" dy="{0 if i == 0 else 24}">{_esc(t)}</tspan>' for i, t in enumerate(lines))
    o.append(f'<text x="{GL}" y="{first_base:.1f}" font-family="{FAM}" font-size="20" font-weight="400" fill="{GREY}">'
             f'{spans}</text>')
    H = int(last_base + 30)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}"><rect x="0" y="0" width="{W}" height="{H}" fill="{BG}"/>')
    return head + "".join(o) + "</svg>", H


_LOGO_URI = None
LOGO_W = 130                      # logo width on the 960 canvas; height from the PNG's aspect ratio
LOGO_H = LOGO_W * 0.3195


def _logo_tag(source_base: float) -> str:
    """The Home Economics logo at the right margin, its bottom edge level with the bottom of the source line whose
    (last) baseline is `source_base` (owner, 21 Oct 2026: logo on every chart, on the source band, never overlapping)."""
    return (f'<image xlink:href="{_logo_uri()}" x="{GR - LOGO_W}" y="{source_base + 5 - LOGO_H:.1f}" width="{LOGO_W}" '
            f'height="{LOGO_H:.1f}" opacity="0.6"/>')


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


def _one(s, start):
    """One series, a dot on the latest point and nothing else on the line (owner, 21 Oct 2026: "We don't need the
    3-month average since it's so similar to the index. Get rid of all the ... annotations")."""
    return [Line(since(s, start), BLUE, 2.5, latest="dot")]


def _bars(s, start):
    """Monthly (or quarterly) changes as blue bars: no average line, no value callout."""
    return [Line(since(s, start), BLUE, kind="bar")]


def _level(s, start):
    """A level series (starts, sales, rates, index levels): one line with light shading beneath it and a dot on the
    latest point (owner, 21 Oct 2026: "maybe we should have some shading beneath it")."""
    return [Line(since(s, start), BLUE, 2.5, latest="dot", shade=True)]


# Size rule (owner, 21 Oct 2026: "All of these need to be small, square-sized. They can't be super long. You made so
# many of these charts very, very tall."): 960 wide, 600-720 tall, at most two rows, side-by-side panels preferred.
ROW_ONE = 436                     # one row of panels -> 600 px canvas (the pending-sales reference)
ROW_TOP, ROW_BOT = 260, 240       # two rows (main panel over two small ones) -> 704 px
STRIP_H = 250                     # a bubble-strip row under a 250 px panel row -> 704 px


def _strip_items(chart: str, rows: list) -> list:
    """(name, value, CBSA code) -> Strip items (name, value, population, region); metros without a population or
    region in the geometry JSON are dropped and recorded in STRIP_DROPPED (never estimated)."""
    out = []
    for name, v, code in rows:
        if v != v:
            continue
        info = cbsa_info(code) if code else None
        if not info or not info["region"]:
            STRIP_DROPPED.append((chart, name, f"no 2024 population/region for CBSA {code}"))
            logger.warning(f"{chart}: {name} (CBSA {code}) has no population in {GEO_JSON.name}; left off the strip")
            continue
        out.append((name, float(v), info["pop"], info["region"]))
    return out


# ═══════════════════════════════════════════════════════════════════════
# builders: each returns dict(svg, H, latest (Timestamp), title, caption, source)
# Wave 1 restyled to the pending-sales reference (owner, 21 Oct 2026: "let's fix the Wave 1 release charts so they're
# uncluttered and beautiful"): one series per panel, a dot on the latest point, no moving averages, legends or value
# callouts, at most two panels per row, short source lines ("Source: X"; the logo carries the brand).
# ═══════════════════════════════════════════════════════════════════════
def b_housing_starts():
    h, sf, mf, pm = fred("HOUST"), fred("HOUST1F"), fred("HOUST5F"), fred("PERMIT")
    t = h.index[-1]
    ch = cur(pchg(h), h)
    rows = [
        Row([Panel(_level(h, "2016-01"), "Total starts", "thousands, annual rate", show_key=False)], ROW_TOP),
        # owner, 21 Oct 2026: "Just take out their regional charts" -- no regional row; two panels, not three
        Row([Panel(_level(sf, "2016-01"), "Single-family", show_key=False),
             Panel(_level(mf, "2016-01"), "Buildings with 5+ units", show_key=False)], ROW_BOT),
    ]
    yy = cur(yoy(h), h)
    title = f"Housing starts {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Starts {_verb(ch)} {abs(ch):.1f}% to a {h.iloc[-1]:,.0f}k annual rate in {month_year(t)} ({_signed(yy)} "
           f"from a year earlier); single-family {sf.iloc[-1]:,.0f}k, 5+ units {mf.iloc[-1]:,.0f}k, permits {pm.iloc[-1]:,.0f}k.")
    src = "Source: Census Bureau and HUD, via FRED. Seasonally adjusted annual rates."
    return rows, None, 0, t, title, cap, src


def b_new_home_sales():
    s, sup, px = fred("HSN1F"), fred("MSACSR"), fred("MSPNHSUS")
    t = s.index[-1]
    ch = cur(pchg(s), s)
    rows = [
        Row([Panel(_level(s, "2016-01"), "New single-family homes sold", "thousands, annual rate", show_key=False)],
            ROW_TOP),
        Row([Panel(_level(sup, "2016-01"), "Months’ supply", show_key=False),
             Panel(_level(px / 1000, "2016-01"), "Median price", yprefix="$", ysuffix="k", show_key=False)], ROW_BOT),
    ]
    title = f"New home sales {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"New single-family home sales were {s.iloc[-1]:,.0f}k at an annual rate in {month_year(t)} "
           f"({_signed(cur(yoy(s), s))} from a year earlier), with {sup.iloc[-1]:.1f} months' supply and a median "
           f"price of ${px.iloc[-1]:,.0f}.")
    src = "Source: Census Bureau and HUD, via FRED. Price not seasonally adjusted."
    return rows, None, 0, t, title, cap, src


# (name, FRED series, CBSA code for the bubble size and region; the Case-Shiller metro areas are close to, not
# identical with, the CBSA of the same name)
CS_METROS = [("New York", "NYXRNSA", "35620"), ("Los Angeles", "LXXRNSA", "31080"), ("Chicago", "CHXRNSA", "16980"),
             ("Dallas", "DAXRNSA", "19100"), ("Miami", "MIXRNSA", "33100"), ("Atlanta", "ATXRNSA", "12060"),
             ("Washington", "WDXRNSA", "47900"), ("Boston", "BOXRNSA", "14460"), ("Phoenix", "PHXRNSA", "38060"),
             ("Seattle", "SEXRNSA", "42660"), ("San Francisco", "SFXRNSA", "41860"), ("Denver", "DNXRNSA", "19740"),
             ("Tampa", "TPXRNSA", "45300")]


def b_case_shiller():
    """Owner, 21 Oct 2026: "it takes up way too much space ... make them bubbles on a left-to-right axis where the
    bubble size is population ... color them regionally ... I think a column chart would look a lot nicer there."
    Top: the national change from a year earlier as columns; below: the metros as a bubble strip."""
    nat, c20 = fred("CSUSHPINSA"), fred("SPCS20RNSA")
    t = nat.index[-1]
    yn, y20 = yoy(nat), yoy(c20)
    vals = []
    for name, sid, cb in CS_METROS:
        y = yoy(fred(sid))
        if y.index[-1] == t:
            vals.append((name, float(y.iloc[-1]), cb))
    vn = cur(yn, nat)
    month = month_year(t)
    rows = [Row([Panel(_bars(yn, "2016-01"), "National index, change from a year earlier", ysuffix="%", zero=True,
                       show_key=False)], STRIP_H),
            Row([Strip(_strip_items("case_shiller", vals), f"{month} by metro", "sized by population",
                       bench=(f"National {_signed(vn)}", vn))], STRIP_H)]
    items = sorted([(n, v) for n, v, _ in vals], key=lambda kv: -kv[1])
    extra = None
    title = f"National home prices {_verb(cur(yn, nat), 'rose', 'fell', 'were flat')} {abs(cur(yn, nat)):.1f}% over the year to {MONTHS[t.month - 1]}"
    if _verb(cur(yn, nat)) == "was unchanged":
        title = f"National home prices were flat over the year to {MONTHS[t.month - 1]}"
    best, worst = items[0], items[-1]
    cap = (f"The S&P Cotality Case-Shiller national index was {_signed(cur(yn, nat))} from a year earlier in {month} and the "
           f"20-city composite {_signed(cur(y20, c20))}; among the metros shown, {best[0]} was strongest ({_signed(best[1])}) "
           f"and {worst[0]} weakest ({_signed(worst[1])}).")
    src = "Source: S&P Cotality Case-Shiller indices via FRED; Census population."
    return rows, extra, 0, t, title, cap, src


def b_fhfa_hpi():
    s = fred("HPIPONM226S")
    t = s.index[-1]
    y, m = yoy(s), pchg(s)
    rows = [Row([Panel(_bars(y, "2016-01"), "Change from a year earlier", ysuffix="%", zero=True, show_key=False),
                 Panel(_bars(m, "2023-01"), "Change from the previous month", ysuffix="%", zero=True,
                       show_key=False)], ROW_ONE)]
    title = f"FHFA house prices {_verb(cur(y, s))} {abs(cur(y, s)):.1f}% over the year to {MONTHS[t.month - 1]}"
    cap = (f"The FHFA purchase-only index was {_signed(cur(y, s))} from a year earlier in {month_year(t)} and "
           f"{_signed(cur(m, s), 2)} from the month before (seasonally adjusted).")
    src = "Source: FHFA purchase-only house price index, seasonally adjusted, via FRED."
    return rows, None, 0, t, title, cap, src


def b_payrolls():
    p, u, c = fred("PAYEMS"), fred("UNRATE"), fred("USCONS")
    t = p.index[-1]
    d = diff(p)
    dc = diff(c)
    rows = [Row([Panel(_bars(d, "2023-01"), "Nonfarm payrolls", "change from the previous month, thousands", zero=True,
                       show_key=False)], ROW_TOP),
            Row([Panel(_level(u, "2016-01"), "Unemployment rate", ysuffix="%", show_key=False),
                 Panel(_level(c / 1000, "2016-01"), "Construction jobs", "millions", show_key=False)], ROW_BOT)]
    v = cur(d, p)
    title = (f"Employers added {v * 1000:,.0f} jobs in {MONTHS[t.month - 1]}" if v >= 0 else
             f"Payrolls fell by {abs(v) * 1000:,.0f} jobs in {MONTHS[t.month - 1]}")
    cap = (f"Nonfarm payrolls changed by {v:+,.0f}k in {month_year(t)} (three-month average {ma(d, 3).iloc[-1]:+,.0f}k); "
           f"unemployment {u.iloc[-1]:.1f}%; construction employment {cur(dc, c):+,.0f}k on the month.")
    src = "Source: Bureau of Labor Statistics, via FRED. Seasonally adjusted."
    return rows, None, 0, t, title, cap, src


def b_cpi_shelter():
    """One row of two panels (the pending-sales layout): rent's change from a year earlier, and its change from the
    previous month. All items and owners' equivalent rent are in the caption, not on the chart."""
    hd, rent, oer = fred("CPIAUCSL"), fred("CUSR0000SEHA"), fred("CUSR0000SEHC")
    t = hd.index[-1]
    yh, yr, yo = yoy(hd), yoy(rent), yoy(oer)
    rows = [Row([Panel(_bars(yr, "2016-01"), "Rent, change from a year earlier", ysuffix="%", zero=True, show_key=False),
                 Panel(_bars(pchg(rent), "2023-01"), "Rent, change from the previous month", ysuffix="%", zero=True,
                       show_key=False)], ROW_ONE)]
    title = f"CPI rent {_verb(cur(yr, rent))} {abs(cur(yr, rent)):.1f}% over the year to {MONTHS[t.month - 1]}"
    cap = (f"In {month_year(t)} rent of primary residence was {_signed(cur(yr, rent))} from a year earlier "
           f"({_signed(cur(pchg(rent), rent), 2)} on the month), owners' equivalent rent {_signed(cur(yo, oer))} and "
           f"all items {_signed(cur(yh, hd))}.")
    src = "Source: Bureau of Labor Statistics, via FRED. Seasonally adjusted."
    return rows, None, 0, t, title, cap, src


def b_mortgage_rates():
    r, g = fred("MORTGAGE30US"), fred("DGS10")
    t = r.index[-1]
    gw = g.resample("W-THU").mean().reindex(r.index).dropna()
    spread = (r.reindex(gw.index) - gw).dropna()
    rows = [Row([Panel(_level(r, "2022-01"), "30-year fixed mortgage rate", ysuffix="%", show_key=False),
                 Panel(_level(spread, "2022-01"), "Spread over the 10-year Treasury", "points",
                       show_key=False)], ROW_ONE)]
    title = f"The 30-year mortgage rate was {r.iloc[-1]:.2f}% on {MONTHS[t.month - 1]} {t.day}"
    wk = r.iloc[-1] - r.iloc[-2]
    cap = (f"Freddie Mac's 30-year fixed rate averaged {r.iloc[-1]:.2f}% in the week to {MONTHS[t.month - 1]} {t.day}, "
           f"{'unchanged' if round(wk, 2) == 0 else _signed(wk, 2, ' points')} on the week; the spread over the 10-year Treasury was {spread.iloc[-1]:.2f} points.")
    src = "Source: Freddie Mac and Federal Reserve, via FRED. Weekly."
    return rows, None, 0, t, title, cap, src


def b_construction_spending():
    s = fred("TLRESCONS") / 1000
    t = s.index[-1]
    ch, y = cur(pchg(s), s), yoy(s)
    rows = [Row([Panel(_level(s, "2016-01"), "Residential spending", "$bn, annual rate", yprefix="$",
                       show_key=False),
                 Panel(_bars(y, "2016-01"), "Change from a year earlier", ysuffix="%", zero=True, show_key=False)],
                ROW_ONE)]
    title = f"Residential construction spending {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Residential construction spending was ${s.iloc[-1]:,.0f} billion at an annual rate in {month_year(t)}, "
           f"{_signed(ch)} on the month and {_signed(cur(y, s))} on the year (not adjusted for inflation).")
    src = "Source: Census Bureau, via FRED. Seasonally adjusted, current dollars."
    return rows, None, 0, t, title, cap, src


def b_jolts():
    jo, q = fred("JTSJOL") / 1000, fred("JTSQUR")
    t = jo.index[-1]
    rows = [Row([Panel(_level(jo, "2016-01"), "Job openings", "millions", show_key=False),
                 Panel(_level(q, "2016-01"), "Quits rate", "% of employment", ysuffix="%", show_key=False)], ROW_ONE)]
    ch = cur(diff(jo), jo)
    title = f"Job openings {_verb(ch, 'rose', 'fell', 'held')} to {jo.iloc[-1]:.1f} million in {MONTHS[t.month - 1]}"
    cap = (f"Job openings were {jo.iloc[-1]:.2f} million in {month_year(t)} ({ch * 1000:+,.0f}k on the month); the "
           f"quits rate was {q.iloc[-1]:.1f}%.")
    src = "Source: Bureau of Labor Statistics, JOLTS, via FRED. Seasonally adjusted."
    return rows, None, 0, t, title, cap, src


def b_jobless_claims():
    s = fred("ICSA") / 1000
    t = s.index[-1]
    rows = [Row([Panel(_level(s, "2023-01"), "Initial jobless claims", "thousands, weekly", show_key=False)], ROW_ONE)]
    title = f"Initial jobless claims were {s.iloc[-1] * 1000:,.0f} in the week to {MONTHS[t.month - 1]} {t.day}"
    cap = (f"Initial claims for unemployment insurance were {s.iloc[-1]:,.0f}k in the week to {MONTHS[t.month - 1]} "
           f"{t.day} (4-week average {ma(s, 4).iloc[-1]:,.0f}k).")
    src = "Source: Department of Labor, via FRED. Seasonally adjusted."
    return rows, None, 0, t, title, cap, src


def b_hvs():
    ho, rv, hv = fred("RHORUSQ156N"), fred("RRVRUSQ156N"), fred("RHVRUSQ156N")
    t = ho.index[-1]
    rows = [Row([Panel(_level(ho, "2010-01"), "Homeownership rate", ysuffix="%", show_key=False)], ROW_TOP),
            Row([Panel(_level(rv, "2010-01"), "Rental vacancy rate", ysuffix="%", show_key=False),
                 Panel(_level(hv, "2010-01"), "Homeowner vacancy rate", ysuffix="%", show_key=False)], ROW_BOT)]
    q = QNAMES[(t.month - 1) // 3]
    title = f"The homeownership rate was {ho.iloc[-1]:.1f}% in the {q} quarter"
    cap = (f"In the {q} quarter of {t.year} the homeownership rate was {ho.iloc[-1]:.1f}%, the rental vacancy rate "
           f"{rv.iloc[-1]:.1f}% and the homeowner vacancy rate {hv.iloc[-1]:.1f}%.")
    src = "Source: Census Bureau, via FRED. Not seasonally adjusted."
    return rows, None, 0, t, title, cap, src


# ═══════════════════════════════════════════════════════════════════════
# wave 2 (21 Oct 2026): sources that are not (fully) on FRED, stored in the data lake
#   NAR pending home sales  Data/NAR/pending_home_sales.parquet  (PDF from NAR's research page, upserted; revisions win)
#   NAR existing-home sales Data/NAR/existing_home_sales.parquet (FRED's 13-month window + NAR overview PDFs, accumulated)
#   Apartment List          Data/ApartmentList/{rent_estimates,vacancy_index}.parquet (whole history each vintage)
#   NAHB/Wells Fargo HMI    Data/NAHB/hmi.parquet (t2 + t3 spreadsheets, whole history each vintage)
#   Freddie Mac HPI         Data/Price/FreddieMac/fmhpi_master_file.parquet (master CSV, refreshed on Last-Modified)
# Each updater runs at most once per process, writes ZSTD parquet (ROW_GROUP_SIZE 100000) with a <stem>.source.json
# sidecar, and falls back to the lake copy (with a warning) when the source cannot be reached.
# Owner's taste (21 Oct 2026, on the first pending-sales chart: "crowded and gross and overlabeled"): one line per
# series, a dot on the latest point and no value callouts, no moving averages or legends unless they carry the
# point, no cramped small multiples, short source lines.
# ═══════════════════════════════════════════════════════════════════════
DATA = Path(os.environ.get("NOON_DATA_LAKE", "/home/aziz/OVH/Data"))
NAR_DIR, AL_DIR, NAHB_DIR, FM_DIR = DATA / "NAR", DATA / "ApartmentList", DATA / "NAHB", DATA / "Price" / "FreddieMac"
MND_PHS = Path("/home/aziz/OVH/2026_10_01_MortgageRates/data/pending_home_sales.parquet")   # pre-NAR seed (secondary)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0 Safari/537.36"}
_DONE: dict[str, object] = {}


def _http(url: str, method: str = "GET", cookies: dict | None = None, timeout: int = 90, tries: int = 3):
    import requests
    last = None
    for attempt in range(tries):
        try:
            r = requests.request(method, url, headers=UA, cookies=cookies, timeout=timeout, allow_redirects=True)
            if method == "HEAD" or r.status_code == 404:
                return r
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{method} {url}: {last}")


def _sidecar_path(f: Path) -> Path:
    return f.with_name(f.stem + ".source.json")


def _sidecar(f: Path) -> dict:
    try:
        return json.loads(_sidecar_path(f).read_text())
    except (OSError, ValueError):
        return {}


def _write_lake(df, f: Path, order: str, meta: dict) -> None:
    """DataFrame -> parquet (ZSTD, sorted, ROW_GROUP_SIZE 100000), atomically, plus the .source.json sidecar."""
    import duckdb
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp.parquet")
    con = duckdb.connect()
    con.register("lake_df", df)
    con.execute(f"COPY (SELECT * FROM lake_df ORDER BY {order}) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)")
    con.unregister("lake_df")
    tmp.replace(f)
    sc = _sidecar(f)
    sc.update(meta)
    sc.update(retrieved=datetime.now(timezone.utc).isoformat(timespec="seconds"), rows=int(len(df)),
              refreshed_by="pulse/editor/release_charts.py")
    _sidecar_path(f).write_text(json.dumps(sc, indent=1))


def _lake_df(f: Path, sql: str = "SELECT * FROM src"):
    import duckdb
    return duckdb.connect().execute(sql.replace("src", f"'{f}'")).df()


def _ts(month: str):
    import pandas as pd
    return pd.Timestamp(month[:7] + "-01")


def _mseries(df, col="value"):
    """Rows with a 'month' column -> float Series on a month-start index."""
    import pandas as pd
    s = pd.Series(df[col].astype(float).values, index=pd.to_datetime(df["month"].astype(str).str[:7] + "-01"))
    return s.sort_index()


# ── NAR PDFs ────────────────────────────────────────────────────────────
NAR_BASE = "https://www.nar.realtor"
PHS_PAGE = NAR_BASE + "/research-and-statistics/housing-statistics/pending-home-sales"
EHS_PAGE = NAR_BASE + "/research-and-statistics/housing-statistics/existing-home-sales"
_NAR_MON = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "June": 6, "Jul": 7, "July": 7, "Aug": 8,
            "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_NAR_ROW = re.compile(r"^\s*(20\d\d)\s+([A-Z][a-z]+)\s*([rp])?\s+(.*)$")
REGIONS = ["US", "Northeast", "Midwest", "South", "West"]


def _nar_rows(txt: str):
    """(period, flag, [numbers]) for every monthly row of a NAR table (annual and 'vs. last month' rows skipped)."""
    for line in txt.splitlines():
        m = _NAR_ROW.match(line)
        if not m or m.group(2) not in _NAR_MON:
            continue
        try:
            v = [float(x.replace(",", "").replace("$", "")) for x in m.group(4).split()]
        except ValueError:
            continue
        yield f"{m.group(1)}-{_NAR_MON[m.group(2)]:02d}", m.group(3) or "", v


def parse_phs(txt: str) -> list[tuple]:
    """NAR PHS PDF text -> (month, region, sa, nsa, flag); table columns US + 4 regions SA, then the same NSA."""
    return [(per, reg, sa, nsa, fl) for per, fl, v in _nar_rows(txt) if len(v) == 10
            for reg, sa, nsa in zip(REGIONS, v[:5], v[5:])]


def parse_ehs(txt: str) -> tuple[list, list]:
    """NAR EHS overview PDF text -> sales rows (month, 5 SA, 5 NSA, inventory, months' supply, flag) and median price
    rows (month, 5 regions NSA, flag)."""
    sales, price = [], []
    for per, fl, v in _nar_rows(txt):
        if len(v) == 12:
            sales.append((per, *v, fl))
        elif len(v) == 5:
            price.append((per, *v, fl))
    return sales, price


def _nar_released(url: str) -> str:
    """Release date from a NAR file name: phs-MM-YYYY-pending-home-sales-MM-DD-YYYY.pdf / ehs-...-YYYY-MM-DD.pdf."""
    name = url.rsplit("/", 1)[-1]
    m = re.search(r"-(\d{1,2})-(\d{1,2})-(20\d\d)\.pdf$", name)
    if m and "pending-home-sales" in name:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"-(20\d\d)-(\d\d)-(\d\d)\.pdf$", name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _nar_links(page: str, prefix: str) -> list[str]:
    t = _http(page).text
    found = set(re.findall(r'/sites/default/files/20\d\d-\d\d/' + prefix + r'-[^"\'\s>\\]+?\.pdf', t))
    return sorted((NAR_BASE + u for u in found), key=_nar_released)


def _nar_text(url: str) -> str:
    """Download a NAR PDF once into Data/NAR/pdfs and return its `pdftotext -layout` text."""
    import subprocess
    d = NAR_DIR / "pdfs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / url.rsplit("/", 1)[-1]
    if not p.exists():
        r = _http(url)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"{url}: not a PDF (HTTP {r.status_code})")
        p.write_bytes(r.content)
    txt = p.with_suffix(".txt")
    if not txt.exists():
        subprocess.run(["pdftotext", "-layout", str(p), str(txt)], check=True, timeout=120)
    return txt.read_text(errors="replace")


def _phs_guess(last_month: str) -> str | None:
    """The predictable URL of the release after `last_month` (NAR's page unreachable): HEAD each day of the release
    month up to today."""
    y, m = int(last_month[:4]), int(last_month[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)                   # data month
    ry, rm = (y + 1, 1) if m == 12 else (y, m + 1)                 # release month
    today = date.today()
    for d in range(1, 32):
        try:
            if date(ry, rm, d) > today:
                break
        except ValueError:
            break
        u = (f"{NAR_BASE}/sites/default/files/{ry}-{rm:02d}/phs-{m:02d}-{y}-pending-home-sales-"
             f"{rm:02d}-{d:02d}-{ry}.pdf")
        r = _http(u, method="HEAD", tries=1, timeout=20)
        if r.status_code == 200 and "pdf" in r.headers.get("content-type", ""):
            return u
    return None


def update_phs(urls: list | None = None):
    """Fetch the newest NAR PHS PDF (or the given URLs), parse it and upsert Data/NAR/pending_home_sales.parquet
    (month, region, sa, nsa, flag, release, released); the latest release wins for each (month, region)."""
    import pandas as pd
    if urls is None and "phs" in _DONE:
        return _DONE["phs"]
    f = NAR_DIR / "pending_home_sales.parquet"
    old = _lake_df(f) if f.exists() else pd.DataFrame(columns=["month", "region", "sa", "nsa", "flag", "release", "released"])
    if "released" not in old.columns:
        old["released"] = old["release"].map(_nar_released)
    rows = []
    try:
        todo = urls
        if todo is None:
            try:
                todo = [u for u in _nar_links(PHS_PAGE, "phs") if "pending-home-sales" in u][-1:]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"NAR pending-sales page unreachable ({e}); trying the predictable URL")
                todo = []
            if not todo and len(old):
                g = _phs_guess(old["month"].max())
                todo = [g] if g else []
        for u in todo:
            parsed = parse_phs(_nar_text(u))
            if not parsed:
                raise RuntimeError(f"{u}: parsed 0 rows (has NAR changed the PDF layout?)")
            rel, name = _nar_released(u), u.rsplit("/", 1)[-1]
            rows += [dict(month=m, region=r, sa=sa, nsa=nsa, flag=fl, release=name, released=rel)
                     for m, r, sa, nsa, fl in parsed]
    except Exception as e:  # noqa: BLE001
        if not len(old):
            raise
        logger.warning(f"pending home sales: update failed ({e}); using the lake copy")
    df = old
    if rows:
        df = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
        df = (df.sort_values(["released", "release"]).drop_duplicates(["month", "region"], keep="last")
              .reset_index(drop=True))
        key = ["month", "region", "sa", "nsa", "flag", "released"]
        same = len(df) == len(old) and old[key].sort_values(key[:2]).reset_index(drop=True).astype(str).equals(
            df[key].sort_values(key[:2]).reset_index(drop=True).astype(str))
        if not same:
            _write_lake(df[["month", "region", "sa", "nsa", "flag", "release", "released"]], f, "region, month",
                        {"type": "url", "url": PHS_PAGE, "format": "pdf (pdftotext -layout)", "source":
                         "NAR Pending Home Sales Index (SA and NSA, 2001 = 100), US and four regions", "date_columns":
                         ["month"], "latest_release": df["release"][df["released"].idxmax()],
                         "note": "one PDF per release, 13 months each; later releases overwrite revised months"})
            logger.info(f"lake NAR PHS: {len(df)} rows through {df['month'].max()}")
    _DONE["phs"] = df
    return df


def _phs_index():
    """US PHSI (SA) since 2001: NAR's releases, with Mortgage News Daily's copy for months before the first NAR month."""
    import pandas as pd
    nar = update_phs()
    us = nar[nar.region == "US"]
    s = _mseries(us, "sa")
    first = s.index.min()
    if MND_PHS.exists():
        mnd = _lake_df(MND_PHS, "SELECT strftime(date, '%Y-%m') AS month, value FROM src WHERE chart = 'pending-home-sales' "
                                "AND series = 'Pending Home Sales Total' ORDER BY date")
        m = _mseries(mnd)
        s = pd.concat([m[m.index < first], s]).sort_index()
    else:
        logger.warning(f"{MND_PHS} missing: the pending-sales chart starts at the first NAR month")
    s = s[~s.index.duplicated(keep="last")]
    return s, first, nar


def b_pending_home_sales():
    """Owner-approved design (21 Oct 2026, scripts/90_phsi_chart_grid.py, pending_home_sales_v10.png): left, the
    index since 2010 (stroke 3.4) with a dot on the latest month and nothing else on the line; right, a panel of the
    same width: regions' change from the previous month as thick blue bars from an axis line, names right of it,
    one bold value each, running down to the year labels; one short source line. Laid out here (Prebuilt)."""
    import pandas as pd
    full, first_nar, nar = _phs_index()
    series = full[full.index >= "2010-01-01"]
    t, v = series.index[-1], float(series.iloc[-1])
    reg = nar.pivot(index="month", columns="region", values="sa").sort_index()
    last_m = reg.index[-1]
    prev_m = f"{int(last_m[:4]) - (last_m[5:7] == '01')}-{(int(last_m[5:7]) - 2) % 12 + 1:02d}"
    if prev_m not in reg.index:
        raise RuntimeError(f"pending home sales: no {prev_m} row for the region change")
    last, prev = reg.loc[last_m], reg.loc[prev_m]
    regions = sorted([(r, 100 * (last[r] / prev[r] - 1)) for r in REGIONS[1:]], key=lambda kv: kv[1])
    us_prev = float(reg.loc[prev_m, "US"])
    ch = 100 * (float(last["US"]) / us_prev - 1)
    yago = f"{int(last_m[:4]) - 1}{last_m[4:]}"
    yy = 100 * (float(last["US"]) / float(reg.loc[yago, "US"]) - 1) if yago in reg.index else float("nan")
    record = v <= float(full.min()) + 1e-9
    mon, pmon = MONTHS[t.month - 1], MONTHS[_ts(prev_m).month - 1]
    if record:
        title = "Pending home sales fall to a record low" if ch < 0 else "Pending home sales hold at a record low"
    else:
        title = (f"Pending home sales {'rise' if ch > 0.05 else 'fall' if ch < -0.05 else 'hold steady'}"
                 + (f" {abs(ch):.1f}%" if abs(ch) > 0.05 else "") + f" in {mon}")
    rec_txt = f", the lowest since the index began in {full.index.min().year}" if record else ""
    cap = (f"NAR's Pending Home Sales Index {_verb(ch)} {abs(ch):.1f}% to {v:.1f} in {month_year(t)}{rec_txt} "
           f"({_signed(yy)} from a year earlier); by region, " +
           ", ".join(f"{r} {_signed(c)}" for r, c in regions) + f" from {pmon}.")
    # the logo carries the brand, so the source line no longer ends "· Home Economics"
    src = f"Source: NAR; values before {MON3[first_nar.month - 1]} {first_nar.year} via Mortgage News Daily"
    # ---- layout: the owner's grid reference (scripts/90_phsi_chart_grid.py, pending_home_sales_v10.png)
    Wc, Hc = W, 600
    PW = (GR - GL - COLGAP) / 2                       # two panels of identical width
    TOP, PH = 140, 330
    BOT = TOP + PH
    LX0, LX1 = GL, GL + PW
    RX0, RX1 = LX1 + COLGAP, GR
    lo, hi = float(series.min()), float(series.max())
    span = hi - lo
    ymin, ymax = math.floor((lo - 0.07 * span) / 20) * 20 - 5, hi + 0.03 * span
    ticks = list(range(int(math.ceil(ymin / 20) * 20), int(ymax) + 1, 20))
    px0, px1 = LX0 + max(_tw(str(g), 20) for g in ticks) + 17, LX1
    ords = [ts.year * 12 + ts.month for ts in series.index]
    o0, o1 = ords[0], ords[-1]
    X = lambda k: px0 + (px1 - px0) * (k - o0) / max(o1 - o0, 1)
    Y = lambda val: BOT - PH * (val - ymin) / (ymax - ymin)
    rlabel = f"{mon} by region, change from {pmon}"
    if _tw(rlabel, 20) > PW:
        rlabel = f"{MON3[t.month - 1]} by region, change from {MON3[_ts(prev_m).month - 1]}"
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{Wc}" '
         f'height="{Hc}" viewBox="0 0 {Wc} {Hc}">',
         f'<rect width="{Wc}" height="{Hc}" fill="{BG}"/>',
         _T(GL, 54, title, 34, 700, INK, extra=' letter-spacing="-0.7"'),
         _T(LX0, TOP - LABEL_GAP, "NAR pending home sales, seasonally adjusted", 20, 400, GREY)]
    for g in ticks:
        o.append(f'<line x1="{px0:.1f}" y1="{Y(g):.1f}" x2="{px1:.1f}" y2="{Y(g):.1f}" stroke="{GRID}" stroke-width="1"/>')
        o.append(_T(LX0, Y(g) + 7, str(g), 20, 400, GREY))
    first_yr = series.index[0].year + (0 if series.index[0].month == 1 else 1)
    for yr in range(first_yr, t.year + 1):
        if (yr - first_yr) % 4 == 0:
            _xlabel(o, X(yr * 12 + 1), str(yr), px0, px1, BOT + XLAB)
    d = " L".join(f"{X(k):.1f},{Y(val):.1f}" for k, val in zip(ords, series.values))
    # light shading beneath the level line, as on every release chart's level lines (owner, 21 Oct 2026)
    o.append(f'<path d="M{d} L{X(o1):.1f},{BOT:.1f} L{X(o0):.1f},{BOT:.1f} Z" fill="{BLUE}" '
             f'fill-opacity="{SHADE_OPACITY}" stroke="none"/>')
    o.append(f'<path d="M{d}" fill="none" stroke="{BLUE}" stroke-width="3.4" stroke-linejoin="round"/>')
    o.append(f'<circle cx="{X(o1):.1f}" cy="{Y(v):.1f}" r="5" fill="{BLUE}"/>')
    # region bars: run from the panel top down to the baseline of the left panel's year labels
    hbar_panel(o, regions, RX0, RX1, TOP, rlabel, "", _signed, height=BOT + XLAB - TOP)
    o.append(_T(GL, Hc - 30, src, 20, 400, GREY))
    o.append(_logo_tag(Hc - 30))
    o.append("</svg>")
    return Prebuilt("".join(o), Hc), None, 0, t, title, cap, src


def latest_phs():
    return _phs_index()[0].index[-1]


# ── NAR existing-home sales: accumulate FRED's 13-month window (+ overview PDFs) ───────────────────────────────
EHS_FRED = {("US", "sales_saar"): "EXHOSLUSM495S", ("Northeast", "sales_saar"): "EXHOSLUSNEM495S",
            ("Midwest", "sales_saar"): "EXHOSLUSMWM495S", ("South", "sales_saar"): "EXHOSLUSSOM495S",
            ("West", "sales_saar"): "EXHOSLUSWTM495S", ("US", "median_price"): "HOSMEDUSM052N",
            ("Northeast", "median_price"): "HOSMEDUSNEM052N", ("Midwest", "median_price"): "HOSMEDUSMWM052N",
            ("South", "median_price"): "HOSMEDUSSOM052N", ("West", "median_price"): "HOSMEDUSWTM052N",
            ("US", "inventory"): "HOSINVUSM495N", ("US", "months_supply"): "HOSSUPUSM673N"}


def update_ehs(extra_pdfs: list | tuple = ()):
    """Upsert FRED's NAR series (13 months) and the current NAR overview PDF (plus any `extra_pdfs`, e.g. archived
    September releases) into Data/NAR/existing_home_sales.parquet: month, region, measure (sales_saar, sales_nsa,
    median_price, inventory, months_supply), value, flag, source, vintage. Per (month, region, measure) the latest
    vintage wins; on a tie FRED wins. History therefore grows month by month although FRED keeps only 13."""
    import pandas as pd
    if not extra_pdfs and "ehs" in _DONE:
        return _DONE["ehs"]
    f = NAR_DIR / "existing_home_sales.parquet"
    cols = ["month", "region", "measure", "value", "flag", "source", "vintage"]
    old = _lake_df(f) if f.exists() else pd.DataFrame(columns=cols)
    rows = []
    for (reg, meas), sid in EHS_FRED.items():
        try:
            s = fred(sid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"existing home sales: FRED {sid} unavailable ({e})")
            continue
        lu = ((_META.get(sid) or {}).get("last_updated") or json.loads(
            (LAKE / f"{sid}.parquet.source.json").read_text()).get("last_updated", ""))[:10]
        rows += [dict(month=ts.strftime("%Y-%m"), region=reg, measure=meas, value=float(v), flag="",
                      source=f"FRED {sid}", vintage=lu) for ts, v in s.items()]
    pdfs = list(extra_pdfs)
    try:
        pdfs += [u for u in _nar_links(EHS_PAGE, "ehs") if "-overview-" in u][-1:]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"NAR existing-home-sales page unreachable ({e}); FRED only")
    for u in pdfs:
        try:
            sales, price = parse_ehs(_nar_text(u))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"existing home sales: {u} not parsed ({e})")
            continue
        if not sales:
            logger.warning(f"existing home sales: {u} parsed 0 sales rows (layout changed?)")
        rel, name = _nar_released(u), "NAR " + u.rsplit("/", 1)[-1]
        for per, *v, fl in sales:
            for i, reg in enumerate(REGIONS):
                rows.append(dict(month=per, region=reg, measure="sales_saar", value=v[i], flag=fl, source=name, vintage=rel))
                rows.append(dict(month=per, region=reg, measure="sales_nsa", value=v[5 + i], flag=fl, source=name, vintage=rel))
            rows.append(dict(month=per, region="US", measure="inventory", value=v[10], flag=fl, source=name, vintage=rel))
            rows.append(dict(month=per, region="US", measure="months_supply", value=v[11], flag=fl, source=name, vintage=rel))
        for per, *v, fl in price:
            for i, reg in enumerate(REGIONS):
                rows.append(dict(month=per, region=reg, measure="median_price", value=v[i], flag=fl, source=name, vintage=rel))
    df = old
    if rows:
        df = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
        df["_fred"] = df["source"].astype(str).str.startswith("FRED").astype(int)
        df = (df.sort_values(["vintage", "_fred"]).drop_duplicates(["month", "region", "measure"], keep="last")
              .drop(columns="_fred").reset_index(drop=True))
        k = ["month", "region", "measure"]
        a = old.sort_values(k).reset_index(drop=True)[cols].astype(str)
        b = df.sort_values(k).reset_index(drop=True)[cols].astype(str)
        if not (len(a) == len(b) and a.equals(b)):
            _write_lake(df[cols], f, "region, measure, month",
                        {"type": "manual", "source": "NAR existing-home sales: FRED (13-month window, NAR licensing) "
                         "accumulated monthly, plus NAR overview PDFs", "url": EHS_PAGE, "date_columns": ["month"],
                         "fred_series": sorted(set(EHS_FRED.values())), "note": "values in units (sales SAAR and "
                         "NSA, inventory), dollars (median price, NSA) and months (supply); latest vintage wins"})
            logger.info(f"lake NAR EHS: {len(df)} rows, {df['month'].min()}..{df['month'].max()}")
    _DONE["ehs"] = df
    return df


def _ehs(df, region, measure):
    return _mseries(df[(df.region == region) & (df.measure == measure)])


def latest_ehs():
    return _ehs(update_ehs(), "US", "sales_saar").index[-1]


def b_existing_home_sales():
    df = update_ehs()
    s, inv, px = _ehs(df, "US", "sales_saar") / 1e6, _ehs(df, "US", "inventory") / 1e6, _ehs(df, "US", "median_price") / 1000
    t = s.index[-1]
    ch = cur(pchg(s), s) if len(s) > 1 else float("nan")
    yy = cur(yoy(s), s) if len(s) > 12 else float("nan")
    xm = (s.index[-1] - s.index[0]).days < 800
    rows = [
        Row([Panel(_level(s, "1900-01"), "Existing homes sold", "millions, annual rate", xmonths=xm,
                   show_key=False)], ROW_TOP),
        Row([Panel(_level(inv, "1900-01"), "Inventory", "millions", show_key=False, xmonths=xm),
             Panel(_level(px, "1900-01"), "Median price", yprefix="$", ysuffix="k", show_key=False,
                   xmonths=xm)], ROW_BOT),
    ]
    title = f"Existing home sales {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Existing home sales were {s.iloc[-1]:.2f} million at an annual rate in {month_year(t)} ({_signed(ch)} on "
           f"the month, {_signed(yy)} on the year); inventory {inv.iloc[-1]:.2f} million, median price "
           f"${px.iloc[-1] * 1000:,.0f}.")
    src = "Source: NAR. Inventory and median price not seasonally adjusted."
    return rows, None, 0, t, title, cap, src


# ── Apartment List ──────────────────────────────────────────────────────
AL_PAGE = "https://www.apartmentlist.com/research/category/data-rent-estimates"
AL_FILES = {"rent_estimates": r"Apartment_List_Rent_Estimates_(\d{4})_(\d\d)\.csv$",
            "vacancy_index": r"Apartment_List_Vacancy_Index_(\d{4})_(\d\d)\.csv$"}


def update_apartment_list() -> dict:
    """Scrape the data page (cookie geofence_bypass=true gets past the non-US geo-block) for this month's CSV links,
    and when the vintage is new download rent estimates + vacancy index and store them long in
    Data/ApartmentList/<name>.parquet (location ids, [bed_size], month, value). Each vintage has the full history."""
    import duckdb
    if "al" in _DONE:
        return _DONE["al"]
    out = {k: AL_DIR / f"{k}.parquet" for k in AL_FILES}
    try:
        page = _http(AL_PAGE, cookies={"geofence_bypass": "true"}).text
        links = ["https:" + u if u.startswith("//") else u for u in
                 set(re.findall(r"(?:https:)?//(?:images|assets)\.ctfassets\.net/[^\"\\ ]+?\.csv", page))]
        if not links:
            raise RuntimeError("no CSV links on the data page (geo-block cookie no longer accepted, or layout changed)")
        for k, rx in AL_FILES.items():
            cands = sorted((re.search(rx, u).groups(), u) for u in links if re.search(rx, u))
            if not cands:
                raise RuntimeError(f"no {k} CSV link on the data page")
            (yy, mm), u = cands[-1]
            vint = f"{yy}-{mm}"
            if out[k].exists() and _sidecar(out[k]).get("vintage") == vint:
                continue
            dl = AL_DIR / ".download"
            dl.mkdir(parents=True, exist_ok=True)
            csv = dl / u.rsplit("/", 1)[-1]
            csv.write_bytes(_http(u, timeout=180).content)
            con = duckdb.connect()
            rel = f"read_csv('{csv}', all_varchar=true, header=true)"
            cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
            months = [c for c in cols if re.fullmatch(r"\d{4}_\d\d", c)]
            if len(months) < 24:
                raise RuntimeError(f"{csv.name}: only {len(months)} month columns")
            ids = [c for c in cols if c not in months]
            idsel = ", ".join(f'"{c}"' if c != "population" else "TRY_CAST(population AS BIGINT) AS population" for c in ids)
            order = "location_type, location_fips_code" + (", bed_size" if "bed_size" in ids else "") + ", month"
            tmp = out[k].with_suffix(".tmp.parquet")
            out[k].parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"""COPY (SELECT {idsel}, CAST(strptime(m, '%Y_%m') AS DATE) AS month, TRY_CAST(v AS DOUBLE) AS value
                FROM (UNPIVOT (SELECT * FROM {rel}) ON {", ".join(f'"{c}"' for c in months)} INTO NAME m VALUE v)
                WHERE TRY_CAST(v AS DOUBLE) IS NOT NULL ORDER BY {order})
                TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)""")
            n = con.execute(f"SELECT count(*) FROM '{tmp}'").fetchone()[0]
            tmp.replace(out[k])
            sc = _sidecar(out[k])
            sc.update(type="url", url=u, page=AL_PAGE, format="csv (wide, one column per month; stored long)",
                      vintage=vint, date_columns=["month"], rows=int(n), cookie="geofence_bypass=true",
                      source=f"Apartment List {k.replace('_', ' ')}",
                      units="dollars per month" if k == "rent_estimates" else "fraction of units vacant",
                      retrieved=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      refreshed_by="pulse/editor/release_charts.py")
            _sidecar_path(out[k]).write_text(json.dumps(sc, indent=1))
            csv.unlink(missing_ok=True)
            logger.info(f"lake Apartment List {k}: vintage {vint}, {n} rows")
    except Exception as e:  # noqa: BLE001
        if not all(p.exists() for p in out.values()):
            raise
        logger.warning(f"Apartment List: update failed ({e}); using the lake copy")
    _DONE["al"] = out
    return out


def _al_national():
    f = update_apartment_list()
    rent = _mseries(_lake_df(f["rent_estimates"], "SELECT month, value FROM src WHERE location_type = 'National' "
                                                  "AND bed_size = 'overall' ORDER BY month"))
    vac = _mseries(_lake_df(f["vacancy_index"], "SELECT month, value FROM src WHERE location_type = 'National' "
                                                "ORDER BY month")) * 100
    return rent, vac, f


def latest_apartment_list():
    return _al_national()[0].index[-1]


def _short_metro(name: str) -> str:
    return name.split(",")[0].split("-")[0].split("/")[0].strip()


def b_apartment_list():
    import pandas as pd
    rent, vac, f = _al_national()
    t = rent.index[-1]
    y = yoy(rent)
    m12 = (t - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    big = _lake_df(f["rent_estimates"], f"""
        WITH top AS (SELECT location_fips_code, any_value(location_name) AS name, max(population) AS pop FROM src
                     WHERE location_type = 'Metro' AND bed_size = 'overall' GROUP BY 1 ORDER BY pop DESC LIMIT 12)
        SELECT top.name, top.location_fips_code AS code, top.pop, a.value AS now, b.value AS ago FROM top
        JOIN src a ON a.location_fips_code = top.location_fips_code AND a.location_type = 'Metro'
             AND a.bed_size = 'overall' AND a.month = DATE '{t.strftime('%Y-%m-%d')}'
        JOIN src b ON b.location_fips_code = top.location_fips_code AND b.location_type = 'Metro'
             AND b.bed_size = 'overall' AND b.month = DATE '{m12}'""")
    vals = [(_short_metro(r["name"]), 100 * (r["now"] / r["ago"] - 1), str(r["code"]).zfill(5))
            for _, r in big.iterrows()]
    items = sorted([(n, v) for n, v, _ in vals], key=lambda kv: -kv[1])
    month = month_year(t)
    yv = cur(y, rent)
    # owner, 21 Oct 2026 (size rule + bubble strip): the median-rent level line is dropped (its change is the title
    # and the left panel; the level is in the caption); the twelve-metro bar list became a bubble strip
    rows = [Row([Panel(_bars(y, "2018-01"), "Rent, change from a year earlier", ysuffix="%", zero=True,
                       show_key=False),
                 Panel(_level(vac, "2017-01"), "Vacancy index", ysuffix="%", show_key=False)], STRIP_H),
            Row([Strip(_strip_items("apartment_list", vals), "12 largest metros", "sized by population",
                       bench=(f"National {_signed(yv)}", yv))], STRIP_H)]
    extra = None
    title = f"Apartment List rents {_verb(yv)} {abs(yv):.1f}% over the year to {MONTHS[t.month - 1]}"
    if _verb(yv) == "was unchanged":
        title = f"Apartment List rents were flat over the year to {MONTHS[t.month - 1]}"
    best, worst = items[0], items[-1]
    cap = (f"Apartment List's national median rent was ${rent.iloc[-1]:,.0f} in {month} ({_signed(yv)} from a year "
           f"earlier, {_signed(cur(pchg(rent), rent))} on the month); the vacancy index was {vac.iloc[-1]:.1f}%. Among the "
           f"12 largest metros, {best[0]} was strongest ({_signed(best[1])}) and {worst[0]} weakest ({_signed(worst[1])}).")
    src = "Source: Apartment List; Census population estimates."
    return rows, extra, 0, t, title, cap, src


# ── NAHB/Wells Fargo Housing Market Index ───────────────────────────────
NAHB_PAGE = "https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index"
HMI_SERIES = {"present": "present_sales", "next six": "next_6_months", "traffic": "traffic"}


def _parse_t2(p: Path) -> list[dict]:
    import pandas as pd
    df = pd.read_excel(p, header=None, engine="xlrd")
    rows = []
    for _, r in df.iterrows():
        try:
            yr = int(float(r[0]))
        except (TypeError, ValueError):
            continue
        if 1980 <= yr <= 2100:
            for m in range(1, 13):
                v = pd.to_numeric(r[m], errors="coerce")
                if pd.notna(v):
                    rows.append(dict(month=date(yr, m, 1), series="hmi", value=float(v)))
    return rows


def _parse_t3(p: Path) -> list[dict]:
    """Three blocks (present sales, next six months, traffic): a header row, a row of years, then Jan.-Dec. rows."""
    import pandas as pd
    df = pd.read_excel(p, header=None, engine="xlrd")
    rows, cur_s, years = [], None, {}
    mon = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}
    for _, r in df.iterrows():
        c0 = str(r[0]).strip().lower() if pd.notna(r[0]) else ""
        hit = next((v for k, v in HMI_SERIES.items() if k in c0), None)
        if hit:
            cur_s, years = hit, {}
            continue
        if cur_s and not c0:
            years = {j: int(float(x)) for j, x in r.items() if j and pd.notna(x)}
            continue
        if cur_s and years and c0[:3] in mon:
            for j, yr in years.items():
                v = pd.to_numeric(r[j], errors="coerce")
                if pd.notna(v):
                    rows.append(dict(month=date(yr, mon[c0[:3]], 1), series=cur_s, value=float(v)))
    return rows


def update_nahb() -> Path:
    """NAHB's HMI page links this month's t2 (national HMI history, SA, since 1985) and t3 (components) spreadsheets
    (URL carries ?rev=&hash=, so scrape it). New vintage -> Data/NAHB/hmi.parquet (month, series, value)."""
    import pandas as pd
    if "nahb" in _DONE:
        return _DONE["nahb"]
    f = NAHB_DIR / "hmi.parquet"
    try:
        page = _http(NAHB_PAGE).text
        links = {}
        for tag in ("t2-national-hmi-history", "t3-national-hmi-components-history"):
            m = re.search(r'href="([^"]*' + tag + r'-(\d{6})\.xls[^"]*)"', page)
            if not m:
                raise RuntimeError(f"no {tag} link on the HMI page")
            links[tag] = ("https://www.nahb.org" + m.group(1) if m.group(1).startswith("/")
                          else m.group(1)).replace("&amp;", "&"), m.group(2)
        vint = links["t2-national-hmi-history"][1]
        if not (f.exists() and _sidecar(f).get("vintage") == vint):
            d = NAHB_DIR / "xls"
            d.mkdir(parents=True, exist_ok=True)
            paths = {}
            for tag, (u, v) in links.items():
                p = d / f"{tag}-{v}.xls"
                r = _http(u)
                if not r.content.startswith(b"\xd0\xcf\x11\xe0"):
                    raise RuntimeError(f"{u}: not an .xls file")
                p.write_bytes(r.content)
                paths[tag] = p
            rows = _parse_t2(paths["t2-national-hmi-history"]) + _parse_t3(paths["t3-national-hmi-components-history"])
            df = pd.DataFrame(rows)
            got = set(df["series"]) if len(df) else set()
            if got != {"hmi", *HMI_SERIES.values()}:
                raise RuntimeError(f"NAHB spreadsheets parsed to series {sorted(got)} (layout changed?)")
            _write_lake(df, f, "series, month", {"type": "url", "url": NAHB_PAGE, "format": "xls (t2 + t3 tables)",
                                                 "source": "NAHB/Wells Fargo Housing Market Index, seasonally adjusted",
                                                 "vintage": vint, "files": [u for u, _ in links.values()],
                                                 "date_columns": ["month"]})
            logger.info(f"lake NAHB HMI: vintage {vint}, {len(df)} rows through {df['month'].max()}")
    except Exception as e:  # noqa: BLE001
        if not f.exists():
            raise
        logger.warning(f"NAHB HMI: update failed ({e}); using the lake copy")
    _DONE["nahb"] = f
    return f


def _hmi(series: str):
    return _mseries(_lake_df(update_nahb(), f"SELECT month, value FROM src WHERE series = '{series}' ORDER BY month"))


def latest_nahb():
    return _hmi("hmi").index[-1]


def b_nahb_hmi():
    h = _hmi("hmi")
    ps, n6, tr = _hmi("present_sales"), _hmi("next_6_months"), _hmi("traffic")
    t = h.index[-1]
    d = cur(diff(h), h)
    # wave-1 restyle (owner, 21 Oct 2026): the three overlapping component lines and their legend gave way to the
    # pending-sales layout -- the index on the left, this month's components as bars (name + one value) on the right
    comps = sorted([(nm, float(s.iloc[-1])) for nm, s in (("Current sales", ps), ("Next six months", n6),
                                                          ("Buyer traffic", tr)) if s.index[-1] == t],
                   key=lambda kv: -kv[1])
    rows = [Row([Panel(_level(h, "2000-01"), "Housing Market Index", "seasonally adjusted", ref=50, show_key=False),
                 HBars(comps, f"Components in {MONTHS[t.month - 1]}", vfmt=lambda v: f"{v:.0f}")], ROW_ONE)]
    v = h.iloc[-1]
    verb = "rose" if d > 0 else "fell" if d < 0 else "held"
    title = f"Builder confidence {verb} {'at' if verb == 'held' else 'to'} {v:.0f} in {MONTHS[t.month - 1]}"
    prev = h.iloc[-2] if len(h) > 1 else float("nan")
    cap = (f"The NAHB/Wells Fargo Housing Market Index was {v:.0f} in {month_year(t)} ({prev:.0f} the month before); "
           f"current sales {ps.iloc[-1]:.0f}, sales expectations for the next six months {n6.iloc[-1]:.0f}, buyer "
           f"traffic {tr.iloc[-1]:.0f}.")
    src = "Source: NAHB/Wells Fargo. Above 50, more builders say good than poor."
    return rows, None, 0, t, title, cap, src


# ── Freddie Mac House Price Index ───────────────────────────────────────
FM_URL = "https://www.freddiemac.com/fmac-resources/research/docs/fmhpi_master_file.csv"
FM_METROS = [("New York", "35620"), ("Los Angeles", "31080"), ("Chicago", "16980"), ("Dallas", "19100"),
             ("Houston", "26420"), ("Washington", "47900"), ("Philadelphia", "37980"), ("Miami", "33100"),
             ("Atlanta", "12060"), ("Boston", "14460"), ("Phoenix", "38060"), ("San Francisco", "41860")]


def update_fmhpi() -> Path:
    """Freddie Mac's master CSV (US, states, 387 CBSAs, monthly since 1975), refreshed when Last-Modified changes."""
    import duckdb
    if "fm" in _DONE:
        return _DONE["fm"]
    f = FM_DIR / "fmhpi_master_file.parquet"
    try:
        lm = _http(FM_URL, method="HEAD", tries=2, timeout=40).headers.get("last-modified")
        if not (f.exists() and lm and _sidecar(f).get("last_modified") == lm):
            dl = FM_DIR / ".download"
            dl.mkdir(parents=True, exist_ok=True)
            csv = dl / "fmhpi_master_file.csv"
            csv.write_bytes(_http(FM_URL, timeout=300).content)
            con = duckdb.connect()
            tmp = f.with_suffix(".tmp.parquet")
            con.execute(f"""COPY (SELECT GEO_Type AS geo_type, CAST(GEO_Code AS VARCHAR) AS geo_code, GEO_Name AS geo_name,
                    make_date(CAST(Year AS INTEGER), CAST(Month AS INTEGER), 1) AS month, CAST(Index_NSA AS DOUBLE) AS index_nsa,
                    CAST(Index_SA AS DOUBLE) AS index_sa FROM read_csv_auto('{csv}', all_varchar=true)
                    ORDER BY geo_type, geo_code, month) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)""")
            n, mx = con.execute(f"SELECT count(*), max(month) FROM '{tmp}'").fetchone()
            tmp.replace(f)
            sc = _sidecar(f)
            sc.update(type="url", url=FM_URL, format="csv", last_modified=lm, date_columns=["month"], rows=int(n),
                      last_obs=str(mx), source="Freddie Mac House Price Index (FMHPI), Dec 2000 = 100",
                      retrieved=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      refreshed_by="pulse/editor/release_charts.py")
            _sidecar_path(f).write_text(json.dumps(sc, indent=1))
            csv.unlink(missing_ok=True)
            logger.info(f"lake Freddie Mac HPI: {n} rows through {mx}")
    except Exception as e:  # noqa: BLE001
        if not f.exists():
            raise
        logger.warning(f"Freddie Mac HPI: update failed ({e}); using the lake copy")
    _DONE["fm"] = f
    return f


def _fm(geo_type: str, code: str | None = None):
    w = f"geo_type = '{geo_type}'" + (f" AND geo_code = '{code}'" if code else "")
    return _mseries(_lake_df(update_fmhpi(), f"SELECT month, index_sa AS value FROM src WHERE {w} ORDER BY month"))


def latest_fmhpi():
    return _fm("US").index[-1]


def b_freddie_mac_hpi():
    import pandas as pd
    us = _fm("US")
    t = us.index[-1]
    y = yoy(us)
    vals = []
    for name, code in FM_METROS:
        s = _fm("CBSA", code)
        if len(s) and s.index[-1] == t:
            vals.append((name, cur(yoy(s), s), code))
    t0 = (t - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    cnt = _lake_df(update_fmhpi(), f"""SELECT count(*) FILTER (WHERE a.index_sa < b.index_sa) AS down, count(*) AS n FROM src a
        JOIN src b ON a.geo_code = b.geo_code AND b.geo_type = 'CBSA' AND b.month = DATE '{t0}'
        WHERE a.geo_type = 'CBSA' AND a.month = DATE '{t.strftime('%Y-%m-%d')}'""").iloc[0]
    month = month_year(t)
    yv = cur(y, us)
    rows = [Row([Panel(_bars(y, "2016-01"), "US, change from a year earlier", "seasonally adjusted", ysuffix="%",
                       zero=True, show_key=False)], STRIP_H),
            Row([Strip(_strip_items("freddie_mac_hpi", vals), "12 largest metros", "sized by population",
                       bench=(f"United States {_signed(yv)}", yv))], STRIP_H)]
    extra = None
    title = f"Freddie Mac house prices {_verb(yv)} {abs(yv):.1f}% over the year to {MONTHS[t.month - 1]}"
    if _verb(yv) == "was unchanged":
        title = f"Freddie Mac house prices were flat over the year to {MONTHS[t.month - 1]}"
    cap = (f"The Freddie Mac House Price Index was {_signed(yv)} from a year earlier in {month} and "
           f"{_signed(cur(pchg(us), us), 2)} on the month (seasonally adjusted); {int(cnt['down'])} of {int(cnt['n'])} "
           f"metro areas were below their level a year earlier.")
    src = "Source: Freddie Mac House Price Index; Census population estimates."
    return rows, extra, 0, t, title, cap, src


# ── FRED-only wave-2 keys ───────────────────────────────────────────────
def _ann(s):
    """Quarter-on-quarter change at an annual rate, %."""
    r = _reg(s)
    return (((r / r.shift(1)) ** 4 - 1) * 100).dropna()


def b_gdp():
    g, ri, rc = fred("A191RL1Q225SBEA"), fred("PRFIC1"), fred("A011RY2Q224SBEA")
    t = g.index[-1]
    rg = _ann(ri)
    rows = [Row([Panel([Line(since(g, "2022-01"), BLUE, kind="bar")], "Real GDP", "annual rate",
                       ysuffix="%", zero=True, show_key=False),
                 Panel([Line(since(rg, "2022-01"), BLUE, kind="bar")], "Residential investment",
                       "annual rate", ysuffix="%", zero=True, show_key=False)], ROW_ONE)]
    v = float(g.iloc[-1])
    q = QNAMES[(t.month - 1) // 3]
    title = (f"The economy grew at a {v:.1f}% annual rate in the {q} quarter" if v >= 0 else
             f"The economy shrank at a {abs(v):.1f}% annual rate in the {q} quarter")
    rgv = cur(rg, ri)
    cap = (f"Real GDP {'grew' if v >= 0 else 'shrank'} at a {abs(v):.1f}% annual rate in the {q} quarter of {t.year}; "
           f"real residential investment {_verb(rgv)} at a {abs(rgv):.1f}% rate, contributing "
           f"{_signed(float(rc.iloc[-1]), 2, ' percentage points')} to growth.")
    src = "Source: Bureau of Economic Analysis, via FRED. Inflation-adjusted, seasonally adjusted."
    return rows, None, 0, t, title, cap, src


def b_vehicle_sales():
    s = fred("TOTALSA")
    t = s.index[-1]
    ch = cur(pchg(s), s)
    rows = [Row([Panel(_level(s, "2016-01"), "Total vehicle sales", "millions, annual rate", show_key=False)],
                ROW_ONE)]
    title = f"Vehicle sales {_verb(ch)} {abs(ch):.1f}% in {MONTHS[t.month - 1]}"
    cap = (f"Total vehicle sales were a {s.iloc[-1]:.2f} million annual rate in {month_year(t)} ({_signed(ch)} on the "
           f"month, {_signed(cur(yoy(s), s))} on the year).")
    src = "Source: Bureau of Economic Analysis, via FRED. Seasonally adjusted annual rate."
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
    latest: Callable | None = None  # () -> Timestamp of the newest period (non-FRED sources; runs their updater)
    rx: re.Pattern | None = None

    def __post_init__(self):
        self.rx = re.compile(self.trigger, re.I)


REGISTRY = [
    # wave 2 first where its pattern is more specific than a wave-1 neighbour (pending before existing)
    Release("pending_home_sales", "pending home sales", r"pending[- ]home[- ]sales|pending sales index|\bPHSI\b",
            "NAR Pending Home Sales Index (PDF from NAR's research page, upserted into Data/NAR; not on FRED)",
            builder=b_pending_home_sales, latest=latest_phs),
    Release("housing_starts", "housing starts", r"housing starts|building permits|residential construction|"
            r"\bstarts\b.{0,40}\bpermits\b", "FRED (Census/HUD New Residential Construction)",
            ["HOUST", "HOUST1F", "HOUST5F", "PERMIT", "HOUSTNE", "HOUSTMW", "HOUSTS", "HOUSTW"], b_housing_starts),
    Release("new_home_sales", "new home sales", r"new[- ]home sales|new single-family (home )?sales|sales of new (single-family )?homes",
            "FRED (Census/HUD New Residential Sales)", ["HSN1F", "MSACSR", "MSPNHSUS"], b_new_home_sales),
    Release("existing_home_sales", "existing home sales", r"existing[- ]home sales",
            "NAR via FRED (13-month window) + NAR overview PDF, accumulated in Data/NAR/existing_home_sales.parquet",
            sorted(set(EHS_FRED.values())), b_existing_home_sales, latest=latest_ehs),
    Release("case_shiller", "Case-Shiller", r"case[- ]shiller", "FRED (S&P Cotality Case-Shiller)",
            ["CSUSHPINSA", "SPCS20RNSA"] + [s for _, s, _ in CS_METROS], b_case_shiller,
            intro="The {period} Case-Shiller release, charted:"),
    Release("cotality_hpi", "Cotality home price index", r"\bcotality\b|\bcorelogic\b", "Cotality HPI (wave 2)"),
    Release("fhfa_hpi", "FHFA house price index", r"\bFHFA\b.{0,60}(price|HPI|index)|federal housing finance agency.{0,60}price",
            "FRED (FHFA purchase-only HPI)", ["HPIPONM226S"], b_fhfa_hpi, intro="The {period} FHFA house price release, charted:"),
    # before mortgage_rates, whose "freddie mac ... rate" pattern would otherwise catch it
    Release("freddie_mac_hpi", "Freddie Mac house price index",
            r"freddie mac (house|home) price|\bFMHPI\b|freddie mac.{0,40}\bHPI\b|freddie mac.{0,40}(house|home) prices",
            "Freddie Mac HPI master CSV (US, states, 387 metros) in Data/Price/FreddieMac", builder=b_freddie_mac_hpi,
            latest=latest_fmhpi, intro="The {period} Freddie Mac house price index, charted:"),
    Release("zillow_zhvi", "Zillow home values", r"\bZHVI\b|zillow home value|zillow.{0,40}home (price|value)s?",
            "Zillow ZHVI with a map (wave 2)"),
    Release("zillow_zori", "Zillow rents", r"\bZORI\b|zillow observed rent|zillow.{0,40}rents?\b", "Zillow ZORI (wave 2)"),
    Release("apartment_list", "Apartment List rents", r"apartment list",
            "Apartment List rent estimates + vacancy index CSVs (data page, geo-block cookie) in Data/ApartmentList",
            builder=b_apartment_list, latest=latest_apartment_list,
            intro="The {period} Apartment List rent report, charted:"),
    Release("construction_spending", "construction spending", r"construction spending|construction outlays",
            "FRED (Census Construction Spending)", ["TLRESCONS"], b_construction_spending),
    Release("hvs", "housing vacancies and homeownership", r"homeownership rate|housing vacanc|vacancy rates?\b|\bHVS\b",
            "FRED (Census HVS)", ["RHORUSQ156N", "RRVRUSQ156N", "RHVRUSQ156N"], b_hvs, freq="Q",
            intro="The {period} homeownership and vacancy release, charted:"),
    Release("nahb_hmi", "NAHB builder confidence", r"\bNAHB\b|builder (confidence|sentiment)|housing market index",
            "NAHB/Wells Fargo HMI spreadsheets (t2 + t3, history since 1985) in Data/NAHB", builder=b_nahb_hmi,
            latest=latest_nahb, intro="The {period} NAHB builder confidence index, charted:"),
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
            "ICE First Look / Mortgage Monitor: not built. mortgagetech.ice.com is reachable, but the First Look "
            "page gives one national delinquency rate per month in prose (no table, PDF or history)"),
    Release("vehicle_sales", "vehicle sales", r"(light )?vehicle sales|auto sales", "FRED (BEA total vehicle sales)",
            ["TOTALSA"], b_vehicle_sales),
    Release("gdp", "GDP", r"\bGDP\b|gross domestic product", "FRED (BEA real GDP and residential investment)",
            ["A191RL1Q225SBEA", "PRFIC1", "A011RY2Q224SBEA"], b_gdp, freq="Q",
            intro="The {period} GDP release, charted:"),
    Release("ism", "ISM", r"\bISM\b|purchasing managers",
            "ISM PMI: not built. ismworld.org serves a reCAPTCHA page to this server; the PR Newswire copies are prose "
            "with one headline number and no history"),
    Release("mba_applications", "MBA mortgage applications",
            r"\bMBA\b.{0,60}(applications|purchase index|refinanc)|mortgage applications",
            "MBA weekly applications: not built. mba.org returns a Cloudflare 403 to this server, and the index "
            "levels are subscription data", freq="W"),
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
    lp = getattr(rel, "latest_period", None)       # release_maps sets a callable returning the period string
    if callable(lp):
        return lp()
    if rel.latest is not None:
        return period_of(rel.latest(), rel.freq)
    if rel.series:
        return period_of(fred(rel.series[0]).index[-1], rel.freq)
    return period_of(rel.builder()[3], rel.freq)


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
    if isinstance(rows, Prebuilt):
        svg, H = rows.svg, rows.H
    else:
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


# ═══════════════════════════════════════════════════════════════════════
# plugin: release_maps.REGISTRY (map builders: zillow_zhvi, zillow_zori, cotality_hpi) replaces the placeholder
# Release of the same key (keys not in REGISTRY are added before the mortgage-rate catch-all). Placed at the end of
# the module so release_maps can import any helper from here. The stage keeps working when the module is absent
# (logged at info) or broken (logged with the traceback; placeholders stay).
# ═══════════════════════════════════════════════════════════════════════
def _load_plugins() -> None:
    sys.modules.setdefault("release_charts", sys.modules[__name__])   # CLI run: `import release_charts` = this module
    try:
        import release_maps
    except ModuleNotFoundError as e:
        if e.name == "release_maps":
            logger.info("release_maps not present; map releases keep their placeholders")
        else:
            logger.exception("release_maps failed to import; map releases keep their placeholders")
        return
    except Exception:  # noqa: BLE001
        logger.exception("release_maps failed to import; map releases keep their placeholders")
        return
    try:
        merged = list(REGISTRY)
        for r in list(getattr(release_maps, "REGISTRY", None) or []):
            if not isinstance(getattr(r, "key", None), str) or not hasattr(r, "rx") or not hasattr(r, "builder"):
                logger.warning(f"release_maps: skipping {r!r} (not a Release)")
                continue
            i = next((k for k, x in enumerate(merged) if x.key == r.key), None)
            if i is None:
                j = next((k for k, x in enumerate(merged) if x.key == "mortgage_rates"), len(merged))
                merged.insert(j, r)
            else:
                merged[i] = r
        REGISTRY[:] = merged
        BY_KEY.clear()
        BY_KEY.update({r.key: r for r in REGISTRY})
        logger.debug(f"release_maps merged: {[r.key for r in getattr(release_maps, 'REGISTRY', [])]}")
    except Exception:  # noqa: BLE001
        logger.exception("release_maps.REGISTRY could not be merged; map releases keep their placeholders")


_load_plugins()


if __name__ == "__main__":
    sys.exit(_cli())
