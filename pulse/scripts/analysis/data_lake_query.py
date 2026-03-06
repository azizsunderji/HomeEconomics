"""Dynamic data lake querying for claim verification.

Two-pass system:
1. Haiku reads claims + compact data catalog → outputs DuckDB queries + FRED lookups
2. We execute queries → return results for the briefing

This replaces the fixed Zillow+Redfin snapshot with dynamic querying
across the full data lake, supplemented by live FRED API calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import anthropic
import duckdb
import httpx

logger = logging.getLogger(__name__)

DATA_LAKE = Path(os.environ.get("DATA_LAKE_PATH", "/Users/azizsunderji/Dropbox/Home Economics/Data"))
MODEL = "claude-haiku-4-5-20251001"  # Use Haiku for query generation (fast + cheap)
MAX_QUERIES = 8
MAX_RESULT_ROWS = 25
FRED_API_KEY = os.environ.get("FRED_API_KEY", "936c7e9922072dbab8e2632a67e93ac9")

# Compact schema of the key datasets in the data lake.
# This is what Sonnet sees to decide what queries to run.
# Intentionally concise — just enough to write correct SQL.
DATA_LAKE_SCHEMA = """
## Key Datasets (DuckDB parquet, root = $DATA)

### Prices (Zillow ZHVI — multiple geographic levels and property types)
- `Price/Zillow/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet`
  METRO-level ZHVI (SFR+Condo combined, mid-tier). Wide format: RegionName (metro, format "Austin, TX" or "United States"),
  SizeRank, ~300 date columns from "2000-01-31" through "2026-01-31" (monthly). Use ILIKE for metro search.
- `Price/Zillow/State_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet`
  STATE-level ZHVI (SFR+Condo combined, mid-tier). Wide format: RegionName (state name like "Florida", "Texas").
  Same date columns as metro. Use for state-level price claims.
- `Price/Zillow/State_zhvi_uc_condo_sm_sa_month.parquet`
  STATE-level ZHVI (CONDO ONLY). Wide format: RegionName (state name). Use for condo-specific state claims
  like "Florida condo owners lost $X" — compare date columns for YoY or peak-to-current.
- `Price/Zillow/Metro_zhvi_uc_condo_sm_sa_month.csv`
  METRO-level ZHVI (CONDO ONLY). CSV wide format. For metro condo claims.

- `Price/FHFA/hpi_metro_quarterly.parquet`
  Columns: cbsa_code, cbsa_name, year, quarter, hpi (index, base=100). Quarterly house price index.

### Mortgage Distribution (FHFA National Mortgage Database)
- `FHFA_NMDB/nmdb_outstanding_quarterly.parquet`
  Outstanding mortgage statistics from FHFA/CFPB National Mortgage Database, quarterly 2013Q1-2025Q3.
  Columns: SOURCE, FREQUENCY, GEOLEVEL, GEOID, GEONAME, MARKET, PERIOD, YEAR, QUARTER, SERIESID, VALUE1.
  Filter: GEOLEVEL='National' (or 'State'), MARKET='All Mortgages'.
  Key series for mortgage rate distribution:
  - PCT_INTRATE_LT_3: % of mortgages with rate below 3%
  - PCT_INTRATE_3_4: % with rate 3-4%
  - PCT_INTRATE_4_5: % with rate 4-5%
  - PCT_INTRATE_5_6: % with rate 5-6%
  - PCT_INTRATE_GE_6: % with rate 6%+
  Also has: AVE_INTRATE, AVE_MTMLTV, PCT_TERM_FRM_30, PCT_TENURE_* (loan age), PCT_VS_* (credit score buckets).
  Use for claims like "X% of homeowners have a rate above 6%" or "lock-in effect" analysis.

### Activity (Redfin) — non-price metrics only
- `Redfin/monthly_metro.parquet`
  Columns: REGION, PERIOD_END, PROPERTY_TYPE, IS_SEASONALLY_ADJUSTED, INVENTORY, INVENTORY_YOY (fraction),
  HOMES_SOLD, HOMES_SOLD_YOY (fraction), MEDIAN_DOM, MEDIAN_DOM_YOY (absolute days), MONTHS_OF_SUPPLY,
  NEW_LISTINGS, NEW_LISTINGS_YOY (fraction), PRICE_DROPS (fraction), AVG_SALE_TO_LIST (fraction),
  SOLD_ABOVE_LIST (fraction). Filter: PROPERTY_TYPE='All Residential', IS_SEASONALLY_ADJUSTED=true.
  YOY columns are fractions (multiply ×100 for %). EXCEPT MEDIAN_DOM_YOY which is absolute days.

### Migration
- `State_Migration/state_to_state_migration_2005_2024.parquet`
  Columns: year, origin (full state name like "California"), destination (full state name), flow (number of people), moe.
  No 2020 data (Census skipped it). Years: 2005-2019, 2021-2024.
  Net migration = SUM(flow WHERE destination=state) - SUM(flow WHERE origin=state AND origin!=destination).
  For red/blue state analysis, use the state_party CTE below.

### Population
- `PopulationEstimates/state_v2025.parquet`
  State population estimates. Check columns with: SELECT * FROM '...' LIMIT 1
- `PopulationEstimates/metro_cbsa_v2024.parquet`
  Metro CBSA population estimates. Check columns with: SELECT * FROM '...' LIMIT 1
- `PopulationEstimates/county_v2024.parquet`
  County population estimates.

### Surveys
- `ACS_1Y/acs_1y.parquet` (2.7 GB — ALWAYS filter with WHERE)
  American Community Survey 1-year microdata 2005-2024. Person-level records.
  Key columns: YEAR, STATEFIP, COUNTYFIP, PUMA, OWNERSHP (1=owned,2=rented), VALUEH (house value),
  RENT, HHINCOME, AGE, RACE, EDUC, EMPSTAT, TRANWORK (commute mode), TRANTIME, MIGRATE1 (moved last year),
  NCHILD (number of own children in household), ROOMS (number of rooms in dwelling), BEDROOMS.
  Weight with HHWT (household) or PERWT (person).
  Generation proxy: use AGE column + YEAR to derive birth year (YEAR - AGE). Boomers: born 1946-1964, GenX: 1965-1980, Millennials: 1981-1996, GenZ: 1997+.
  VALUEH gotcha: values 0 and 9999999 are missing/NA — filter them out. HHINCOME 9999999 is also NA.
  "Large homes": typically 4+ bedrooms (BEDROOMS >= 4) or 7+ rooms (ROOMS >= 7).

- `GSS/gss_cumulative.parquet`
  General Social Survey cumulative file. Attitudes, demographics, social trends.

### Macro / FRED (LIVE API — preferred for macro claims)
  Use FRED API for any macro/economic series. Common series IDs:
  Housing:
  - EXHOSLUSM495S: Existing Home Sales (SAAR, monthly) — from NAR
  - MORTGAGE30US: 30-Year Fixed Mortgage Rate (weekly)
  - CSUSHPINSA: Case-Shiller National Home Price Index (monthly)
  - MSPUS: Median Sales Price of Houses Sold (quarterly)
  - MSACSR: Monthly Supply of New Houses (monthly)
  - NHSDPTS: New Home Sales (SAAR, monthly)
  - HOUST: Housing Starts (SAAR, monthly)
  - PERMIT: Building Permits (SAAR, monthly)
  - RRVRUSQ156N: Rental Vacancy Rate (quarterly)
  - RHORUSQ156N: Homeownership Rate (quarterly)
  Labor/Macro:
  - UNRATE: Unemployment Rate (monthly)
  - PAYEMS: Total Nonfarm Payrolls (monthly, thousands)
  - ICSA: Initial Jobless Claims (weekly)
  - GDP: Gross Domestic Product (quarterly, billions)
  - GDPC1: Real GDP (quarterly, billions chained 2017$)
  Inflation/Prices:
  - CPIAUCSL: Consumer Price Index for All Urban Consumers (monthly)
  - CPILFESL: Core CPI (Less Food and Energy, monthly)
  - PCEPI: PCE Price Index (monthly)
  - PCEPILFE: Core PCE (Less Food and Energy, monthly)
  Manufacturing/Business:
  - MANEMP: Manufacturing Employment (monthly, thousands)
  - NAPM: ISM Manufacturing PMI (monthly)
  - NAPMNOI: ISM Manufacturing New Orders (monthly)
  - NAPMPI: ISM Manufacturing Prices Paid (monthly)
  - NAPMSDI: ISM Manufacturing Supplier Deliveries (monthly)
  - NAPMII: ISM Manufacturing Inventories (monthly)
  - NMFCI: ISM Non-Manufacturing/Services PMI (monthly)
  Financial:
  - DGS10: 10-Year Treasury Yield (daily)
  - DGS2: 2-Year Treasury Yield (daily)
  - T10Y2Y: 10Y-2Y Treasury Spread (daily)
  - DEXUSEU: USD/EUR Exchange Rate (daily)
  - SP500: S&P 500 Index (daily)
  Consumer:
  - UMCSENT: University of Michigan Consumer Sentiment (monthly)
  - RSXFS: Retail Sales ex Food Services (monthly, millions)
  - TOTALSA: Total Vehicle Sales (SAAR, monthly)

  To query: use type "fred" with series_id. Returns last 24 months.
  You can use ANY valid FRED series ID, not just the ones listed above.
  If you know the series ID for a dataset, use it. FRED has 800,000+ series.

### Crosswalks
- `Crosswalks/` — various geographic crosswalk files for joining datasets.

### Political Reference: Presidential Election Results (2020, 2024)
- `Politics/state_party_2020_2024.parquet`
  STATE-LEVEL party winners. Columns: statefip (int), state_name (full), state_abbr, winner_2020 ('D'/'R'), winner_2024 ('D'/'R').
  51 rows (50 states + DC). 2024: 20 D states, 31 R states.
  Join with ACS: `ON acs.STATEFIP = sp.statefip`
  Join with state_migration: `ON migration.origin = sp.state_name` (or destination)
  Use winner_2024 for "red state / blue state" claims unless the claim specifically references 2020.

- `Politics/puma_votes_2020.parquet`
  PUMA-LEVEL 2020 presidential vote counts. 2,462 rows (every PUMA in the US).
  Columns: statefip (int), pumace20 (str), puma_name, biden (int), trump (int), other (int), total (int), biden_pct (float), trump_pct (float).
  Source: VEST 2020 precinct shapefiles spatially overlaid onto Census 2020 PUMA boundaries (area-weighted).
  Use for sub-state political analysis. Join with ACS: `ON acs.STATEFIP = pv.statefip AND CAST(acs.PUMA AS VARCHAR) = pv.pumace20`
  Classify PUMAs as red/blue: `CASE WHEN trump_pct > 50 THEN 'R' ELSE 'D' END`
  This is more precise than state-level for claims like "people in red areas have fewer children" since
  it captures within-state variation (e.g., blue PUMAs in Texas, red PUMAs in California).
"""


def _safe_query(sql: str) -> list[dict]:
    """Execute a DuckDB query against the data lake, with safety limits."""
    # Replace $DATA with actual path
    sql = sql.replace("$DATA", str(DATA_LAKE))

    # Safety: block writes, drops, etc. Use regex word boundaries to avoid
    # false positives (e.g., "DROPBOX" matching "DROP")
    sql_upper = sql.upper().strip()
    for kw in ["DROP TABLE", "DROP VIEW", "DELETE FROM", "INSERT INTO", "UPDATE ", "CREATE TABLE", "CREATE INDEX", "ALTER ", "COPY "]:
        if kw in sql_upper:
            return [{"error": f"Write operation '{kw.strip()}' not allowed"}]

    try:
        con = duckdb.connect()
        # Add row limit if not present
        if "LIMIT" not in sql_upper:
            sql = sql.rstrip().rstrip(";") + f" LIMIT {MAX_RESULT_ROWS}"
        df = con.execute(sql).df()
        # Truncate large results
        if len(df) > MAX_RESULT_ROWS:
            df = df.head(MAX_RESULT_ROWS)
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


def _fred_query(series_id: str, periods: int = 24) -> list[dict]:
    """Fetch recent observations from FRED API."""
    try:
        resp = httpx.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "api_key": FRED_API_KEY,
                "series_id": series_id,
                "file_type": "json",
                "sort_order": "desc",
                "limit": periods,
            },
            timeout=15,
        )
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        return [{"date": o["date"], "value": o["value"]} for o in obs if o.get("value") != "."]
    except Exception as e:
        return [{"error": f"FRED API error for {series_id}: {e}"}]


def generate_claim_queries(
    claims_text: str,
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """Given notable claims from the conversation, generate DuckDB queries to verify them.

    Args:
        claims_text: Text describing the claims to verify (from Pass 1 of synthesis)
        client: Anthropic client

    Returns:
        List of {claim, query, result} dicts
    """
    client = client or anthropic.Anthropic()

    prompt = f"""You are a data analyst. Given these claims, verify each one using FRED API lookups and/or DuckDB queries against the data lake.

## IMPORTANT: USE FRED FIRST
FRED has 800,000+ time series and is live/current. For ANY claim about:
- Mortgage rates → FRED: MORTGAGE30US
- Home sales → FRED: EXHOSLUSM495S, NHSDPTS
- Housing starts/permits → FRED: HOUST, PERMIT
- GDP/recession → FRED: GDP, GDPC1
- Unemployment/jobs → FRED: UNRATE, PAYEMS, ICSA
- Inflation/CPI → FRED: CPIAUCSL, CPILFESL, PCEPI, PCEPILFE
- ISM/manufacturing → FRED: NAPM, NAPMPI, NAPMNOI, MANEMP
- Consumer sentiment → FRED: UMCSENT
- Treasury yields → FRED: DGS10, DGS2, T10Y2Y
- Any other macro/economic series → try FRED first (you can use ANY valid FRED series ID)
FRED data is live and always current. NEVER say "data not available" for macro claims without trying FRED.

## Data Lake Schema (for DuckDB parquet queries)
{DATA_LAKE_SCHEMA}

## Claims to Verify
{claims_text}

## Instructions
- For each claim, choose the BEST data source:
  * FRED API for macro/economic claims (rates, sales, GDP, inflation, employment, etc.)
  * DuckDB parquet for geographic/demographic claims (metro prices, migration, ACS, condo prices, mortgage distribution)
  * Use BOTH if the claim spans macro + geographic (e.g., "Austin prices fell while rates rose")
- Write 1-2 queries per claim
- Return ONLY a JSON array, no explanation.
- For FRED lookups: {{"claim": "...", "type": "fred", "series_id": "MORTGAGE30US"}}
- For DuckDB queries: {{"claim": "...", "type": "duckdb", "query": "SELECT ..."}}
- Use $DATA as the root path prefix for all parquet files
- Always include LIMIT clauses in SQL
- For Zillow wide-format data, the latest date column is approximately "2026-01-31"
- For migration net flows: SUM inflows - SUM outflows
- IMPORTANT: If a claim has a POLITICAL dimension (e.g., "red states vs blue states", "Republican-led states",
  "Democratic states"), you MUST verify BOTH the underlying data AND the political angle:
  * For state-level claims: JOIN with Politics/state_party_2020_2024.parquet on statefip, GROUP BY winner_2024
  * For sub-state or ACS-based claims: JOIN with Politics/puma_votes_2020.parquet on statefip+PUMA,
    classify PUMAs as red/blue (trump_pct > 50 = 'R'), and GROUP BY that classification.
    PUMA-level is more precise since it captures within-state variation.
  Don't just check the data without the partisan breakdown.
- Max {MAX_QUERIES} queries total
- If a claim can't be verified with available data, skip it
- Use standard single quotes for SQL string literals (e.g., WHERE state = 'Texas')
- Do NOT double single quotes — DuckDB uses standard SQL quoting
- For CTEs with UNION ALL to define state lists, use VALUES ('Texas'), ('California') syntax instead of UNION ALL
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # Parse JSON from response — handle code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            if "```" in text:
                text = text[:text.index("```")]

        # Try parsing
        try:
            queries = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find the JSON array in the text
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    queries = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse query JSON even with fallback")
                    return []
            else:
                return []

        return queries[:MAX_QUERIES]

    except Exception as e:
        logger.error(f"Failed to generate claim queries: {e}")
        return []


def run_claim_verification(
    claims_text: str,
    client: anthropic.Anthropic | None = None,
) -> str:
    """Full pipeline: generate queries for claims, execute them, return formatted results.

    Args:
        claims_text: Description of claims to verify
        client: Anthropic client

    Returns:
        Formatted text block with query results for each claim
    """
    client = client or anthropic.Anthropic()
    queries = generate_claim_queries(claims_text, client)

    if not queries:
        return "No verifiable claims identified."

    results = []
    for q in queries:
        claim = q.get("claim", "Unknown claim")
        query_type = q.get("type", "duckdb")

        if query_type == "fred":
            series_id = q.get("series_id", "")
            if not series_id:
                continue
            logger.info(f"FRED lookup for {series_id}: {claim[:60]}...")
            rows = _fred_query(series_id)
            source_label = f"FRED:{series_id}"
        else:
            sql = q.get("query", "")
            if not sql:
                continue
            # Fix common Haiku SQL issues: doubled single quotes in string literals
            sql = re.sub(r"''([^']+)''", r"'\1'", sql)
            logger.info(f"Running data check query for: {claim[:60]}...")
            rows = _safe_query(sql)
            source_label = f"QUERY: {sql}"

        # Format result compactly
        if rows and "error" not in rows[0]:
            result_lines = []
            for row in rows[:10]:
                parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                result_lines.append("  " + ", ".join(parts))
            result_text = "\n".join(result_lines)
        elif rows and "error" in rows[0]:
            result_text = f"  Query error: {rows[0]['error']}"
        else:
            result_text = "  No results"

        results.append(f'CLAIM: "{claim}"\n{source_label}\nRESULT:\n{result_text}')

    return "\n\n".join(results)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test with a sample claim
    test_claims = """
    1. "Austin home prices are down 25% from peak" (circulating in bearish housing communities)
    2. "Red states completely dominate domestic migration" (r/neoliberal citing Wikipedia)
    3. "Mortgage rates are the highest since 2008" (Twitter)
    """
    print(run_claim_verification(test_claims))
