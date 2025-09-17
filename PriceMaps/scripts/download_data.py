#!/usr/bin/env python3
"""
Download the latest Zillow ZIP code level housing data
"""
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

ZILLOW_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
OUTPUT_DIR = Path(__file__).parent.parent / "data"

def download_zillow_data():
    """Download the latest Zillow data"""
    print("Downloading Zillow ZIP code data...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_file = OUTPUT_DIR / "ZillowZip.csv"

    try:
        # Download the file
        response = requests.get(ZILLOW_URL, timeout=60)
        response.raise_for_status()

        # Save to file
        with open(output_file, 'wb') as f:
            f.write(response.content)

        print(f"✓ Downloaded to {output_file}")

        # Verify the data
        df = pd.read_csv(output_file)
        print(f"✓ Loaded {len(df):,} ZIP codes")

        # Get latest date from columns
        date_columns = [col for col in df.columns if '-' in col]
        if date_columns:
            latest_date = date_columns[-1]
            print(f"✓ Latest data: {latest_date}")

        return True

    except Exception as e:
        print(f"✗ Error downloading data: {e}")
        return False

def download_reference_data():
    """
    Copy reference data (populations, shapefiles) from main location
    Note: In production, these could be stored in the repo or downloaded from a CDN
    """
    print("\nCopying reference data...")

    # For now, we'll note that these files need to be included
    # In the GitHub repo, we can either:
    # 1. Store them directly (if small enough)
    # 2. Download from a public source
    # 3. Use Git LFS for large files

    resources_dir = Path(__file__).parent.parent / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    print("ℹ️  Reference data locations:")
    print("  - Population: /Users/azizsunderji/Dropbox/Home Economics/Reference/Populations/PopulationByZIP.csv")
    print("  - Shapefile: /Users/azizsunderji/Dropbox/Home Economics/Dogs/cb_2020_us_zcta520_500k.shp")
    print("  These will need to be added to the repository")

    return True

def main():
    """Main download function"""
    success = download_zillow_data()

    if success:
        download_reference_data()
        print("\n✓ Data download complete!")
        return 0
    else:
        print("\n✗ Data download failed")
        return 1

if __name__ == "__main__":
    exit(main())