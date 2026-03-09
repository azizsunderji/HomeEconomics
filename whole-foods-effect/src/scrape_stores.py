"""
Scrape store locations and opening dates for grocery/retail chains.

Sources:
  1. Wikipedia "List of ... locations" pages (often include opening dates)
  2. OpenStreetMap Overpass API for current locations (lat/lon → ZIP)

Chains: Whole Foods, Trader Joe's, Wegmans, Starbucks, Aldi
"""

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# ── Wikipedia sources ────────────────────────────────────────────────────────

WIKI_PAGES = {
    "Whole Foods": "https://en.wikipedia.org/wiki/List_of_Whole_Foods_Market_locations",
    "Trader Joe's": "https://en.wikipedia.org/wiki/List_of_Trader_Joe%27s_locations",
    "Wegmans": "https://en.wikipedia.org/wiki/Wegmans#Store_locations",
    "Starbucks": None,  # Too many locations; rely on OSM
    "Aldi": "https://en.wikipedia.org/wiki/Aldi#United_States",
}

HEADERS = {
    "User-Agent": "HomeEconomicsResearch/1.0 (academic research project)"
}


def scrape_wikipedia_tables(url: str, chain: str) -> pd.DataFrame:
    """Extract store location tables from a Wikipedia page."""
    if url is None:
        return pd.DataFrame()

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
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
            continue

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
    """Pull a 4-digit year from a string (opening date)."""
    m = re.search(r"\b(19|20)\d{2}\b", str(text))
    return int(m.group(0)) if m else None


def clean_wikipedia_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize scraped Wikipedia tables into (chain, zip, open_year) rows."""
    if df.empty:
        return df

    # Try to identify ZIP and opening-date columns heuristically
    zip_col = None
    date_col = None
    location_col = None

    for col in df.columns:
        cl = col.lower()
        if "zip" in cl or "postal" in cl:
            zip_col = col
        if "open" in cl or "date" in cl or "year" in cl:
            date_col = col
        if "location" in cl or "address" in cl or "city" in cl or "store" in cl:
            location_col = col

    # Extract ZIPs
    if zip_col:
        df["zip"] = df[zip_col].apply(_extract_zip)
    elif location_col:
        df["zip"] = df[location_col].apply(_extract_zip)
    else:
        # Try all columns
        for col in df.columns:
            zips = df[col].apply(_extract_zip)
            if zips.notna().sum() > 0:
                df["zip"] = zips
                break

    # Extract opening year
    if date_col:
        df["open_year"] = df[date_col].apply(_extract_year)
    else:
        df["open_year"] = None

    if "zip" not in df.columns:
        df["zip"] = None

    return df[["chain", "zip", "open_year"]].dropna(subset=["zip"])


# ── OpenStreetMap Overpass API ───────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_QUERIES = {
    "Whole Foods": '[out:json][timeout:120];area["ISO3166-1"="US"]->.us;node["shop"="supermarket"]["name"~"Whole Foods"](area.us);out center;',
    "Trader Joe's": '[out:json][timeout:120];area["ISO3166-1"="US"]->.us;node["shop"="supermarket"]["name"~"Trader Joe"](area.us);out center;',
    "Wegmans": '[out:json][timeout:120];area["ISO3166-1"="US"]->.us;node["shop"="supermarket"]["name"~"Wegmans"](area.us);out center;',
    "Starbucks": '[out:json][timeout:120];area["ISO3166-1"="US"]->.us;node["amenity"="cafe"]["name"~"Starbucks"](area.us);out center;',
    "Aldi": '[out:json][timeout:120];area["ISO3166-1"="US"]->.us;node["shop"="supermarket"]["name"~"Aldi"](area.us);out center;',
}


def query_overpass(chain: str) -> pd.DataFrame:
    """Query Overpass API for current store locations."""
    query = OVERPASS_QUERIES.get(chain)
    if not query:
        return pd.DataFrame()

    print(f"  Querying Overpass for {chain}...")
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Overpass query failed for {chain}: {e}")
        return pd.DataFrame()

    rows = []
    for el in data.get("elements", []):
        rows.append(
            {
                "chain": chain,
                "lat": el.get("lat"),
                "lon": el.get("lon"),
                "name": el.get("tags", {}).get("name", ""),
                "osm_id": el.get("id"),
            }
        )

    return pd.DataFrame(rows)


def geocode_to_zip(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert lat/lon to ZIP codes using a reverse-geocoding approach.

    For a production pipeline, use Census ZCTA shapefiles for spatial join.
    This stub adds a placeholder — the match_controls module handles the
    spatial join with proper ZCTA geometries.
    """
    if df.empty or "lat" not in df.columns:
        return df

    # We'll do the proper spatial join in match_controls.py using ZCTA shapefiles.
    # For now, just keep lat/lon — they'll be joined to ZIPs later.
    return df


# ── Main ─────────────────────────────────────────────────────────────────────

CHAINS = ["Whole Foods", "Trader Joe's", "Wegmans", "Starbucks", "Aldi"]


def scrape_all(use_overpass: bool = True) -> pd.DataFrame:
    """Run the full scraping pipeline for all chains."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    all_wiki = []
    all_osm = []

    for chain in CHAINS:
        print(f"Scraping {chain}...")

        # Wikipedia
        url = WIKI_PAGES.get(chain)
        if url:
            raw = scrape_wikipedia_tables(url, chain)
            if not raw.empty:
                raw.to_csv(RAW_DIR / f"wiki_{chain.lower().replace(' ', '_')}.csv", index=False)
                cleaned = clean_wikipedia_data(raw)
                all_wiki.append(cleaned)
                print(f"  Wikipedia: {len(cleaned)} locations with ZIPs")

        # Overpass
        if use_overpass:
            osm = query_overpass(chain)
            if not osm.empty:
                osm.to_csv(RAW_DIR / f"osm_{chain.lower().replace(' ', '_')}.csv", index=False)
                osm = geocode_to_zip(osm)
                all_osm.append(osm)
                print(f"  OSM: {len(osm)} locations")

            # Rate-limit between Overpass queries
            time.sleep(10)

    wiki_df = pd.concat(all_wiki, ignore_index=True) if all_wiki else pd.DataFrame()
    osm_df = pd.concat(all_osm, ignore_index=True) if all_osm else pd.DataFrame()

    # Save intermediate outputs
    if not wiki_df.empty:
        wiki_df.to_csv(PROCESSED_DIR / "store_locations_wiki.csv", index=False)
    if not osm_df.empty:
        osm_df.to_csv(PROCESSED_DIR / "store_locations_osm.csv", index=False)

    print(f"\nDone. Wikipedia: {len(wiki_df)} rows, OSM: {len(osm_df)} rows.")
    return wiki_df, osm_df


if __name__ == "__main__":
    scrape_all()
