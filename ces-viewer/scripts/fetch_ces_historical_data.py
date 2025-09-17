#!/usr/bin/env python3
"""
Fetch Historical Current Employment Statistics (CES) data from BLS API
Fetches data from 1939 to present in chunks to avoid API limits
"""

import json
import time
import argparse
import os
from datetime import datetime
import requests
from typing import Dict, List, Any
from pathlib import Path

# Import all series IDs and names
try:
    from all_series import CES_SERIES, INDUSTRY_NAMES
except ImportError:
    # Fallback to embedded list if import fails
    exec(open(os.path.join(os.path.dirname(__file__), 'all_series.py')).read())

# BLS API configuration
BLS_API_KEY = os.environ.get('BLS_API_KEY', 'a7d81877b6374d11a6b7e15fa63b5f9b')
BLS_API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'

# Define year ranges (BLS API allows max 20 years per request)
YEAR_RANGES = [
    (1939, 1959),  # Earliest CES data starts 1939
    (1960, 1979),
    (1980, 1999),
    (2000, 2019),
    (2020, datetime.now().year)  # Current period
]

def fetch_from_bls(series_ids: List[str], start_year: int, end_year: int) -> Dict[str, Any]:
    """Fetch data from BLS API with retry logic"""

    # Ensure we don't exceed 20-year limit
    if end_year - start_year > 20:
        print(f"Warning: Range {start_year}-{end_year} exceeds 20 years, truncating")
        end_year = start_year + 20

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

def merge_series_data(existing_data: Dict, new_data: Dict) -> Dict:
    """Merge new data points into existing series data"""
    if not existing_data or not existing_data.get('series'):
        return new_data

    # Create lookup for existing series
    existing_by_id = {s['seriesID']: s for s in existing_data.get('series', [])}

    # Merge new data
    for series in new_data.get('series', []):
        series_id = series['seriesID']

        if series_id in existing_by_id:
            # Merge data points
            existing_series = existing_by_id[series_id]
            existing_data_points = {d['year'] + d['period']: d for d in existing_series.get('data', [])}

            # Add new data points
            for data_point in series.get('data', []):
                key = data_point['year'] + data_point['period']
                if key not in existing_data_points:
                    existing_series.setdefault('data', []).append(data_point)
        else:
            # Add new series
            existing_data.setdefault('series', []).append(series)

    return existing_data

def determine_level_from_id(series_id: str) -> str:
    """Determine hierarchy level from series ID"""
    if series_id == "CES0000000001":
        return "total"
    elif series_id in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001", "CES9000000001"]:
        return "major"

    # Extract industry code
    industry_code = series_id[5:11] if len(series_id) >= 11 else "000000"

    if industry_code == "000000":
        return "supersector"
    elif industry_code[2:] == "0000":
        return "sector"
    elif industry_code[4:] == "00":
        return "subsector"
    elif industry_code[5:] == "0":
        return "industry_group"
    else:
        return "industry"

def get_order_from_id(series_id: str) -> int:
    """Get sort order from series ID"""
    if series_id == "CES0000000001":
        return 0
    elif series_id == "CES0500000001":
        return 1
    elif series_id == "CES0600000001":
        return 2
    elif series_id == "CES0700000001":
        return 3
    elif series_id == "CES0800000001":
        return 4
    elif series_id == "CES9000000001":
        return 100

    # Extract numeric portion for ordering
    industry_code = series_id[5:11] if len(series_id) >= 11 else "999999"

    # Try to convert to int for ordering
    for i in range(6, 0, -1):
        try:
            return int(industry_code[:i])
        except:
            continue

    return 999

def determine_hierarchy_level(series_id: str, all_series_set: set = None) -> Dict[str, Any]:
    """Determine the hierarchy level and parent from series ID

    Args:
        series_id: The CES series ID to analyze
        all_series_set: Optional set of all series IDs to check parent existence
    """
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
        # Try to find parent based on NAICS hierarchy
        potential_parents = []

        # For 6-digit codes (e.g., 541310), check for 5-digit parent (54131)
        if industry_code[5:] != "0":
            potential_parents.append(f"CES{supersector}{industry_code[:5]}001")

        # For 5 or 6-digit codes, check for 4-digit parent (5413)
        if industry_code[4:] != "00":
            potential_parents.append(f"CES{supersector}{industry_code[:4]}0001")

        # For 4, 5 or 6-digit codes, check for 3-digit parent (541)
        if industry_code[2:] != "0000":
            potential_parents.append(f"CES{supersector}{industry_code[:2]}00001")

        # Always check for supersector parent as fallback
        potential_parents.append(f"CES{supersector}00000001")

        # Find the most specific parent that exists
        parent_id = f"CES{supersector}00000001"  # Default to supersector
        if all_series_set:
            for potential_parent in potential_parents:
                if potential_parent in all_series_set and potential_parent != series_id:
                    parent_id = potential_parent
                    break

        # Determine level based on NAICS code structure
        if industry_code[2:] == "0000":
            level = "sector"
            order = int(industry_code[:2]) if industry_code[:2].isdigit() else 999
        elif industry_code[4:] == "00":
            level = "subsector"
            order = int(industry_code[:4]) if industry_code[:4].isdigit() else 999
        elif industry_code[5:] == "0":
            level = "industry_group"
            order = int(industry_code[:5]) if industry_code[:5].isdigit() else 999
        else:
            level = "industry"
            order = int(industry_code) if industry_code.isdigit() else 999

        return {"level": level, "parent": parent_id, "order": order}

def compress_data(data: Dict) -> Dict:
    """Compress data for efficient storage with proper hierarchy"""
    compressed = {
        "meta": {
            "generated": datetime.now().isoformat(),
            "start_date": None,
            "end_date": None,
            "series_count": 0,
            "earliest_year": None,
            "latest_year": None,
            "total_data_points": 0
        },
        "series": []
    }

    all_dates = set()
    all_years = set()
    total_points = 0

    # First pass: collect all series IDs
    all_series_ids = set()
    for series in data.get('Results', {}).get('series', []):
        all_series_ids.add(series['seriesID'])

    # Second pass: process series with hierarchy
    for series in data.get('Results', {}).get('series', []):
        series_id = series['seriesID']

        # Get series name
        name = INDUSTRY_NAMES.get(series_id, series_id)

        # Extract and sort data points
        data_points = {}
        for item in series.get('data', []):
            # Only process monthly data
            if item['period'].startswith('M'):
                date_str = f"{item['year']}-{item['period'][1:].zfill(2)}-01"
                try:
                    data_points[date_str] = float(item['value'])
                    all_dates.add(date_str)
                    all_years.add(int(item['year']))
                    total_points += 1
                except (ValueError, KeyError):
                    continue

        if data_points:
            # Sort data points by date
            sorted_points = dict(sorted(data_points.items()))

            # Get hierarchy information using the determine_hierarchy_level function
            hierarchy_info = determine_hierarchy_level(series_id, all_series_ids)

            compressed_series = {
                'id': series_id,
                'name': name,
                'level': hierarchy_info['level'],
                'parent': hierarchy_info.get('parent'),
                'order': hierarchy_info.get('order', 999),
                'data': sorted_points,
                'earliest': min(data_points.keys()) if data_points else None,
                'latest': max(data_points.keys()) if data_points else None,
                'point_count': len(data_points)
            }

            compressed['series'].append(compressed_series)

    # Update metadata
    if all_dates:
        compressed['meta']['start_date'] = min(all_dates)
        compressed['meta']['end_date'] = max(all_dates)
    if all_years:
        compressed['meta']['earliest_year'] = min(all_years)
        compressed['meta']['latest_year'] = max(all_years)
    compressed['meta']['series_count'] = len(compressed['series'])
    compressed['meta']['total_data_points'] = total_points

    return compressed

def main():
    parser = argparse.ArgumentParser(description='Fetch historical CES data from BLS API')
    parser.add_argument('--output', default='data/ces_historical_data.json',
                        help='Output file path')
    parser.add_argument('--start-year', type=int, default=1939,
                        help='Start year for data (default: 1939)')
    parser.add_argument('--test', action='store_true',
                        help='Test mode - only fetch first 10 series')
    args = parser.parse_args()

    # Use test subset if requested
    series_to_fetch = CES_SERIES[:10] if args.test else CES_SERIES

    print(f"Fetching historical CES data from BLS API")
    print(f"Period: {args.start_year} to {datetime.now().year}")
    print(f"Total series to fetch: {len(series_to_fetch)}")
    print(f"Will fetch in {len(YEAR_RANGES)} time periods to avoid API limits")
    print("-" * 60)

    # Fetch in batches of 50 series (BLS API limit) and year ranges
    batch_size = 50
    all_merged_data = {"Results": {"series": []}}

    # Process each year range
    for year_start, year_end in YEAR_RANGES:
        # Skip if before requested start year
        if year_end < args.start_year:
            continue

        # Adjust start year if needed
        range_start = max(year_start, args.start_year)

        print(f"\n{'='*60}")
        print(f"Fetching data for period: {range_start}-{year_end}")
        print(f"{'='*60}")

        range_data = {"Results": {"series": []}}

        # Fetch series in batches
        for i in range(0, len(series_to_fetch), batch_size):
            batch = series_to_fetch[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(series_to_fetch) + batch_size - 1) // batch_size

            print(f"  Batch {batch_num}/{total_batches} ({len(batch)} series)...", end='', flush=True)
            batch_data = fetch_from_bls(batch, range_start, year_end)

            if batch_data.get('Results', {}).get('series'):
                series_count = len(batch_data['Results']['series'])
                # Count data points
                point_count = sum(len(s.get('data', [])) for s in batch_data['Results']['series'])
                range_data['Results'] = merge_series_data(range_data['Results'], batch_data['Results'])
                print(f" ✓ ({series_count} series, {point_count} points)")
            else:
                print(f" ✗ (no data)")

            # Rate limiting between batches
            if i + batch_size < len(series_to_fetch):
                time.sleep(0.5)

        # Merge this range into main dataset
        print(f"  Merging {range_start}-{year_end} data...")
        all_merged_data['Results'] = merge_series_data(all_merged_data['Results'], range_data['Results'])

        # Longer delay between year ranges
        if year_end < YEAR_RANGES[-1][1]:
            print(f"  Waiting before next period...")
            time.sleep(2)

    # Compress and save data
    print(f"\n{'='*60}")
    print("Compressing and analyzing data...")
    compressed = compress_data(all_merged_data)

    # Show statistics
    print(f"\nData Statistics:")
    print(f"  Total series with data: {compressed['meta']['series_count']}")
    print(f"  Total data points: {compressed['meta']['total_data_points']:,}")
    print(f"  Date range: {compressed['meta']['start_date']} to {compressed['meta']['end_date']}")
    print(f"  Year range: {compressed['meta']['earliest_year']} to {compressed['meta']['latest_year']}")

    # Series coverage by decade
    print(f"\nSeries coverage by earliest data:")
    decade_counts = {}
    for series in compressed['series']:
        if series.get('earliest'):
            year = int(series['earliest'][:4])
            decade = (year // 10) * 10
            decade_counts[decade] = decade_counts.get(decade, 0) + 1

    for decade in sorted(decade_counts.keys()):
        print(f"  {decade}s: {decade_counts[decade]} series")

    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save to file
    print(f"\nSaving to {args.output}...")
    with open(args.output, 'w') as f:
        json.dump(compressed, f, separators=(',', ':'))

    file_size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"✓ Data saved ({file_size_mb:.1f} MB)")

    # Also save a backup of the raw data for debugging
    if not args.test:
        backup_path = output_path.parent / 'ces_historical_data_raw.json'
        with open(backup_path, 'w') as f:
            json.dump(all_merged_data, f, separators=(',', ':'))
        print(f"✓ Raw backup saved to {backup_path}")

if __name__ == '__main__':
    main()