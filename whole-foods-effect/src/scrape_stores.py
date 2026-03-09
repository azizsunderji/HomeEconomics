"""
Scrape and assemble store locations + opening dates for grocery/retail chains.

Data sources (in priority order for opening dates):
  1. USDA SNAP Historical Retailer Data — authorization dates as opening proxy
     (fetch_snap.py)
  2. Wikipedia "List of ... locations" — structured tables with dates
  3. OpenStreetMap Overpass API — current locations with start_date tags
  4. Starbucks GitHub dataset — current locations (chrismeller/StarbucksLocations)
  5. Hand-curated seed data — Wegmans confirmed opening dates

Chains: Whole Foods, Trader Joe's, Wegmans, Starbucks, Aldi
"""

import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

HEADERS = {"User-Agent": "HomeEconomicsResearch/1.0 (academic research project)"}

CHAINS = ["Whole Foods", "Trader Joe's", "Wegmans", "Starbucks", "Aldi"]

# ── Wikipedia sources ────────────────────────────────────────────────────────

WIKI_PAGES = {
    "Whole Foods": "https://en.wikipedia.org/wiki/List_of_Whole_Foods_Market_locations",
    "Trader Joe's": "https://en.wikipedia.org/wiki/List_of_Trader_Joe%27s_locations",
    "Wegmans": "https://en.wikipedia.org/wiki/List_of_Wegmans_locations",
    "Starbucks": None,  # Too many; use SNAP + GitHub
    "Aldi": "https://en.wikipedia.org/wiki/Aldi",
}


def scrape_wikipedia_tables(url: str, chain: str) -> pd.DataFrame:
    """Extract store location tables from a Wikipedia page."""
    if url is None:
        return pd.DataFrame()

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Wikipedia fetch failed for {chain}: {e}")
        return pd.DataFrame()

    soup = BeautifulSoup(resp.text, "lxml")
    tables = soup.find_all("table", class_="wikitable")
    if not tables:
        print(f"  No wikitables found for {chain}")
        return pd.DataFrame()

    frames = []
    for table in tables:
        try:
            df = pd.read_html(str(table))[0]
            df.columns = [str(c).strip().lower() for c in df.columns]
            frames.append(df)
        except Exception as e:
            print(f"  Could not parse table for {chain}: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["chain"] = chain
    return combined


def _extract_zip(text: str) -> str | None:
    """Pull a 5-digit ZIP code from a string."""
    m = re.search(r"\b(\d{5})\b", str(text))
    return m.group(1) if m else None


def _extract_year(text: str) -> int | None:
    """Pull a 4-digit year from a string."""
    m = re.search(r"\b(19|20)\d{2}\b", str(text))
    return int(m.group(0)) if m else None


def clean_wikipedia_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize scraped Wikipedia tables into (chain, city, state, zip, open_year)."""
    if df.empty:
        return df

    zip_col = date_col = location_col = state_col = city_col = None

    for col in df.columns:
        cl = col.lower()
        if "zip" in cl or "postal" in cl:
            zip_col = col
        if "open" in cl or "date" in cl or "year" in cl:
            date_col = col
        if "location" in cl or "address" in cl:
            location_col = col
        if cl in ("state", "state/territory"):
            state_col = col
        if cl in ("city", "city/town", "town"):
            city_col = col

    # Extract ZIPs
    if zip_col:
        df["zip"] = df[zip_col].apply(_extract_zip)
    elif location_col:
        df["zip"] = df[location_col].apply(_extract_zip)
    else:
        for col in df.columns:
            if col in ("chain",):
                continue
            zips = df[col].apply(_extract_zip)
            if zips.notna().sum() > len(df) * 0.1:
                df["zip"] = zips
                break

    # Extract opening year
    if date_col:
        df["open_year"] = df[date_col].apply(_extract_year)
    else:
        df["open_year"] = None

    # Extract city/state if available
    if city_col and "city" not in df.columns:
        df["city"] = df[city_col].astype(str).str.strip()
    if state_col and "state" not in df.columns:
        df["state"] = df[state_col].astype(str).str.strip()

    if "zip" not in df.columns:
        df["zip"] = None

    keep = ["chain"]
    for c in ["city", "state", "zip", "open_year"]:
        if c in df.columns:
            keep.append(c)

    return df[keep].dropna(subset=["zip"])


# ── OpenStreetMap Overpass API ───────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Updated queries: request full tags (including start_date, addr:postcode)
OVERPASS_QUERIES = {
    "Whole Foods": (
        '[out:json][timeout:180];'
        'area["ISO3166-1"="US"]->.us;'
        '(node["shop"="supermarket"]["name"~"Whole Foods",i](area.us);'
        ' way["shop"="supermarket"]["name"~"Whole Foods",i](area.us););'
        'out center tags;'
    ),
    "Trader Joe's": (
        '[out:json][timeout:180];'
        'area["ISO3166-1"="US"]->.us;'
        '(node["shop"="supermarket"]["name"~"Trader Joe",i](area.us);'
        ' way["shop"="supermarket"]["name"~"Trader Joe",i](area.us););'
        'out center tags;'
    ),
    "Wegmans": (
        '[out:json][timeout:180];'
        'area["ISO3166-1"="US"]->.us;'
        '(node["shop"="supermarket"]["name"~"Wegmans",i](area.us);'
        ' way["shop"="supermarket"]["name"~"Wegmans",i](area.us););'
        'out center tags;'
    ),
    "Starbucks": (
        '[out:json][timeout:180];'
        'area["ISO3166-1"="US"]->.us;'
        '(node["amenity"="cafe"]["name"~"Starbucks",i](area.us);'
        ' way["amenity"="cafe"]["name"~"Starbucks",i](area.us););'
        'out center tags;'
    ),
    "Aldi": (
        '[out:json][timeout:180];'
        'area["ISO3166-1"="US"]->.us;'
        '(node["shop"="supermarket"]["name"~"Aldi",i](area.us);'
        ' way["shop"="supermarket"]["name"~"Aldi",i](area.us););'
        'out center tags;'
    ),
}


def query_overpass(chain: str) -> pd.DataFrame:
    """Query Overpass API for current store locations with full tags."""
    query = OVERPASS_QUERIES.get(chain)
    if not query:
        return pd.DataFrame()

    print(f"  Querying Overpass for {chain}...")
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Overpass query failed for {chain}: {e}")
        return pd.DataFrame()

    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        # For ways, lat/lon is in center
        lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
        lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")

        row = {
            "chain": chain,
            "osm_id": el.get("id"),
            "osm_type": el.get("type"),
            "name": tags.get("name", ""),
            "lat": lat,
            "lon": lon,
            "zip": tags.get("addr:postcode", ""),
            "city": tags.get("addr:city", ""),
            "state": tags.get("addr:state", ""),
            "address": tags.get("addr:street", ""),
            "housenumber": tags.get("addr:housenumber", ""),
            "start_date": tags.get("start_date", ""),
            "opening_date": tags.get("opening_date", ""),
            "phone": tags.get("phone", ""),
            "website": tags.get("website", ""),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    if not df.empty:
        # Clean ZIP to 5 digits
        df["zip"] = df["zip"].astype(str).str.strip().str[:5]
        df["zip"] = df["zip"].where(df["zip"].str.match(r"^\d{5}$"), "")

        # Parse start_date to year
        df["open_year"] = df["start_date"].apply(_extract_year)

        n_with_zip = (df["zip"] != "").sum()
        n_with_date = df["open_year"].notna().sum()
        print(f"  OSM: {len(df)} locations, {n_with_zip} with ZIP, {n_with_date} with start_date")

    return df


# ── Seed data ────────────────────────────────────────────────────────────────

def load_seed_data() -> pd.DataFrame:
    """Load hand-curated opening date data."""
    seed_path = RAW_DIR / "wegmans_openings_seed.csv"
    if seed_path.exists():
        df = pd.read_csv(seed_path)
        print(f"  Loaded {len(df)} seed records from {seed_path}")
        return df
    return pd.DataFrame()


# ── Assembly ─────────────────────────────────────────────────────────────────

def scrape_all(use_overpass: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run the full scraping pipeline for all chains.

    Returns:
        (wiki_df, osm_df) — Wikipedia-sourced and OSM-sourced DataFrames.
        Also loads SNAP and seed data as side effects (saved to processed/).
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # ── Wikipedia ──
    all_wiki = []
    for chain in CHAINS:
        url = WIKI_PAGES.get(chain)
        if url:
            print(f"Scraping Wikipedia for {chain}...")
            raw = scrape_wikipedia_tables(url, chain)
            if not raw.empty:
                raw.to_csv(
                    RAW_DIR / f"wiki_{chain.lower().replace(' ', '_').replace(chr(39), '')}.csv",
                    index=False,
                )
                cleaned = clean_wikipedia_data(raw)
                all_wiki.append(cleaned)
                print(f"  Wikipedia: {len(cleaned)} locations with ZIPs")

    wiki_df = pd.concat(all_wiki, ignore_index=True) if all_wiki else pd.DataFrame()

    # ── Overpass (OSM) ──
    all_osm = []
    if use_overpass:
        for chain in CHAINS:
            osm = query_overpass(chain)
            if not osm.empty:
                osm.to_csv(
                    RAW_DIR / f"osm_{chain.lower().replace(' ', '_').replace(chr(39), '')}.csv",
                    index=False,
                )
                all_osm.append(osm)
            time.sleep(10)  # Rate-limit

    osm_df = pd.concat(all_osm, ignore_index=True) if all_osm else pd.DataFrame()

    # ── Seed data (Wegmans confirmed dates) ──
    seed_df = load_seed_data()

    # ── SNAP data (run separately via fetch_snap.py) ──
    snap_path = PROCESSED_DIR / "snap_chain_locations.csv"
    if snap_path.exists():
        snap_df = pd.read_csv(snap_path)
        print(f"  SNAP data available: {len(snap_df):,} rows")

    # ── Save intermediate outputs ──
    if not wiki_df.empty:
        wiki_df.to_csv(PROCESSED_DIR / "store_locations_wiki.csv", index=False)
    if not osm_df.empty:
        osm_df.to_csv(PROCESSED_DIR / "store_locations_osm.csv", index=False)
    if not seed_df.empty:
        seed_df.to_csv(PROCESSED_DIR / "store_locations_seed.csv", index=False)

    n_wiki = len(wiki_df)
    n_osm = len(osm_df)
    n_seed = len(seed_df)
    print(f"\nScraping complete. Wikipedia: {n_wiki}, OSM: {n_osm}, Seed: {n_seed}")
    return wiki_df, osm_df


if __name__ == "__main__":
    scrape_all()
