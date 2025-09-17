#!/usr/bin/env python3
"""
Update CES historical data with recent months only
Preserves existing historical data and only fetches last 2 years for updates
"""

import json
import time
import os
from datetime import datetime
import requests
from typing import Dict, List, Any
from pathlib import Path

# Import all series IDs and names
try:
    from all_series import CES_SERIES, INDUSTRY_NAMES
except ImportError:
    exec(open(os.path.join(os.path.dirname(__file__), 'all_series.py')).read())

# Import hierarchy functions from the historical fetch script
try:
    from fetch_ces_historical_data import determine_hierarchy_level, determine_level_from_id, get_order_from_id
except ImportError:
    # Define them inline if import fails
    def determine_hierarchy_level(series_id: str, all_series_set: set = None) -> Dict[str, Any]:
        """Determine the hierarchy level and parent from series ID"""
        # CES format: CES + supersector(2) + industry(6) + datatype(2)
        if len(series_id) < 13:
            return {"level": "unknown", "parent": None}

        supersector = series_id[3:5]
        industry_code = series_id[5:11]

        # Special cases for top-level aggregates
        if series_id == "CES0000000001":
            return {"level": "total", "parent": None, "order": 0}
        elif series_id == "CES0500000001":
            return {"level": "major", "parent": "CES0000000001", "order": 1}
        elif series_id == "CES0600000001":
            return {"level": "major", "parent": "CES0500000001", "order": 2}
        elif series_id == "CES0700000001":
            return {"level": "major", "parent": "CES0000000001", "order": 3}
        elif series_id == "CES0800000001":
            return {"level": "major", "parent": "CES0500000001", "order": 4}
        elif series_id == "CES9000000001":
            return {"level": "major", "parent": "CES0000000001", "order": 100}

        # Regular hierarchy
        if industry_code == "000000":
            # This is a supersector
            if supersector in ["10", "20", "30", "31", "32", "40", "50", "55", "60", "65", "70", "80"]:
                # These are under private
                parent = "CES0800000001" if supersector not in ["10", "20", "30", "31", "32"] else "CES0600000001"
                return {"level": "supersector", "parent": parent, "order": int(supersector)}
            else:
                return {"level": "supersector", "parent": "CES0500000001", "order": int(supersector)}
        else:
            # This is an industry - find its proper parent
            parent_id = f"CES{supersector}00000001"  # Default to supersector
            return {"level": "industry", "parent": parent_id, "order": 999}

# BLS API configuration
BLS_API_KEY = os.environ.get('BLS_API_KEY', 'a7d81877b6374d11a6b7e15fa63b5f9b')
BLS_API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'

def fetch_from_bls(series_ids: List[str], start_year: int, end_year: int) -> Dict[str, Any]:
    """Fetch data from BLS API with retry logic"""

    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
        "registrationkey": BLS_API_KEY
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(BLS_API_URL, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data['status'] == 'REQUEST_SUCCEEDED':
                return data
            else:
                print(f"API request failed: {data.get('message', 'Unknown error')}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue

        except Exception as e:
            print(f"Request error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue

    return {"Results": {"series": []}}

def main():
    print("CES Historical Data Updater")
    print("="*60)

    # Load existing historical data
    historical_file = Path(__file__).parent.parent / 'data' / 'ces_historical_data.json'

    if not historical_file.exists():
        print(f"ERROR: Historical data file not found at {historical_file}")
        print("Please run fetch_ces_historical_data.py first to create the base historical data.")
        return 1

    print(f"Loading existing historical data from {historical_file}")
    with open(historical_file, 'r') as f:
        historical_data = json.load(f)

    print(f"  Loaded {historical_data['meta']['series_count']} series")
    print(f"  Current range: {historical_data['meta']['start_date']} to {historical_data['meta']['end_date']}")

    # Determine update range (last 2 years to ensure we capture all recent data)
    current_year = datetime.now().year
    update_start_year = current_year - 1

    print(f"\nFetching recent data: {update_start_year} to {current_year}")
    print("-"*60)

    # Fetch recent data in batches
    batch_size = 50
    all_recent_data = {"Results": {"series": []}}

    for i in range(0, len(CES_SERIES), batch_size):
        batch = CES_SERIES[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(CES_SERIES) + batch_size - 1) // batch_size

        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} series)...", end='', flush=True)
        batch_data = fetch_from_bls(batch, update_start_year, current_year)

        if batch_data.get('Results', {}).get('series'):
            all_recent_data['Results']['series'].extend(batch_data['Results']['series'])
            print(f" ✓")
        else:
            print(f" ✗")

        # Rate limiting
        if i + batch_size < len(CES_SERIES):
            time.sleep(0.5)

    # Merge recent data into historical data
    print(f"\nMerging recent data into historical dataset...")

    # Create lookup for existing series
    series_by_id = {s['id']: s for s in historical_data['series']}

    updated_count = 0
    new_points = 0

    for series in all_recent_data['Results']['series']:
        series_id = series['seriesID']

        if series_id in series_by_id:
            existing_series = series_by_id[series_id]

            # Add new data points
            for item in series.get('data', []):
                if item['period'].startswith('M'):  # Only monthly data
                    date_str = f"{item['year']}-{item['period'][1:].zfill(2)}-01"
                    try:
                        value = float(item['value'])

                        # Only update if this is new data or different from existing
                        if date_str not in existing_series['data'] or existing_series['data'][date_str] != value:
                            existing_series['data'][date_str] = value
                            new_points += 1

                    except (ValueError, KeyError):
                        continue

            # Update latest date for this series
            if existing_series['data']:
                dates = list(existing_series['data'].keys())
                existing_series['latest'] = max(dates)
                existing_series['point_count'] = len(dates)
                updated_count += 1

    # Update metadata
    all_dates = set()
    all_years = set()
    total_points = 0

    for series in historical_data['series']:
        if series.get('data'):
            dates = series['data'].keys()
            all_dates.update(dates)
            for date in dates:
                all_years.add(int(date[:4]))
                total_points += 1

    if all_dates:
        historical_data['meta']['start_date'] = min(all_dates)
        historical_data['meta']['end_date'] = max(all_dates)

    if all_years:
        historical_data['meta']['earliest_year'] = min(all_years)
        historical_data['meta']['latest_year'] = max(all_years)

    historical_data['meta']['total_data_points'] = total_points
    historical_data['meta']['generated'] = datetime.now().isoformat()
    historical_data['meta']['last_update'] = datetime.now().isoformat()

    # Save updated data
    print(f"\nSaving updated historical data...")
    with open(historical_file, 'w') as f:
        json.dump(historical_data, f, separators=(',', ':'))

    file_size_mb = historical_file.stat().st_size / 1024 / 1024
    print(f"✓ Updated data saved ({file_size_mb:.1f} MB)")
    print(f"  Updated {updated_count} series with {new_points} new data points")
    print(f"  New range: {historical_data['meta']['start_date']} to {historical_data['meta']['end_date']}")

    # Also generate/update recession periods
    print(f"\nUpdating recession periods...")
    os.system(f"cd {Path(__file__).parent} && python3 process_recessions.py")

    print("\n✓ Update complete!")

if __name__ == '__main__':
    main()