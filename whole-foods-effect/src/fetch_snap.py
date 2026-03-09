"""
Fetch and parse USDA SNAP Retailer Historical Data.

The USDA provides a CSV of every SNAP-authorized retailer since 2004, including:
  - Store name, type, address (street, city, state, zip)
  - Latitude/longitude
  - Authorization and end dates (proxy for opening/closing)

Download source:
  https://www.fns.usda.gov/snap/retailer/historical-data
  (zipped CSV, ~200MB unzipped)

Enhanced version with census geographies:
  https://github.com/jshannon75/snap_retailers
"""

import zipfile
from pathlib import Path

import pandas as pd
import requests

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

HEADERS = {"User-Agent": "HomeEconomicsResearch/1.0 (academic research project)"}

# Direct link to USDA historical SNAP retailer data
# This URL may change; check https://www.fns.usda.gov/snap/retailer/historical-data
SNAP_HISTORICAL_URL = (
    "https://fns-prod.azureedge.us/sites/default/files/resource-files/"
    "SNAP_Retailer_Locator_Historical_Data.zip"
)

# Alternative: Jerry Shannon's enhanced dataset with census geographies
SNAP_GITHUB_URL = (
    "https://github.com/jshannon75/snap_retailers/raw/master/data/"
    "snap_retailers_csv.zip"
)

# Chains we care about — patterns to match in store names
CHAIN_PATTERNS = {
    "Whole Foods": ["whole foods"],
    "Trader Joe's": ["trader joe"],
    "Wegmans": ["wegmans"],
    "Starbucks": ["starbucks"],
    "Aldi": ["aldi"],
}


def download_snap_data(
    url: str = SNAP_HISTORICAL_URL, filename: str = "snap_historical.zip"
) -> Path:
    """Download the SNAP historical retailer ZIP file."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / filename

    if dest.exists():
        print(f"  Already downloaded: {dest}")
        return dest

    print(f"  Downloading SNAP data from {url}...")
    resp = requests.get(url, headers=HEADERS, timeout=300, stream=True)
    resp.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    print(f"  Saved to {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def load_snap_csv(zip_path: Path) -> pd.DataFrame:
    """Extract and load the CSV from the SNAP ZIP file."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise FileNotFoundError(f"No CSV found in {zip_path}")

        # Use the largest CSV (the main data file)
        csv_name = max(csv_names, key=lambda n: zf.getinfo(n).file_size)
        print(f"  Loading {csv_name}...")

        with zf.open(csv_name) as f:
            df = pd.read_csv(f, dtype=str, low_memory=False)

    print(f"  Loaded {len(df):,} rows, {len(df.columns)} columns")
    print(f"  Columns: {list(df.columns)}")
    return df


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to a standard format."""
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Map common SNAP column names to our standard names
    col_map = {}
    for col in df.columns:
        if "store_name" in col or "retailer_name" in col or col == "name":
            col_map[col] = "store_name"
        elif "authorization" in col and "date" in col and "end" not in col:
            col_map[col] = "auth_date"
        elif ("end" in col and "date" in col) or "expiration" in col:
            col_map[col] = "end_date"
        elif col in ("zip", "zip_code", "zip5", "postal"):
            col_map[col] = "zip"
        elif col in ("state",):
            col_map[col] = "state"
        elif col in ("city",):
            col_map[col] = "city"
        elif col in ("address", "street", "address1", "street_address"):
            col_map[col] = "address"
        elif col in ("latitude", "lat", "y"):
            col_map[col] = "lat"
        elif col in ("longitude", "lon", "lng", "long", "x"):
            col_map[col] = "lon"
        elif "store_type" in col or "retailer_type" in col or col == "type":
            col_map[col] = "store_type"
        elif col in ("record_id", "retailer_id", "store_id", "id"):
            col_map[col] = "retailer_id"

    df = df.rename(columns=col_map)
    return df


def filter_chains(df: pd.DataFrame) -> pd.DataFrame:
    """Filter SNAP data to only our target chains."""
    if "store_name" not in df.columns:
        print("  WARNING: No 'store_name' column found. Columns:", list(df.columns))
        return pd.DataFrame()

    name_lower = df["store_name"].str.lower().fillna("")

    masks = {}
    for chain, patterns in CHAIN_PATTERNS.items():
        mask = pd.Series(False, index=df.index)
        for pat in patterns:
            mask = mask | name_lower.str.contains(pat, na=False)
        masks[chain] = mask

    combined_mask = pd.Series(False, index=df.index)
    for mask in masks.values():
        combined_mask = combined_mask | mask

    filtered = df[combined_mask].copy()

    # Add chain label
    filtered["chain"] = "Unknown"
    for chain, mask in masks.items():
        filtered.loc[mask, "chain"] = chain

    print(f"  Filtered to {len(filtered):,} rows across target chains:")
    for chain in CHAIN_PATTERNS:
        n = (filtered["chain"] == chain).sum()
        if n > 0:
            print(f"    {chain}: {n:,}")

    return filtered


def parse_auth_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Parse authorization dates to extract opening year/month."""
    if "auth_date" in df.columns:
        df["auth_date_parsed"] = pd.to_datetime(df["auth_date"], errors="coerce")
        df["open_year"] = df["auth_date_parsed"].dt.year
        df["open_month"] = df["auth_date_parsed"].dt.month
    if "end_date" in df.columns:
        df["end_date_parsed"] = pd.to_datetime(df["end_date"], errors="coerce")

    return df


def clean_zip(df: pd.DataFrame) -> pd.DataFrame:
    """Clean ZIP codes to 5-digit format."""
    if "zip" in df.columns:
        df["zip"] = df["zip"].astype(str).str.strip().str[:5]
        df["zip"] = df["zip"].where(df["zip"].str.match(r"^\d{5}$"), None)
    return df


def fetch_and_process_snap(use_github: bool = False) -> pd.DataFrame:
    """
    Full pipeline: download SNAP data, filter to target chains, clean, save.

    Args:
        use_github: If True, use jshannon75's enhanced dataset instead of
                    the official USDA file.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    output_path = PROCESSED_DIR / "snap_chain_locations.csv"
    if output_path.exists():
        print(f"  Loading cached: {output_path}")
        return pd.read_csv(output_path)

    # Download
    if use_github:
        zip_path = download_snap_data(SNAP_GITHUB_URL, "snap_github.zip")
    else:
        zip_path = download_snap_data(SNAP_HISTORICAL_URL, "snap_historical.zip")

    # Load
    df = load_snap_csv(zip_path)
    df = _normalize_columns(df)

    # Filter to our chains
    df = filter_chains(df)
    if df.empty:
        print("  No matching chains found in SNAP data.")
        return df

    # Parse dates and clean
    df = parse_auth_dates(df)
    df = clean_zip(df)

    # Select output columns
    out_cols = [
        c
        for c in [
            "chain",
            "store_name",
            "retailer_id",
            "address",
            "city",
            "state",
            "zip",
            "lat",
            "lon",
            "store_type",
            "auth_date",
            "end_date",
            "open_year",
            "open_month",
        ]
        if c in df.columns
    ]
    result = df[out_cols].copy()

    result.to_csv(output_path, index=False)
    print(f"  Saved {len(result):,} rows to {output_path}")
    return result


if __name__ == "__main__":
    print("=" * 60)
    print("USDA SNAP Retailer Historical Data — Chain Filter")
    print("=" * 60)
    print("\nAttempting official USDA source...")
    try:
        df = fetch_and_process_snap(use_github=False)
    except Exception as e:
        print(f"  Official source failed: {e}")
        print("\nFalling back to GitHub (jshannon75) source...")
        df = fetch_and_process_snap(use_github=True)

    if not df.empty:
        print(f"\nDone! {len(df):,} chain locations with authorization dates.")
    else:
        print("\nNo data retrieved. Check network access and URLs.")
