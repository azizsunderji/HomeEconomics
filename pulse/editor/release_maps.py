"""Release maps: US metro bubble maps for the release-chart stage (owner, 21 Oct 2026).

Owner's rule (21 Oct 2026): "the monthly Zillow home price releases, we should cover with a map", and "Go ahead with
the Zillow map and whatever else you need to complete this task of being able to put in updated charts when new
releases come out."

What is here
  map engine   draw_metro_map(): contiguous-US Albers equal-area map (hand-rolled formula: lat_1 29.5, lat_2 45.5,
               lat_0 37.5, lon_0 -96), Census cb_2023 state outlines, one bubble per metro at the CBSA centroid,
               bubble area proportional to 2024 population, colour = change on a diverging scale around zero
               (falling red / light red, mild rise light green, rising light blue / blue), labelled metros placed
               without overlaps, colour and size legends. Alaska and Hawaii are omitted (said in the source line).
               Drawn as an `extra` callable for release_charts.compose(), so the output contract (PNG via
               render_png, title, footer, logo) is the same as every other release chart.
  geometry     pulse/editor/geo/us_metro_map_geo.json: simplified state rings (lon/lat) and, per CBSA, centroid,
               2024 population and the Zillow-style name keys. Built once with geopandas (`--build-geo`); the
               editor venv has no geopandas, so the stage only reads the JSON.
  builders     zillow_zhvi, zillow_zori (lake parquet, period from the newest month in the file) and cotality_hpi
               (Cotality publishes no download: each monthly article's Infogram charts carry their data as JSON;
               cotality_update() appends every edition listed in the sitemap to the lake and fails loudly when the
               template changes). Builders return release_charts' tuple `rows, extra, extra_h, t, title, cap, src`.
  REGISTRY     Release entries with the same keys and trigger regexes as the placeholders in
               release_charts.REGISTRY (merged by release_charts._load_plugins()). Each sets Release.latest
               (() -> Timestamp of the newest period; LATEST) when the dataclass has that field, plus a
               `latest_period` attribute (() -> "YYYY-MM"; PERIODS). Cotality's runs its updater.
  label rule   Owner (21 Oct 2026): no clutter. The map labels a handful of metros (the four largest, the two
               strongest and two weakest, fallbacks by size; at most 8), no value callouts on the national line.

CLI
    python release_maps.py --build-geo              rebuild the geometry JSON (needs geopandas)
    python release_maps.py --update-cotality        fetch new Cotality editions into the lake
    python release_maps.py --render KEY [--out P]   dry render one map to a PNG (nothing hosted or recorded)
"""
from __future__ import annotations

import argparse
import html as _html
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, fields as dataclass_fields
from datetime import date, datetime, timezone
from pathlib import Path


def _rc():
    """release_charts, without importing a second copy when it is running as __main__ (its CLI) and imports this
    module from its plugin hook part-way through its own execution."""
    m = sys.modules.get("release_charts")
    if m is not None:
        return m
    main = sys.modules.get("__main__")
    if main is not None and Path(getattr(main, "__file__", "") or "").name == "release_charts.py" \
            and hasattr(main, "Release"):
        return main
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import release_charts as m2
    return m2


rc = _rc()
Release, compose, render_png, Row, Panel, Line, draw_panel = (rc.Release, rc.compose, rc.render_png, rc.Row, rc.Panel,
                                                              rc.Line, rc.draw_panel)
_T, _halo, _tw, _esc, wrap = rc._T, rc._halo, rc._tw, rc._esc, rc.wrap
FAM, INK, GREY, GRID, BG, BLUE, RED, GREEN = rc.FAM, rc.INK, rc.GREY, rc.GRID, rc.BG, rc.BLUE, rc.RED, rc.GREEN
GL, GR, MONTHS, MON3 = rc.GL, rc.GR, rc.MONTHS, rc.MON3
_signed, _verb, month_year, since, period_of = rc._signed, rc._verb, rc.month_year, rc.since, rc.period_of

logger = logging.getLogger("noon.release_maps")

ZILLOW = Path(os.environ.get("NOON_ZILLOW_LAKE", "/home/aziz/OVH/Data/Price/Zillow"))
ZHVI_FILE = ZILLOW / "Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet"
ZORI_FILE = ZILLOW / "Metro_zori_uc_sfrcondomfr_sm_month.parquet"
COT_DIR = Path(os.environ.get("NOON_COTALITY_LAKE", "/home/aziz/OVH/Data/Cotality"))
COT_RELEASES = COT_DIR / "hpi_releases.parquet"
COT_METROS = COT_DIR / "hpi_release_metros.parquet"
GEO_JSON = Path(__file__).resolve().parent / "geo" / "us_metro_map_geo.json"
SHAPES = Path("/home/aziz/OVH/Reference/Shapefiles")
XWALK = Path("/home/aziz/OVH/Data/Crosswalks/CBSA_County_Crosswalk_incl2020.parquet")
COUNTY_POP = Path("/home/aziz/OVH/Data/county_population_2020_2024.parquet")
EXCLUDE_STATES = {"02", "15", "60", "66", "69", "72", "78"}      # AK, HI, territories (house map rule)
N_METROS = 100

LAND, BORDER = "#EDEFE7", "#FFFFFF"
LRED, LGREEN, LBLUE = "#FBCAB5", "#C6DCCB", "#7DD4FF"


# ═══════════════════════════════════════════════════════════════════════
# projection
# ═══════════════════════════════════════════════════════════════════════
_P1, _P2, _P0, _L0 = (math.radians(v) for v in (29.5, 45.5, 37.5, -96.0))
_N = (math.sin(_P1) + math.sin(_P2)) / 2
_C = math.cos(_P1) ** 2 + 2 * _N * math.sin(_P1)
_RHO0 = math.sqrt(_C - 2 * _N * math.sin(_P0)) / _N


def albers(lon: float, lat: float) -> tuple[float, float]:
    """Albers equal-area conic on the unit sphere (house parameters). y grows northward."""
    rho = math.sqrt(_C - 2 * _N * math.sin(math.radians(lat))) / _N
    th = _N * (math.radians(lon) - _L0)
    return rho * math.sin(th), _RHO0 - rho * math.cos(th)


# ═══════════════════════════════════════════════════════════════════════
# geometry cache (built once with geopandas; read with plain json)
# ═══════════════════════════════════════════════════════════════════════
def _name_keys(cbsa_name: str) -> list[str]:
    """Zillow-style names for a CBSA title: first principal city + first state ("New York, NY"), splitting the city
    part on "-" and "/" (Louisville/Jefferson County -> Louisville). Primary key first, then the other cities."""
    city_part, _, st_part = cbsa_name.rpartition(", ")
    st = st_part.split("-")[0][:2]
    cities = [c.strip() for c in re.split(r"[-/]", city_part) if c.strip()]
    keys = [f"{c}, {st}" for c in cities]
    return list(dict.fromkeys(keys))


def build_geo_cache(out: Path = GEO_JSON, tol_m: float = 2500) -> Path:
    import duckdb
    import geopandas as gpd
    aea = "+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=37.5 +lon_0=-96 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"
    st = gpd.read_file(SHAPES / "cb_2023_state" / "cb_2023_us_state_5m.shp")
    st = st[~st.STATEFP.isin(EXCLUDE_STATES)].to_crs(aea)
    st["geometry"] = st.geometry.simplify(tol_m, preserve_topology=True)
    st = st.to_crs(4326)
    states = []
    for _, r in st.sort_values("STATEFP").iterrows():
        polys = list(r.geometry.geoms) if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
        rings = []
        for p in polys:
            if p.area < 0.002:              # drop specks (tiny islands) at this scale
                continue
            rings.append([[round(x, 3), round(y, 3)] for x, y in p.exterior.coords])
        states.append({"fips": r.STATEFP, "abbr": r.STUSPS, "rings": rings})
    cb = gpd.read_file(SHAPES / "cb_2023_cbsa" / "cb_2023_us_cbsa_5m.shp").to_crs(aea)
    cen = gpd.GeoSeries(cb.geometry.centroid, crs=aea).to_crs(4326)
    pop = dict(duckdb.connect().execute(
        f"SELECT lpad(CAST(x.cbsa23 AS VARCHAR), 5, '0'), sum(p.pop_2024) FROM '{XWALK}' x "
        f"JOIN '{COUNTY_POP}' p ON lpad(CAST(x.county AS VARCHAR), 5, '0') = p.fips "
        f"WHERE x.Year = 2023 AND x.cbsa23 <> 99999 GROUP BY 1").fetchall())
    cbsas = []
    for (_, r), c in zip(cb.iterrows(), cen):
        cbsas.append({"cbsa": r.CBSAFP, "name": r.NAME, "lsad": r.LSAD, "lon": round(c.x, 4), "lat": round(c.y, 4),
                      "pop": int(pop.get(r.CBSAFP, 0) or 0), "keys": _name_keys(r.NAME)})
    obj = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "sources": {"states": "Census cb_2023_us_state_5m (AK, HI and territories excluded), simplified "
                                 f"{tol_m:.0f} m in Albers",
                       "cbsa": "Census cb_2023_us_cbsa_5m; centroid computed in Albers equal-area",
                       "pop": "Census county population estimates, July 2024 (Data/county_population_2020_2024), "
                              "summed over 2023 CBSA delineation counties (Data/Crosswalks)"},
           "states": states, "cbsa": cbsas}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, separators=(",", ":")))
    return out


_GEO = None


def geo() -> dict:
    global _GEO
    if _GEO is None:
        _GEO = json.loads(GEO_JSON.read_text())
    return _GEO


def match_metros(names: list[tuple[str, str]]) -> tuple[dict, list]:
    """Match (RegionName, StateName) pairs to CBSAs. Returns ({RegionName: cbsa dict}, [unmatched names]).
    Primary keys are tried first, then secondary principal cities."""
    cb = geo()["cbsa"]
    prim = {}
    sec = {}
    for c in cb:
        prim.setdefault(c["keys"][0], c)
        for k in c["keys"][1:]:
            sec.setdefault(k, c)
    out, miss = {}, []
    for name, _st in names:
        c = prim.get(name) or sec.get(name)
        if c:
            out[name] = c
        else:
            miss.append(name)
    return out, miss


# ═══════════════════════════════════════════════════════════════════════
# map engine
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class MapPoint:
    name: str                      # label text stem, e.g. "New York"
    lon: float
    lat: float
    value: float                   # change, percent
    pop: float = 0.0               # bubble area ~ pop (0 = fixed size)
    label: int = 0                 # label priority: 1 = first; 0 = no label


def diverging_bins(values: list, step: float | None = None) -> tuple[list, list, list]:
    """Five bins around zero: below -step, -step..0, 0..step, step..2step, above 2step.
    The step is chosen so that about 90% of |values| fall within 2 steps. Returns (edges, colours, labels)."""
    if step is None:
        a = sorted(abs(v) for v in values if v == v)
        p90 = a[int(0.9 * (len(a) - 1))] if a else 1
        step = next((s for s in (0.5, 1, 2, 2.5, 3, 4, 5, 10, 20) if 2 * s >= p90), 20)
    f = (lambda v: f"{v:g}")
    edges = [-step, 0, step, 2 * step]
    cols = [RED, LRED, LGREEN, LBLUE, BLUE]
    labs = [f"Below −{f(step)}%", f"−{f(step)} to 0%", f"0 to {f(step)}%", f"{f(step)} to {f(2 * step)}%",
            f"Above {f(2 * step)}%"]
    return edges, cols, labs


def colour_of(v: float, edges: list, cols: list) -> str:
    for e, c in zip(edges, cols):
        if v < e:
            return c
    return cols[-1]


def _box_overlap(a, b, pad=2) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _circle_box_overlap(cx, cy, r, b) -> bool:
    nx, ny = min(max(cx, b[0]), b[2]), min(max(cy, b[1]), b[3])
    return (nx - cx) ** 2 + (ny - cy) ** 2 < r * r


def draw_metro_map(o: list, x0: float, top: float, width: float, points: list, edges, cols, labs,
                   vfmt=lambda v: _signed(v), heading: str = "", sub: str = "", rmax: float = 34,
                   pop_ref: float | None = None, max_labels: int = 8, size_legend: bool = True,
                   fixed_r: float = 9) -> float:
    """Draw the map with its legend; returns the bottom y. `points` with label=True are label candidates in
    priority order (a candidate that cannot be placed clear of other labels is skipped)."""
    g = geo()
    # projected frame from the state outlines
    proj = [[[albers(lo, la) for lo, la in ring] for ring in s["rings"]] for s in g["states"]]
    xs = [p[0] for s in proj for ring in s for p in ring]
    ys = [p[1] for s in proj for ring in s for p in ring]
    mx0, mx1, my0, my1 = min(xs), max(xs), min(ys), max(ys)
    # legend line above the map
    y = top
    label = heading + (f", {sub}" if heading and sub else sub)
    if label:                    # the stage's panel label: one muted 20 px line (baseline y+20 in an extra)
        o.append(_T(x0, y + 20, label, 20, 400, GREY))
        y += 36
    lx = x0
    for c, lb in zip(cols, labs):
        o.append(f'<circle cx="{lx + 10:.1f}" cy="{y + 17:.1f}" r="10" fill="{c}"/>')
        o.append(_T(lx + 26, y + 24, lb, 20, 400, INK))
        lx += 26 + _tw(lb, 20, 400) + 26
    y += 46
    # fit the map into the width
    sc = width / (mx1 - mx0)
    mh = (my1 - my0) * sc
    X = lambda px: x0 + (px - mx0) * sc
    Y = lambda py: y + (my1 - py) * sc
    for s in proj:
        d = " ".join("M" + " L".join(f"{X(px):.1f},{Y(py):.1f}" for px, py in ring) + " Z" for ring in s)
        o.append(f'<path d="{d}" fill="{LAND}" stroke="{BORDER}" stroke-width="0.75" stroke-linejoin="round"/>')
    pmax = pop_ref or max([p.pop for p in points] + [1])
    R = (lambda pop: max(3.0, rmax * math.sqrt(pop / pmax))) if any(p.pop for p in points) else (lambda pop: fixed_r)
    placed = []                  # (cx, cy, r, point)
    for p in sorted(points, key=lambda q: -q.pop):
        px, py = albers(p.lon, p.lat)
        placed.append((X(px), Y(py), R(p.pop), p))
    for cx, cy, r, p in placed:
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{colour_of(p.value, edges, cols)}" '
                 f'fill-opacity="0.92" stroke="{BG}" stroke-width="1"/>')
    # labels: candidates in the caller's order; try 8 positions; keep clear of other labels, labelled bubbles and
    # the map frame; among clear positions pick the one covering the fewest other bubbles
    boxes = []
    rings = []
    yb_map = y + mh
    if size_legend and any(p.pop for p in points):
        # keep labels off the population key drawn later at the bottom left
        refs0 = ([v for v in (20e6, 5e6) if v <= pmax * 1.05] or [pmax])[0]
        r0 = R(refs0)
        boxes.append((x0, yb_map - 6 - 2 * r0 - 34, x0 + max(_tw("Population", 20, 500),
                                                            6 + 2 * r0 + 14 + _tw("20m", 20, 300)) + 4, yb_map))
    cands = [(cx, cy, r, p) for cx, cy, r, p in placed if p.label]
    cands.sort(key=lambda c: c[3].label)
    frame = (x0, y - 4, x0 + width, y + mh + 30)
    n_done = 0
    for cx, cy, r, p in cands:
        if n_done >= max_labels:
            break
        txt = f"{p.name} {vfmt(p.value)}"
        if any(_circle_box_overlap(cx, cy, r + 1, q) for q in boxes):
            logger.info(f"map label skipped (bubble under another label): {txt}")
            continue
        w1 = _tw(txt, 20, 500) + 4
        w2 = max(_tw(p.name, 20, 500), _tw(vfmt(p.value), 20, 500)) + 4
        d = r * 0.7
        # (lines, anchor, x, first baseline): one-line positions first, then name/value stacked on two lines
        opts = [(1, "start", cx + r + 5, cy + 7), (1, "end", cx - r - 5, cy + 7),
                (1, "middle", cx, cy - r - 7), (1, "middle", cx, cy + r + 21),
                (1, "start", cx + d + 3, cy - d - 5), (1, "end", cx - d - 3, cy - d - 5),
                (1, "start", cx + d + 3, cy + d + 19), (1, "end", cx - d - 3, cy + d + 19),
                (2, "start", cx + r + 5, cy - 4), (2, "end", cx - r - 5, cy - 4),
                (2, "middle", cx, cy - r - 29), (2, "middle", cx, cy + r + 21),
                (2, "start", cx + d + 3, cy - d - 27), (2, "end", cx - d - 3, cy - d - 27),
                (2, "start", cx + d + 3, cy + d + 19), (2, "end", cx - d - 3, cy + d + 19)]
        best = None
        why = []
        for nl, anchor, tx, ty in opts:
            w = w1 if nl == 1 else w2
            bx0 = tx if anchor == "start" else tx - w if anchor == "end" else tx - w / 2
            b = (bx0, ty - 16, bx0 + w, ty + 5 + (22 if nl == 2 else 0))
            if b[0] < frame[0] or b[2] > frame[2] or b[1] < frame[1] or b[3] > frame[3]:
                why.append("frame")
                continue
            if any(_box_overlap(b, q) for q in boxes):
                why.append("label")
                continue
            hit = [f"{qx:.0f},{qy:.0f}" for qx, qy, qr in rings if _circle_box_overlap(qx, qy, qr + 1, b)]
            if hit:
                why.append("labelled bubble")
                continue
            # cost: another label candidate's bubble 10; any other bubble 5 when the text hides its centre, else 1;
            # two-line and diagonal positions 1 extra each (they read as less attached to their bubble)
            cover = sum((10 if q[3].label else 5 if (b[0] <= q[0] <= b[2] and b[1] <= q[1] <= b[3]) else 1)
                        for q in placed if q[3] is not p and _circle_box_overlap(q[0], q[1], q[2], b))
            cover += (1 if nl == 2 else 0) + (1 if abs(ty - cy) > 8 and anchor != "middle" and nl == 1 else 0)
            if best is None or cover < best[0]:
                best = (cover, nl, anchor, tx, ty, b)
        if best is None:
            logger.info(f"map label skipped (no room): {txt} [{', '.join(why)}]")
            continue
        _, nl, anchor, tx, ty, b = best
        boxes.append(b)
        rings.append((cx, cy, r))
        if nl == 1:
            o.append(_halo(tx, ty, txt, 20, 500, INK, anchor))
        else:
            o.append(_halo(tx, ty, p.name, 20, 500, INK, anchor))
            o.append(_halo(tx, ty + 22, vfmt(p.value), 20, 500, INK, anchor))
        n_done += 1
    for cx, cy, r in rings:                    # outline only the labelled bubbles
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="{INK}" stroke-width="1.2"/>')
    yb = y + mh
    # size legend, bottom left (Pacific, off southern California)
    if size_legend and any(p.pop for p in points):
        refs = [v for v in (20e6, 5e6) if v <= pmax * 1.05] or [pmax]
        bx, by = x0 + 6 + R(refs[0]), yb - 6
        o.append(_T(x0, by - 2 * R(refs[0]) - 14, "Population", 20, 500, INK))
        for v in refs:
            rr = R(v)
            o.append(f'<circle cx="{bx:.1f}" cy="{by - rr:.1f}" r="{rr:.1f}" fill="none" stroke="{INK}" '
                     f'stroke-width="0.9"/>')
            ly = by - 2 * rr
            o.append(f'<line x1="{bx:.1f}" y1="{ly:.1f}" x2="{bx + R(refs[0]) + 10:.1f}" y2="{ly:.1f}" stroke="{INK}" '
                     f'stroke-width="0.6"/>')
            o.append(_T(bx + R(refs[0]) + 14, ly + 7, f"{v / 1e6:g}m", 20, 300, INK))
    return yb


def stat_block(o: list, x0: float, top: float, heading: str, big: str, big_sub: str, chg: str, chg_sub: str,
               chg_col: str, note: str = "") -> None:
    """Big-number panel: label where a panel label sits (36 px above `top`), level, change, optional note."""
    o.append(_T(x0, top - getattr(rc, "LABEL_GAP", 36), heading, 20, 400, GREY))
    o.append(_T(x0, top + 22, big, 44, 700, INK, extra=' letter-spacing="-0.7"'))
    o.append(_T(x0, top + 52, big_sub, 20, 300, GREY))
    o.append(_T(x0, top + 112, chg, 44, 700, chg_col, extra=' letter-spacing="-0.7"'))
    o.append(_T(x0, top + 142, chg_sub, 20, 300, GREY))
    if note:
        o.append(_T(x0, top + 182, note, 20, 400, INK))


def _chg_col(v: float) -> str:
    return BLUE if v > 0.05 else RED if v < -0.05 else INK


def pick_labels(points: list, n_big: int = 4, n_ext: int = 2) -> list:
    """Label candidates in priority order: the largest metros, the strongest and weakest, then a few more by size
    (fallbacks when a label has no room). Owner (21 Oct 2026): no clutter, label only the handful that matter."""
    by_pop = sorted(points, key=lambda p: -p.pop)
    by_val = sorted(points, key=lambda p: -p.value)
    order = by_pop[:n_big] + by_val[:n_ext] + by_val[::-1][:n_ext] + by_pop[n_big:10]
    seen, out = set(), []
    for p in order:
        if id(p) not in seen:
            seen.add(id(p))
            out.append(p)
    return out


# ═══════════════════════════════════════════════════════════════════════
# Zillow (ZHVI wide file, ZORI long file)
# ═══════════════════════════════════════════════════════════════════════
def _ts(d):
    import pandas as pd
    return pd.Timestamp(d)


def zhvi_months() -> list[str]:
    import duckdb
    cols = [c[0] for c in duckdb.connect().execute(f"DESCRIBE SELECT * FROM '{ZHVI_FILE}'").fetchall()]
    return sorted(c for c in cols if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c))


def zillow_frame(kind: str):
    """(metros DataFrame [RegionName, StateName, SizeRank, cur, prev], US Series, latest Timestamp) for ZHVI/ZORI.
    Latest = the newest month in the file (the lake refresher does not record it for wide files)."""
    import duckdb
    import pandas as pd
    con = duckdb.connect()
    if kind == "zhvi":
        months = zhvi_months()
        last = months[-1]
        prev = (pd.Timestamp(last) - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
        if prev not in months:
            raise RuntimeError(f"ZHVI: no column for {prev}, a year before {last}")
        df = con.execute(f"SELECT RegionName, StateName, SizeRank, RegionType, \"{last}\" AS cur, \"{prev}\" AS prev "
                         f"FROM '{ZHVI_FILE}'").df()
        sel = ", ".join(f'"{m}"' for m in months if m >= "2014-01-01")
        us = con.execute(f"SELECT {sel} FROM '{ZHVI_FILE}' WHERE RegionType = 'country'").df()
        s = pd.Series(us.iloc[0].values.astype(float), index=pd.to_datetime(us.columns), name="US")
        lt = pd.Timestamp(last)
    else:
        lt = pd.Timestamp(con.execute(f"SELECT max(date) FROM '{ZORI_FILE}'").fetchone()[0])
        pv = lt - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)
        df = con.execute(f"SELECT RegionName, any_value(StateName) AS StateName, any_value(SizeRank) AS SizeRank, "
                         f"any_value(RegionType) AS RegionType, "
                         f"max(zori) FILTER (WHERE date = ?) AS cur, max(zori) FILTER (WHERE date = ?) AS prev "
                         f"FROM '{ZORI_FILE}' WHERE date IN (?, ?) GROUP BY RegionName",
                         [lt.to_pydatetime(), pv.to_pydatetime(), lt.to_pydatetime(), pv.to_pydatetime()]).df()
        us = con.execute(f"SELECT date, zori FROM '{ZORI_FILE}' WHERE RegionType = 'country' ORDER BY date").df()
        s = pd.Series(us["zori"].astype(float).values, index=pd.to_datetime(us["date"]), name="US")
    # month-start index so release_charts' regular-calendar helpers (yoy, asfreq MS) apply
    s.index = s.index.to_period("M").to_timestamp()
    return df, s, lt


def zillow_points(kind: str, n: int = N_METROS):
    """Top-n contiguous-US metros by 2024 population with both months, as MapPoints, plus diagnostics."""
    df, us, lt = zillow_frame(kind)
    m = df[(df.RegionType == "msa") & (df.SizeRank <= n * 2 + 40)].copy()
    hit, miss = match_metros(list(zip(m.RegionName, m.StateName)))
    pts, notes = [], {"unmatched": miss, "missing_values": [], "dropped_ak_hi": []}
    for r in m.itertuples():
        c = hit.get(r.RegionName)
        if not c:
            continue
        if r.StateName in ("AK", "HI"):
            notes["dropped_ak_hi"].append(r.RegionName)
            continue
        if not (r.cur == r.cur and r.prev == r.prev and r.prev):
            notes["missing_values"].append(r.RegionName)
            continue
        pts.append(MapPoint(r.RegionName.split(",")[0], c["lon"], c["lat"], (r.cur / r.prev - 1) * 100, c["pop"]))
    pts.sort(key=lambda p: -p.pop)
    pts = pts[:n]
    notes["n"] = len(pts)
    return pts, us, lt, notes


def _zillow_build(kind: str):
    pts, us, lt, notes = zillow_points(kind)
    if notes["unmatched"]:
        logger.warning(f"{kind}: Zillow metros with no CBSA match: {notes['unmatched']}")
    t = _ts(lt).to_period("M").to_timestamp()
    y_us = rc.yoy(us)
    v = rc.cur(y_us, us)
    lvl = us.iloc[-1]
    falling = sum(1 for p in pts if p.value < 0)
    best = max(pts, key=lambda p: p.value)
    worst = min(pts, key=lambda p: p.value)
    edges, cols, labs = diverging_bins([p.value for p in pts])
    for i, p in enumerate(pick_labels(pts)):
        p.label = i + 1
    noun, what = ("home values", "typical home value") if kind == "zhvi" else ("rents", "typical asking rent")
    mon, my = MONTHS[t.month - 1], month_year(t)
    if v != v:
        title = f"Zillow {noun}, {my}"
    elif abs(v) < 0.05:
        title = f"US {noun} were flat over the year to {mon}"
    else:
        title = f"US {noun} {_verb(v)} {abs(v):.1f}% over the year to {mon}"
    lvl_s = f"${lvl:,.0f}"
    n = len(pts)

    # owner, 21 Oct 2026 ("All of these need to be small, square-sized. They can't be super long."): the map sits on
    # the left at reduced width with the US level and the US change (columns) beside it, instead of the stat block
    # and line panel stacked under a full-width map (1217 px tall before, about 630 now). The change is in the title,
    # so the stat block keeps only the level.
    map_w = 560

    def extra(o, y):
        yb = draw_metro_map(o, GL, y, map_w, pts, edges, cols, labs, heading="Change from a year earlier",
                            sub=f"{n} largest metros, sized by population", rmax=24)
        rx0 = GL + map_w + 28
        hy = y + 116
        o.append(_T(rx0, hy, "United States", 20, 400, GREY))
        o.append(_T(rx0, hy + 46, lvl_s, 44, 700, INK, extra=' letter-spacing="-0.7"'))
        o.append(_T(rx0, hy + 76, what, 20, 400, GREY))
        top = hy + 76 + 46 + getattr(rc, "LABEL_GAP", 36)
        bot = yb - 30
        draw_panel(o, Panel([Line(since(y_us, "2019-01"), BLUE, kind="bar")], "US, change from a year earlier",
                            ysuffix="%", zero=True, show_key=False), rx0, GR, top, bot)
        return yb + 10                         # x labels sit 30 below `bot` = level with the map's bottom edge

    extra_h = 0
    if kind == "zhvi":
        cap = (f"Zillow's typical US home value was {lvl_s} in {my}, {_signed(v)} from a year earlier. Among the {n} "
               f"largest metros, {falling} were lower than a year before; {worst.name} fell most ({_signed(worst.value)}) "
               f"and {best.name} rose most ({_signed(best.value)}).")
        src = "Source: Zillow Home Value Index; Census population. Contiguous US only."
    else:
        cap = (f"Zillow's typical US asking rent was {lvl_s} in {my}, {_signed(v)} from a year earlier. Among the {n} "
               f"largest metros, {falling} were lower than a year before; {worst.name} fell most ({_signed(worst.value)}) "
               f"and {best.name} rose most ({_signed(best.value)}).")
        src = "Source: Zillow Observed Rent Index; Census population. Contiguous US only."
    return [], extra, extra_h, t, title, cap, src


def b_zillow_zhvi():
    return _zillow_build("zhvi")


def b_zillow_zori():
    return _zillow_build("zori")


def zhvi_latest_period() -> str:
    return zhvi_months()[-1][:7]


def zori_latest_period() -> str:
    import duckdb
    return str(duckdb.connect().execute(f"SELECT max(date) FROM '{ZORI_FILE}'").fetchone()[0])[:7]


# ═══════════════════════════════════════════════════════════════════════
# Cotality HPI: article page + Infogram JSON -> lake
# ═══════════════════════════════════════════════════════════════════════
COT_BASE = "https://www.cotality.com/insights/articles/us-home-price-insights-"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0 Safari/537.36"}
_MON_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)} | {m.lower(): i + 1 for i, m in enumerate(MON3)} | \
           {"sept": 9}


class CotalityTemplateError(RuntimeError):
    """The page no longer has the layout the parser expects. Fail loudly."""


def _get(url: str, tries: int = 3) -> str:
    import httpx
    last = None
    for a in range(tries):
        try:
            r = httpx.get(url, headers=UA, follow_redirects=True, timeout=60)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            r.raise_for_status()
            return r.text
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (a + 1))
    raise RuntimeError(f"GET {url}: {last}")


def cotality_slugs() -> list[tuple[str, int, int]]:
    """(slug, release year, release month) for every HPI edition in the sitemap, oldest first."""
    xml = _get("https://www.cotality.com/sitemap.xml")
    out = set()
    for m in re.finditer(r"us-home-price-insights-([a-z]+)-(\d{4})(?=[<\"/?#\s])", xml):
        mon = _MON_IDX.get(m.group(1))
        if mon:
            out.add((f"us-home-price-insights-{m.group(1)}-{m.group(2)}", int(m.group(2)), mon))
    return sorted(out, key=lambda s: (s[1], s[2]))


def _cells(sheet) -> list:
    return [[(c.get("value") if isinstance(c, dict) else c) for c in row] for row in sheet if row]


def _sheets_and_labels(d) -> tuple[list, list]:
    sheets, labels = [], []

    def walk(o):
        if isinstance(o, dict):
            dat = o.get("data")
            if isinstance(dat, list) and dat and isinstance(dat[0], list):
                sheets.extend(_cells(s) for s in dat)
            for k, v in o.items():
                if k == "text" and isinstance(v, str):
                    t = _html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
                    if t:
                        labels.append(t)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d.get("elements", d))
    return sheets, labels


def _pct(s, need_sign: bool = False) -> float | None:
    """First number in a cell ("1.8%", "-0.1", "12.5% YOY, ..."). Older editions omit the % sign."""
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%" if need_sign else r"(-?\d+(?:\.\d+)?)", str(s or "").replace("−", "-"))
    return float(m.group(1)) if m else None


def parse_cotality(slug: str, ry: int, rm: int, pause: float = 1.0) -> tuple[dict, list]:
    """One edition -> (release row, metro rows). Raises CotalityTemplateError when the national numbers or the
    top/bottom tables cannot be found."""
    url = COT_BASE + slug.split("us-home-price-insights-", 1)[1]
    page = _get(url)
    srcs = list(dict.fromkeys(re.findall(r'src="(https://e\.infogram\.com/(?:_/)?[A-Za-z0-9-]+)', page)))
    if not srcs:
        raise CotalityTemplateError(f"{slug}: no Infogram charts on the page")
    body = re.sub(r"<(script|style).*?</\1>", " ", page, flags=re.S | re.I)
    txt = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
    rel = {"release_month": f"{ry:04d}-{rm:02d}", "slug": slug, "url": url}
    metros, tables = [], []
    labels_all = []
    for src in srcs:
        time.sleep(pause)
        g = _get(src)
        m = re.search(r"window\.infographicData\s*=\s*(\{.*?\});\s*</script>", g, flags=re.S)
        if not m:
            continue
        sheets, labels = _sheets_and_labels(json.loads(m.group(1)))
        labels_all += labels
        for sh in sheets:
            head = [str(c or "").strip().lower() for c in sh[0]] if sh else []
            if len(head) >= 3 and head[1] == "mom" and head[2] == "yoy":
                for row in sh[1:]:
                    tag = str(row[0] or "").strip().lower()
                    if tag.startswith("cur"):             # "Current" ("Currrent" in July 2025)
                        rel["mom"], rel["yoy"] = _pct(row[1]), _pct(row[2])
                    elif tag.startswith("forecast"):
                        rel["fc_mom"], rel["fc_yoy"] = _pct(row[1]), _pct(row[2])
            elif sh and len(sh[0]) >= 4 and re.search(r"YOY", str(sh[0][1] or ""), re.I) and \
                    re.search(r"-?\d+\.\d+[ ,]+-?\d+\.\d+", str(sh[0][3] or "")):
                tab = []
                for row in sh:
                    a = str(row[1] or "")
                    ll = re.findall(r"-?\d+\.\d+", str(row[3] or ""))
                    yy = re.search(r"(-?\d+(?:\.\d+)?)\s*%?\s*YOY", a, re.I)       # "12.9 YOY" lacks the %
                    y3 = re.search(r"YOY,\s*(-?\d+(?:\.\d+)?)\s*%?\s*\(([^)]*)\)", a, re.I)
                    if len(ll) < 2 or not yy:
                        continue
                    tab.append({"name": str(row[0]).strip(), "yoy": float(yy.group(1)),
                                "chg3m": float(y3.group(1)) if y3 else None,
                                "chg3m_window": y3.group(2).strip() if y3 else None, "lat": float(ll[0]),
                                "lon": float(ll[1]), "order": len(tab)})
                if tab:
                    tables.append(tab)
                    metros += tab
    if rel.get("yoy") is None:
        raise CotalityTemplateError(f"{slug}: national MoM/YoY chart (header MoM, YoY) not found")
    # data month, in order of preference: the prose "x% year-over-year in June 2026" / "price growth dipped to
    # 1.4% in July 2025"; the chart label "From August 2025 - August 2026"; the end of the metro tables' 3-month
    # window "(Apr - June)"; release month minus two
    dm, how = None, None
    pm = (re.search(r"(-?\d+(?:\.\d+)?)%\s*year[- ]over[- ]year in ([A-Za-z]+),? (\d{4})", txt, re.I) or
          re.search(r"year[- ]over[- ]year (?:home )?price (?:growth|appreciation|gains?)\b[^.%]{0,50}?"
                    r"(-?\d+(?:\.\d+)?)% in ([A-Za-z]+),? (\d{4})", txt, re.I))
    rel["prose_yoy"] = float(pm.group(1)) if pm else None
    if pm and pm.group(2).lower() in _MON_IDX:
        dm, how = (int(pm.group(3)), _MON_IDX[pm.group(2).lower()]), "article text"
    for lb in ([] if dm else labels_all):
        mm = re.search(r"From\s+([A-Za-z]+)\s+(\d{4})\s*[-–]\s*([A-Za-z]+)\s+(\d{4})", lb)
        if mm and mm.group(3).lower() in _MON_IDX:
            dm, how = (int(mm.group(4)), _MON_IDX[mm.group(3).lower()]), "chart label"
            break
    if not dm and metros and metros[0].get("chg3m_window"):
        end = metros[0]["chg3m_window"].split("-")[-1].strip().lower()
        if end in _MON_IDX:
            mo = _MON_IDX[end]
            dm, how = ((ry if mo < rm else ry - 1), mo), "metro table window"
    if not dm:
        k = ry * 12 + rm - 1 - 2
        dm, how = (k // 12, k % 12 + 1), "release month minus two"
    rel["data_month_source"] = how
    rel["data_month"] = f"{dm[0]:04d}-{dm[1]:02d}"
    for lb in labels_all:
        if re.fullmatch(r"\$\d{3},\d{3}", lb) and "median_price" not in rel:
            rel["median_price"] = float(lb[1:].replace(",", ""))
    rel["yoy_note"] = None
    if rel["prose_yoy"] is not None and abs(rel["prose_yoy"] - rel["yoy"]) > 0.05:
        if abs(abs(rel["prose_yoy"]) - abs(rel["yoy"])) <= 0.05:
            # September 2025 edition: chart says -1.4, article says "dipped to 1.4% in July 2025"
            rel["yoy_note"] = f"chart showed {rel['yoy']}; sign taken from the article text ({rel['prose_yoy']})"
            rel["yoy"] = rel["prose_yoy"]
        else:
            rel["yoy_note"] = f"article text says {rel['prose_yoy']}; chart value kept"
        logger.warning(f"cotality {slug}: {rel['yoy_note']}")
    # top / bottom tables: the top list runs from the highest value down, the bottom list from the lowest up
    rows = []
    for tb in tables:
        grp = "top" if tb[0]["yoy"] >= tb[-1]["yoy"] and tb[0]["yoy"] > 0 else "bottom"
        for r in tb:
            rows.append({"release_month": rel["release_month"], "data_month": rel["data_month"], "group": grp,
                         "rank": r["order"] + 1, **{k: r[k] for k in ("name", "yoy", "chg3m", "chg3m_window", "lat", "lon")}})
    rel["n_metros"] = len(rows)
    rel["retrieved"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return rel, rows


def _write_parquet(df, path: Path, order: str, sidecar: dict) -> None:
    import duckdb
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    con = duckdb.connect()
    con.register("d", df)
    con.execute(f"COPY (SELECT * FROM d ORDER BY {order}) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD, "
                f"ROW_GROUP_SIZE 100000)")
    tmp.replace(path)
    Path(str(path) + ".source.json").write_text(json.dumps(sidecar, indent=1))


_COT_CHECKED = 0.0


def cotality_update(max_new: int = 40, pause: float = 1.0, backfill: bool = False) -> dict:
    """Fetch sitemap editions newer than the newest one in the lake (every missing edition when `backfill` or the
    lake is empty), oldest first. Returns {"added": [...], "failed": {...}}. The newest edition failing to parse
    raises CotalityTemplateError (a 404 there only means the page is not live yet); older ones are recorded as
    failed. Editions before July 2025 have no Infogram charts and June 2025 lacks the national chart (21 Oct 2026)."""
    global _COT_CHECKED
    import duckdb
    import pandas as pd
    have = set()
    rel_df = met_df = None
    if COT_RELEASES.exists():
        rel_df = duckdb.connect().execute(f"SELECT * FROM '{COT_RELEASES}'").df()
        have = set(rel_df["release_month"])
    if COT_METROS.exists():
        met_df = duckdb.connect().execute(f"SELECT * FROM '{COT_METROS}'").df()
    slugs = cotality_slugs()
    if not slugs:
        raise CotalityTemplateError("no us-home-price-insights editions in the sitemap")
    newest = max(have) if have else ""
    todo = [s for s in slugs if (k := f"{s[1]:04d}-{s[2]:02d}") not in have and (backfill or not have or k > newest)]
    todo = todo[-max_new:]
    added, failed, new_rel, new_met = [], {}, [], []
    for slug, ry, rm in todo:
        try:
            rel, rows = parse_cotality(slug, ry, rm, pause=pause)
        except FileNotFoundError as e:
            failed[slug] = f"404 (not live yet?): {e}"
            continue
        except CotalityTemplateError as e:
            failed[slug] = str(e)
            if (slug, ry, rm) == slugs[-1]:
                raise CotalityTemplateError(f"newest edition {slug} did not parse: {e}") from e
            continue
        new_rel.append(rel)
        new_met += rows
        added.append(rel["release_month"])
        time.sleep(pause)
    if new_rel:
        cols = ["release_month", "data_month", "data_month_source", "yoy", "yoy_note", "mom", "fc_yoy", "fc_mom", "median_price",
                "prose_yoy", "n_metros", "slug", "url", "retrieved"]
        nr = pd.DataFrame(new_rel).reindex(columns=cols)
        rel_df = nr if rel_df is None else pd.concat([rel_df[~rel_df.release_month.isin(nr.release_month)], nr])
        side = {"type": "scrape", "source": "Cotality US Home Price Insights article pages (Infogram chart JSON)",
                "url_pattern": COT_BASE + "<releasemonth>-<yyyy>", "key": "release_month",
                "notes": "yoy/mom = 'Current' row of the national chart; fc_* = 'Forecast' row; data_month from the "
                         "article text, else the chart label, else the metro tables' 3-month window (data_month_source "
                         "says which). yoy_note records a chart value corrected from the article text (Sep 2025 "
                         "edition: sign typo). Values as published in each release (later releases revise earlier "
                         "months; each row keeps its own release's figure). Editions before Jul 2025 are not "
                         "machine-readable (no Infogram charts); Jun 2025 lacks the national chart.",
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "refreshed_by": "pulse/editor/release_maps.py"}
        _write_parquet(rel_df, COT_RELEASES, "release_month", side)
        if new_met:
            nm = pd.DataFrame(new_met)
            met_df = nm if met_df is None else pd.concat([met_df[~met_df.release_month.isin(nm.release_month)], nm])
            _write_parquet(met_df, COT_METROS, "release_month, \"group\", rank",
                           dict(side, source="Cotality top/bottom 10 metros by YoY (Infogram chart JSON)",
                                key="release_month, group, rank"))
    _COT_CHECKED = time.time()
    return {"added": added, "failed": failed, "editions_in_sitemap": len(slugs)}


def _cot_frames(update: bool = True):
    import duckdb
    if update and time.time() - _COT_CHECKED > 600:      # once per 10 minutes per process
        try:
            r = cotality_update()
            if r["added"] or r["failed"]:
                logger.info(f"cotality: added {r['added']}, failed {list(r['failed'])}")
        except CotalityTemplateError:
            raise
        except Exception as e:  # noqa: BLE001 (network down: chart from the lake as it is)
            if not COT_RELEASES.exists():
                raise
            logger.warning(f"cotality: update failed ({e}); using the lake copy")
    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM '{COT_RELEASES}' ORDER BY release_month").df()
    met = con.execute(f"SELECT * FROM '{COT_METROS}' ORDER BY release_month").df() if COT_METROS.exists() else None
    return rel, met


def cotality_latest_period() -> str:
    rel, _ = _cot_frames(update=True)
    return str(rel.iloc[-1]["data_month"])


def cotality_cbsa(names: list[str]) -> dict:
    """Cotality metro names ("Youngstown, OH") -> CBSA codes: the map's Zillow-style name keys first, then a CBSA
    whose title has the same first city and lists the state among its states (Cotality's "Wheeling, OH" is the
    CBSA "Wheeling, WV-OH"). Unmatched names are left out (the strip drops and reports them)."""
    hit, miss = match_metros([(n, "") for n in names])
    out = {n: c["cbsa"] for n, c in hit.items()}
    for n in miss:
        city, _, st = n.rpartition(", ")
        for c in geo()["cbsa"]:
            cp, _, sp = c["name"].rpartition(", ")
            if re.split(r"[-/]", cp)[0].strip() == city.strip() and st.strip() in sp.split("-"):
                out[n] = c["cbsa"]
                logger.info(f"cotality: {n} matched to CBSA {c['name']} by city and state")
                break
    return out


def b_cotality_hpi():
    import pandas as pd
    rel, met = _cot_frames(update=True)
    last = rel.iloc[-1]
    dm = pd.Timestamp(str(last["data_month"]) + "-01")
    # national YoY as first published, one point per data month (the newest release wins a duplicate month)
    nat = rel.sort_values("release_month").drop_duplicates("data_month", keep="last")
    s = pd.Series(nat["yoy"].astype(float).values, index=pd.to_datetime(nat["data_month"] + "-01")).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    v, mom = float(last["yoy"]), last["mom"]
    mon, my = MONTHS[dm.month - 1], month_year(dm)
    title = (f"Cotality: US home prices were flat over the year to {mon}" if abs(v) < 0.05 else
             f"Cotality: US home prices {_verb(v)} {abs(v):.1f}% over the year to {mon}")
    rows = []
    if len(s) >= 3:
        rows.append(Row([Panel([Line(s, BLUE, kind="bar")], "US, change from a year earlier",
                               "as first published", ysuffix="%", zero=True, show_key=False)], rc.STRIP_H))
    mm = met[met.release_month == last["release_month"]] if met is not None else None
    top = mm[mm["group"] == "top"].sort_values("rank") if mm is not None else None
    bot = mm[mm["group"] == "bottom"].sort_values("rank") if mm is not None else None
    if top is None or not len(top) or bot is None or not len(bot):
        raise CotalityTemplateError(f"{last['slug']}: top/bottom metro tables missing")
    fc = last.get("fc_yoy")
    fc_s = (f" Cotality forecasts {_signed(float(fc))} over the next 12 months." if fc == fc and fc is not None else "")

    # Cotality's ten fastest-rising and ten fastest-falling metros (latest edition) as a bubble strip (owner, 21 Oct
    # 2026: metro comparisons as bubbles on a left-to-right axis, size = population, colour = region), replacing the
    # 20-row bar list that made this chart 1309 px tall
    names = [str(n) for n in top.name] + [str(n) for n in bot.name]
    yoys = [float(x) for x in top.yoy] + [float(x) for x in bot.yoy]
    codes = cotality_cbsa(names)
    vals = [(n.split(",")[0], x, codes.get(n)) for n, x in zip(names, yoys)]
    rows.append(Row([rc.Strip(rc._strip_items("cotality_hpi", vals), "Top and bottom 10 metros", "sized by population",
                              bench=(f"US {_signed(v)}", v), rmax=22)], rc.STRIP_H))
    lists, extra_h = None, 0
    cap = (f"Cotality's US home price index was {_signed(v)} from a year earlier in {my} "
           f"({_signed(float(mom))} on the month).{fc_s} Strongest metro: {top.iloc[0]['name']} "
           f"({_signed(float(top.iloc[0]['yoy']))}); weakest: {bot.iloc[0]['name']} ({_signed(float(bot.iloc[0]['yoy']))}).")
    src = "Source: Cotality Home Price Insights, as first published; Census population."
    return rows, lists, extra_h, dm, title, cap, src


# ═══════════════════════════════════════════════════════════════════════
# registry (same keys and triggers as the placeholders in release_charts.REGISTRY)
# ═══════════════════════════════════════════════════════════════════════
def _trig(key: str, fallback: str) -> str:
    r = getattr(rc, "BY_KEY", {}).get(key)
    return r.trigger if r is not None else fallback


PERIODS = {"zillow_zhvi": zhvi_latest_period, "zillow_zori": zori_latest_period,
           "cotality_hpi": cotality_latest_period}         # () -> "YYYY-MM"


def _month_ts(fn):
    def f():
        import pandas as pd
        return pd.Timestamp(fn() + "-01")
    f.__name__ = fn.__name__.replace("_period", "")
    return f


LATEST = {k: _month_ts(v) for k, v in PERIODS.items()}         # () -> Timestamp (Release.latest contract)
_HAS_LATEST = "latest" in {f.name for f in dataclass_fields(Release)}


def _rel(key, name, trigger, source, builder, intro):
    kw = {"latest": LATEST[key]} if _HAS_LATEST else {}
    r = Release(key, name, _trig(key, trigger), source, [], builder, intro=intro, **kw)
    r.latest_period = PERIODS[key]
    return r


REGISTRY = [
    _rel("cotality_hpi", "Cotality home price index", r"\bcotality\b|\bcorelogic\b",
         "Cotality Home Price Insights article pages (Infogram chart JSON), accumulated in Data/Cotality",
         b_cotality_hpi, "Cotality's {period} home price figures, charted:"),
    _rel("zillow_zhvi", "Zillow home values", r"\bZHVI\b|zillow home value|zillow.{0,40}home (price|value)s?",
         "Zillow ZHVI metro file in the data lake (refreshed daily by lakerefresh)", b_zillow_zhvi,
         "Zillow's {period} home value data, mapped:"),
    _rel("zillow_zori", "Zillow rents", r"\bZORI\b|zillow observed rent|zillow.{0,40}rents?\b",
         "Zillow ZORI metro file in the data lake (refreshed daily by lakerefresh)", b_zillow_zori,
         "Zillow's {period} rent data, mapped:"),
]


def render(key: str, out: Path) -> dict:
    """Dry render (nothing hosted or recorded): the same compose + render_png path release_charts.build() uses."""
    r = next(x for x in REGISTRY if x.key == key)
    rows, extra, extra_h, t, title, cap, src = r.builder()
    svg, H = compose(title, rows, wrap(src), extra=extra)
    dest = rc._free_path(Path(out))
    render_png(svg, H, dest)
    return {"png_path": str(dest), "period": period_of(t, r.freq), "title": title, "caption": cap, "source": src, "H": H}


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-geo", action="store_true")
    ap.add_argument("--update-cotality", action="store_true")
    ap.add_argument("--render", metavar="KEY")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.build_geo:
        print(build_geo_cache())
    if a.update_cotality:
        print(json.dumps(cotality_update(backfill=True), indent=1))
    if a.render:
        out = a.out or str(rc.DRY_DIR / f"{a.render}_map_v1.png")
        print(json.dumps(render(a.render, Path(out)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
