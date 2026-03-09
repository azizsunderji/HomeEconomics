"""
Fetch Starbucks store locations from chrismeller/StarbucksLocations on GitHub.

This repo contains a daily-updated CSV of all Starbucks locations worldwide.
By examining git history, we can infer approximate opening dates for stores
that appeared after a given date.

Source: https://github.com/chrismeller/StarbucksLocations
Format: CSV with columns including store name, address, city, state, zip, lat, lon

For historical opening dates, this dataset alone isn't sufficient — combine
with USDA SNAP authorization dates (fetch_snap.py) for a richer timeline.
"""

from pathlib import Path

import pandas as pd
import requests

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

HEADERS = {"User-Agent": "HomeEconomicsResearch/1.0 (academic research project)"}

# Raw CSV of all current Starbucks locations
STARBUCKS_CSV_URL = (
    "https://raw.githubusercontent.com/chrismeller/StarbucksLocations/"
    "master/directory.csv"
)

# Alternative: Kaggle dataset (requires auth)
# https://www.kaggle.com/datasets/omarsobhy14/starbucks-store-location-2023


def download_starbucks_csv() -> Path:
    """Download the Starbucks locations CSV from GitHub."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / "starbucks_directory.csv"

    if dest.exists():
        print(f"  Already downloaded: {dest}")
        return dest

    print(f"  Downloading Starbucks locations...")
    resp = requests.get(STARBUCKS_CSV_URL, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    dest.write_bytes(resp.content)
    print(f"  Saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def load_and_filter_us(csv_path: Path) -> pd.DataFrame:
    """Load the Starbucks CSV and filter to US locations."""
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    print(f"  Loaded {len(df):,} worldwide locations")
    print(f"  Columns: {list(df.columns)}")

    # Filter to US — the column name varies by dataset version
    country_col = None
    for col in df.columns:
        if "country" in col:
            country_col = col
            break

    if country_col:
        us_mask = df[country_col].str.strip().str.upper().isin(["US", "USA", "UNITED STATES"])
        df = df[us_mask].copy()
        print(f"  Filtered to {len(df):,} US locations")

    # Normalize key columns
    col_map = {}
    for col in df.columns:
        if col in ("postcode", "postal_code", "zip", "zip_code"):
            col_map[col] = "zip"
        elif col in ("latitude", "lat"):
            col_map[col] = "lat"
        elif col in ("longitude", "lon", "lng"):
            col_map[col] = "lon"
        elif col in ("store_name", "name", "store_number"):
            col_map[col] = "store_name"
        elif col in ("state", "state_province", "stateabbr"):
            col_map[col] = "state"
        elif col in ("city",):
            col_map[col] = "city"
        elif col in ("street_address", "address", "street"):
            col_map[col] = "address"

    df = df.rename(columns=col_map)
    df["chain"] = "Starbucks"

    # Clean ZIP
    if "zip" in df.columns:
        df["zip"] = df["zip"].astype(str).str.strip().str[:5]
        df["zip"] = df["zip"].where(df["zip"].str.match(r"^\d{5}$"), None)

    return df


def fetch_starbucks() -> pd.DataFrame:
    """Full pipeline: download, filter to US, clean, save."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    output_path = PROCESSED_DIR / "starbucks_locations.csv"
    if output_path.exists():
        print(f"  Loading cached: {output_path}")
        return pd.read_csv(output_path)

    csv_path = download_starbucks_csv()
    df = load_and_filter_us(csv_path)

    out_cols = [c for c in ["chain", "store_name", "address", "city", "state",
                             "zip", "lat", "lon"] if c in df.columns]
    result = df[out_cols]
    result.to_csv(output_path, index=False)
    print(f"  Saved {len(result):,} US Starbucks to {output_path}")
    return result


if __name__ == "__main__":
    fetch_starbucks()
