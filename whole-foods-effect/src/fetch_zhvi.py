"""
Download and reshape Zillow Home Value Index (ZHVI) at ZIP level.

Source: Zillow Research Data
  https://www.zillow.com/research/data/

Uses the ZHVI All Homes (SFR, Condo/Co-op) Time Series, Smoothed, Seasonally Adjusted.
"""

from pathlib import Path

import pandas as pd
import requests

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# Zillow publishes this CSV publicly
ZHVI_URL = (
    "https://files.zillowstatic.com/research/public_csvs/zhvi/"
    "Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
)


def download_zhvi(force: bool = False) -> Path:
    """Download the ZHVI ZIP-level CSV if not already cached."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / "zhvi_zip.csv"

    if dest.exists() and not force:
        print(f"ZHVI already downloaded: {dest}")
        return dest

    print("Downloading ZHVI ZIP-level data...")
    resp = requests.get(ZHVI_URL, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"Saved to {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def load_and_reshape(path: Path | None = None) -> pd.DataFrame:
    """
    Load the wide-format ZHVI CSV and melt to long format:
      RegionName (ZIP) | date | zhvi
    """
    if path is None:
        path = RAW_DIR / "zhvi_zip.csv"

    df = pd.read_csv(path, dtype={"RegionName": str})

    # Identify date columns (formatted YYYY-MM-DD)
    date_cols = [c for c in df.columns if len(c) == 10 and c[4] == "-"]
    id_cols = ["RegionID", "RegionName", "SizeRank", "RegionType", "StateName",
               "State", "City", "Metro", "CountyName"]
    id_cols = [c for c in id_cols if c in df.columns]

    long = df.melt(
        id_vars=id_cols,
        value_vars=date_cols,
        var_name="date",
        value_name="zhvi",
    )

    long["date"] = pd.to_datetime(long["date"])
    long["zip"] = long["RegionName"].str.zfill(5)

    # Drop rows without home values
    long = long.dropna(subset=["zhvi"])

    print(f"ZHVI long format: {len(long):,} rows, "
          f"{long['zip'].nunique():,} ZIPs, "
          f"{long['date'].min().date()} to {long['date'].max().date()}")

    return long


def process_zhvi(force_download: bool = False) -> pd.DataFrame:
    """Full pipeline: download → reshape → save processed."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    path = download_zhvi(force=force_download)
    long = load_and_reshape(path)

    out = PROCESSED_DIR / "zhvi_long.parquet"
    long.to_parquet(out, index=False)
    print(f"Saved processed ZHVI to {out}")

    return long


if __name__ == "__main__":
    process_zhvi()
