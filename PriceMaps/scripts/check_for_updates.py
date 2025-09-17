#!/usr/bin/env python3
"""
Check if Zillow has published new data by comparing the last column date
"""
import requests
import json
import os
from datetime import datetime
from pathlib import Path

ZILLOW_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
METADATA_FILE = Path(__file__).parent.parent / "data" / "last_update.json"

def get_latest_date_from_zillow():
    """Fetch just the header row to check the latest date column"""
    try:
        # Stream the file to avoid downloading everything
        response = requests.get(ZILLOW_URL, stream=True)
        response.raise_for_status()

        # Get just the first line (header)
        header_line = response.iter_lines().__next__().decode('utf-8')
        columns = header_line.split(',')

        # Last column should be the most recent date
        latest_date_str = columns[-1]

        # Parse date (format: YYYY-MM-DD)
        latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d")

        return latest_date_str, latest_date

    except Exception as e:
        print(f"Error fetching Zillow data: {e}")
        return None, None

def get_stored_date():
    """Get the last processed date from our metadata file"""
    if METADATA_FILE.exists():
        with open(METADATA_FILE, 'r') as f:
            metadata = json.load(f)
            return metadata.get('last_date')
    return None

def save_metadata(date_str):
    """Save the current date as processed"""
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        'last_date': date_str,
        'checked_at': datetime.now().isoformat()
    }
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2)

def main():
    """Check for updates and return exit code"""
    print("Checking for new Zillow data...")

    latest_date_str, latest_date = get_latest_date_from_zillow()

    if not latest_date_str:
        print("Failed to fetch latest date from Zillow")
        return 1

    stored_date = get_stored_date()

    if stored_date is None:
        print(f"No previous data found. Latest available: {latest_date_str}")
        print("NEW_DATA=true")
        save_metadata(latest_date_str)
        return 0

    if latest_date_str != stored_date:
        print(f"New data available! Latest: {latest_date_str}, Previous: {stored_date}")
        print("NEW_DATA=true")
        save_metadata(latest_date_str)
        return 0
    else:
        print(f"No new data. Current version: {latest_date_str}")
        print("NEW_DATA=false")
        return 0

if __name__ == "__main__":
    exit(main())