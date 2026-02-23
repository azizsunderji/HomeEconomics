"""Auto-query data lake for current stats relevant to today's topics.

Runs DuckDB queries against key parquet files and returns a plain-text
snapshot of real numbers that gets included in the briefing.

Data source rules:
- PRICES: Always Zillow ZHVI (Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet)
- ACTIVITY METRICS: Redfin monthly_metro.parquet (inventory, DOM, sales volume,
  % below asking, months of supply, new listings, price drops)
- Redfin settings: Property Type = All Residential, IS_SEASONALLY_ADJUSTED = true,
  year-over-year change preferred
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_LAKE = Path(os.environ.get("DATA_LAKE_PATH", "/Users/azizsunderji/Dropbox/Home Economics/Data"))

if not DATA_LAKE.exists():
    DATA_LAKE = None
else:
    logger.info(f"Data lake path: {DATA_LAKE}")

ZILLOW_METRO = "Price/Zillow/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet"
REDFIN_METRO = "Redfin/monthly_metro.parquet"


def _query(sql: str) -> list[dict]:
    """Run a DuckDB query and return results as list of dicts."""
    try:
        import duckdb
        con = duckdb.connect()
        df = con.execute(sql).df()
        return df.to_dict("records")
    except Exception as e:
        logger.warning(f"DuckDB query failed: {e}")
        return []


def _zillow_date_cols() -> list[str]:
    """Get all date columns from Zillow ZHVI (cached)."""
    if not DATA_LAKE:
        return []
    path = DATA_LAKE / ZILLOW_METRO
    if not path.exists():
        return []

    import duckdb
    con = duckdb.connect()
    cols = con.execute(f"SELECT * FROM '{path}' LIMIT 0").df().columns.tolist()
    return [c for c in cols if c.startswith("20")]


def _zillow_latest_cols() -> tuple[str, str]:
    """Get the latest and year-ago date columns from Zillow ZHVI."""
    date_cols = _zillow_date_cols()
    if len(date_cols) < 13:
        return date_cols[-1] if date_cols else "", ""
    return date_cols[-1], date_cols[-13]


def get_zillow_national_snapshot() -> str:
    """Latest national home value from Zillow ZHVI."""
    if not DATA_LAKE:
        return ""

    path = DATA_LAKE / ZILLOW_METRO
    if not path.exists():
        return ""

    latest, year_ago = _zillow_latest_cols()
    if not latest or not year_ago:
        return ""

    rows = _query(f"""
        SELECT
            "RegionName",
            "{latest}" as zhvi,
            "{year_ago}" as zhvi_year_ago,
            ROUND(("{latest}" / "{year_ago}" - 1) * 100, 1) as yoy_pct
        FROM '{path}'
        WHERE "RegionName" = 'United States'
    """)

    if not rows:
        return ""

    r = rows[0]
    return f"""ZILLOW HOME VALUES (as of {latest}):
- National ZHVI: ${r['zhvi']:,.0f} ({r['yoy_pct']:+.1f}% YoY)
  Source: Zillow ZHVI (smoothed, seasonally adjusted, all homes typical value)"""


def get_redfin_national_activity() -> str:
    """Latest national activity metrics from Redfin (non-price)."""
    if not DATA_LAKE:
        return ""

    path = DATA_LAKE / REDFIN_METRO
    if not path.exists():
        return ""

    # Redfin YOY columns: most are fractions (multiply by 100 for %),
    # EXCEPT MEDIAN_DOM_YOY which is absolute days change.
    # PRICE_DROPS, AVG_SALE_TO_LIST, SOLD_ABOVE_LIST are fractions (0.27 = 27%).
    rows = _query(f"""
        SELECT
            PERIOD_END,
            ROUND(AVG(INVENTORY_YOY) * 100, 1) as avg_inventory_yoy_pct,
            ROUND(AVG(HOMES_SOLD_YOY) * 100, 1) as avg_homes_sold_yoy_pct,
            ROUND(AVG(MEDIAN_DOM), 0) as avg_days_on_market,
            ROUND(AVG(MEDIAN_DOM_YOY), 0) as avg_dom_yoy_days,
            ROUND(AVG(MONTHS_OF_SUPPLY), 1) as avg_months_supply,
            ROUND(AVG(NEW_LISTINGS_YOY) * 100, 1) as avg_new_listings_yoy_pct,
            ROUND(AVG(PRICE_DROPS) * 100, 1) as avg_price_drops_pct,
            ROUND(AVG(AVG_SALE_TO_LIST) * 100, 1) as avg_sale_to_list_pct,
            ROUND(AVG(SOLD_ABOVE_LIST) * 100, 1) as avg_sold_above_list_pct
        FROM '{path}'
        WHERE PROPERTY_TYPE = 'All Residential'
        AND IS_SEASONALLY_ADJUSTED = true
        AND PERIOD_END = (
            SELECT MAX(PERIOD_END) FROM '{path}'
            WHERE PROPERTY_TYPE = 'All Residential' AND IS_SEASONALLY_ADJUSTED = true
        )
        GROUP BY PERIOD_END
    """)

    if not rows:
        return ""

    r = rows[0]
    period = r['PERIOD_END'].strftime('%B %Y') if hasattr(r['PERIOD_END'], 'strftime') else r['PERIOD_END']
    return f"""REDFIN ACTIVITY SNAPSHOT (as of {period}, seasonally adjusted):
- Inventory: {r['avg_inventory_yoy_pct']:+.1f}% YoY
- Homes sold: {r['avg_homes_sold_yoy_pct']:+.1f}% YoY
- New listings: {r['avg_new_listings_yoy_pct']:+.1f}% YoY
- Days on market: {r['avg_days_on_market']:.0f} ({r['avg_dom_yoy_days']:+.0f} days YoY)
- Months of supply: {r['avg_months_supply']:.1f}
- Sale-to-list ratio: {r['avg_sale_to_list_pct']:.1f}%
- Sold above list: {r['avg_sold_above_list_pct']:.1f}%
- Share with price drops: {r['avg_price_drops_pct']:.1f}%
  Source: Redfin monthly metro data (All Residential, SA)"""


def get_zillow_top_movers() -> str:
    """Metros with biggest ZHVI moves (Zillow)."""
    if not DATA_LAKE:
        return ""

    path = DATA_LAKE / ZILLOW_METRO
    if not path.exists():
        return ""

    latest, year_ago = _zillow_latest_cols()
    if not latest or not year_ago:
        return ""

    gainers = _query(f"""
        SELECT "RegionName",
            "{latest}" as zhvi,
            ROUND(("{latest}" / "{year_ago}" - 1) * 100, 1) as yoy_pct
        FROM '{path}'
        WHERE "{latest}" IS NOT NULL AND "{year_ago}" IS NOT NULL
        AND "RegionName" != 'United States'
        AND "SizeRank" <= 100
        ORDER BY ("{latest}" / "{year_ago}" - 1) DESC
        LIMIT 5
    """)

    date_cols = _zillow_date_cols()
    greatest_expr = ", ".join(f'"{c}"' for c in date_cols)

    decliners = _query(f"""
        SELECT "RegionName",
            "{latest}" as zhvi,
            ROUND(("{latest}" / "{year_ago}" - 1) * 100, 1) as yoy_pct,
            GREATEST({greatest_expr}) as peak_val,
            ROUND(("{latest}" / GREATEST({greatest_expr}) - 1) * 100, 1) as from_peak_pct
        FROM '{path}'
        WHERE "{latest}" IS NOT NULL AND "{year_ago}" IS NOT NULL
        AND "RegionName" != 'United States'
        AND "SizeRank" <= 100
        ORDER BY ("{latest}" / "{year_ago}" - 1) ASC
        LIMIT 5
    """)

    lines = [f"METRO PRICE MOVERS (Zillow ZHVI, top 100 metros, as of {latest}):"]
    lines.append("  Fastest appreciation:")
    for r in gainers:
        lines.append(f"    {r['RegionName']}: ${r['zhvi']:,.0f} ({r['yoy_pct']:+.1f}% YoY)")
    lines.append("  Biggest declines (with peak-to-current):")
    for r in decliners:
        peak_note = f", {r['from_peak_pct']:+.1f}% from peak" if r['from_peak_pct'] < -3 else ""
        lines.append(f"    {r['RegionName']}: ${r['zhvi']:,.0f} ({r['yoy_pct']:+.1f}% YoY{peak_note})")

    return "\n".join(lines)


def get_metro_detail(metro_names: list[str]) -> str:
    """Get combined Zillow price + Redfin activity for specific metros."""
    if not DATA_LAKE or not metro_names:
        return ""

    zillow_path = DATA_LAKE / ZILLOW_METRO
    redfin_path = DATA_LAKE / REDFIN_METRO

    latest, year_ago = _zillow_latest_cols()

    lines = ["METRO DETAIL (for cities mentioned in today's conversation):"]

    for metro in metro_names[:10]:
        parts = []

        # Zillow price with peak-to-current
        zrows = []
        if zillow_path.exists() and latest:
            date_cols = _zillow_date_cols()
            greatest_expr = ", ".join(f'"{c}"' for c in date_cols)
            # Find peak value and compute decline from peak
            zrows = _query(f"""
                SELECT "RegionName",
                    "{latest}" as zhvi,
                    ROUND(("{latest}" / "{year_ago}" - 1) * 100, 1) as yoy_pct,
                    GREATEST({greatest_expr}) as peak_val,
                    ROUND(("{latest}" / GREATEST({greatest_expr}) - 1) * 100, 1) as from_peak_pct
                FROM '{zillow_path}'
                WHERE "RegionName" ILIKE '%{metro}%'
                AND "{latest}" IS NOT NULL
                LIMIT 1
            """)
            if zrows:
                z = zrows[0]
                peak_note = ""
                if z['from_peak_pct'] < -3:
                    # Find peak month
                    for c in reversed(date_cols):
                        prow = _query(f"""
                            SELECT "{c}" as val FROM '{zillow_path}'
                            WHERE "RegionName" = '{z['RegionName']}'
                        """)
                        if prow and prow[0]['val'] and abs(prow[0]['val'] - z['peak_val']) < 1:
                            peak_note = f", peaked ${z['peak_val']:,.0f} in {c[:7]}, now {z['from_peak_pct']:+.1f}% from peak"
                            break
                parts.append(f"ZHVI ${z['zhvi']:,.0f} ({z['yoy_pct']:+.1f}% YoY{peak_note})")

        # Redfin activity
        if redfin_path.exists():
            rrows = _query(f"""
                SELECT REGION,
                    ROUND(INVENTORY) as inventory,
                    ROUND(INVENTORY_YOY * 100, 1) as inv_yoy,
                    ROUND(HOMES_SOLD_YOY * 100, 1) as sold_yoy,
                    ROUND(MEDIAN_DOM) as dom,
                    ROUND(MONTHS_OF_SUPPLY, 1) as mos,
                    ROUND(NEW_LISTINGS_YOY * 100, 1) as new_list_yoy,
                    ROUND(PRICE_DROPS * 100, 1) as price_drops_pct
                FROM '{redfin_path}'
                WHERE PROPERTY_TYPE = 'All Residential'
                AND IS_SEASONALLY_ADJUSTED = true
                AND PERIOD_END = (
                    SELECT MAX(PERIOD_END) FROM '{redfin_path}'
                    WHERE PROPERTY_TYPE = 'All Residential' AND IS_SEASONALLY_ADJUSTED = true
                )
                AND REGION ILIKE '%{metro}%'
                LIMIT 1
            """)
            if rrows:
                r = rrows[0]
                parts.append(
                    f"inventory {r['inventory']:,.0f} ({r['inv_yoy']:+.1f}% YoY), "
                    f"{r['dom']:.0f} DOM, {r['mos']:.1f} mo supply, "
                    f"new listings {r['new_list_yoy']:+.1f}% YoY, "
                    f"price drops {r['price_drops_pct']:.1f}%"
                )

        if parts:
            label = metro
            if zrows:
                label = zrows[0].get("RegionName", metro)
            elif rrows:
                label = rrows[0].get("REGION", metro)
            lines.append(f"  {label}: {', '.join(parts)}")

    if len(lines) == 1:
        return ""

    return "\n".join(lines)


def get_full_snapshot(mentioned_metros: list[str] | None = None) -> str:
    """Get all available data lake stats as a single text block."""
    sections = []

    zillow_national = get_zillow_national_snapshot()
    if zillow_national:
        sections.append(zillow_national)

    activity = get_redfin_national_activity()
    if activity:
        sections.append(activity)

    movers = get_zillow_top_movers()
    if movers:
        sections.append(movers)

    if mentioned_metros:
        metro_detail = get_metro_detail(mentioned_metros)
        if metro_detail:
            sections.append(metro_detail)

    if not sections:
        return "Data lake not available in this environment."

    return "\n\n".join(sections)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(get_full_snapshot(["Austin", "Miami", "Phoenix", "Boise"]))
