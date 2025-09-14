#!/usr/bin/env python3
"""
Fetch Current Employment Statistics (CES) data from BLS API
Handles all 842 series with smart compression and retry logic
"""

import json
import time
import argparse
import os
from datetime import datetime, timedelta
import requests
from typing import Dict, List, Any

# Import all series IDs and names
try:
    from all_series import CES_SERIES, INDUSTRY_NAMES
except ImportError:
    # Fallback to embedded list if import fails
    exec(open(os.path.join(os.path.dirname(__file__), 'all_series.py')).read())

# BLS API configuration
BLS_API_KEY = os.environ.get('BLS_API_KEY', 'a7d81877b6374d11a6b7e15fa63b5f9b')
BLS_API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'

# Fallback series list (first few for testing)
if 'CES_SERIES' not in locals():
    CES_SERIES = [
        "CES0000000001", "CES0500000001", "CES0500000002", "CES0500000003",
    "CES0600000001", "CES0600000002", "CES0600000003", "CES0800000001",
    "CES1000000001", "CES1011330001", "CES1021100001", "CES1021200001",
    "CES1021210001", "CES1021220001", "CES1021230001", "CES2000000001",
    "CES2023600001", "CES2023610001", "CES2023620001", "CES2023700001",
    "CES2023800001", "CES3000000001", "CES3100000001", "CES3132100001",
    "CES3132700001", "CES3133100001", "CES3133200001", "CES3133300001",
    "CES3133400001", "CES3133500001", "CES3133600001", "CES3133700001",
    "CES3133900001", "CES3200000001", "CES3231100001", "CES3231200001",
    "CES3231300001", "CES3231400001", "CES3231500001", "CES3231600001",
    "CES3231700001", "CES3231800001", "CES3231900001", "CES3232100001",
    "CES3232200001", "CES3232300001", "CES3232500001", "CES3232600001",
    "CES3232700001", "CES3232800001", "CES3232900001", "CES3233100001",
    "CES3233200001", "CES3233300001", "CES3233400001", "CES3233500001",
    "CES3233600001", "CES3233700001", "CES3233900001", "CES4000000001",
    "CES4142000001", "CES4142100001", "CES4142200001", "CES4142300001",
    "CES4142400001", "CES4142500001", "CES4142600001", "CES4142700001",
    "CES4200000001", "CES4244100001", "CES4244110001", "CES4244120001",
    "CES4244130001", "CES4244200001", "CES4244300001", "CES4244310001",
    "CES4244400001", "CES4244500001", "CES4244510001", "CES4244520001",
    "CES4244530001", "CES4244600001", "CES4244700001", "CES4244800001",
    "CES4244900001", "CES4245100001", "CES4245200001", "CES4245300001",
    "CES4245310001", "CES4245320001", "CES4245400001", "CES4245410001",
    "CES4245500001", "CES4300000001", "CES4348100001", "CES4348200001",
    "CES4348300001", "CES4348400001", "CES4348500001", "CES4349200001",
    "CES4349300001", "CES4422000001", "CES4422110001", "CES4422200001",
    "CES4422300001", "CES4422410001", "CES4422500001", "CES4422510001",
    "CES4423000001", "CES5000000001", "CES5051100001", "CES5051200001",
    "CES5051300001", "CES5051400001", "CES5051500001", "CES5051600001",
    "CES5051700001", "CES5051800001", "CES5051900001", "CES5500000001",
    "CES5552100001", "CES5552200001", "CES5552210001", "CES5552300001",
    "CES5553100001", "CES5553200001", "CES6000000001", "CES6054110001",
    "CES6054120001", "CES6054130001", "CES6054140001", "CES6054150001",
    "CES6054160001", "CES6054170001", "CES6054180001", "CES6054190001",
    "CES6056110001", "CES6056120001", "CES6056130001", "CES6056140001",
    "CES6056150001", "CES6056160001", "CES6056170001", "CES6056190001",
    "CES6500000001", "CES6561100001", "CES6561200001", "CES6561300001",
    "CES6561400001", "CES6561500001", "CES6561600001", "CES6561700001",
    "CES6562100001", "CES6562200001", "CES7000000001", "CES7071100001",
    "CES7071200001", "CES7071300001", "CES7072100001", "CES7072200001",
    "CES8000000001", "CES9000000001", "CES9091100001", "CES9091200001",
    "CES9091400001", "CES9091600001", "CES9091900001", "CES9092000001",
    "CES9092100001", "CES9092200001", "CES9093000001", "CES9093100001",
    "CES9093200001", "CES9093300001"
]

# Industry names mapping
INDUSTRY_NAMES = {
    "CES0000000001": "Total nonfarm",
    "CES0500000001": "Total private",
    "CES0600000001": "Goods-producing",
    "CES0800000001": "Private service-providing",
    "CES1000000001": "Mining and logging",
    "CES1011330001": "Oil and gas extraction",
    "CES1021100001": "Support activities for mining",
    "CES1021200001": "Mining, except oil and gas",
    "CES2000000001": "Construction",
    "CES2023600001": "Construction of buildings",
    "CES2023700001": "Heavy and civil engineering construction",
    "CES2023800001": "Specialty trade contractors",
    "CES3000000001": "Manufacturing",
    "CES3100000001": "Durable goods",
    "CES3200000001": "Nondurable goods",
    "CES4000000001": "Trade, transportation, and utilities",
    "CES4142000001": "Wholesale trade",
    "CES4200000001": "Retail trade",
    "CES4300000001": "Transportation and warehousing",
    "CES4422000001": "Utilities",
    "CES5000000001": "Information",
    "CES5500000001": "Financial activities",
    "CES6000000001": "Professional and business services",
    "CES6500000001": "Education and health services",
    "CES7000000001": "Leisure and hospitality",
    "CES8000000001": "Other services",
    "CES9000000001": "Government"
}

def fetch_from_bls(series_ids: List[str], start_year: int = 2015) -> Dict[str, Any]:
    """Fetch data from BLS API with retry logic"""
    end_year = datetime.now().year
    
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

def build_hierarchy(series_list: List[Dict]) -> Dict[str, Any]:
    """Build hierarchical structure from series data"""
    hierarchy = {}
    
    for series in series_list:
        series_id = series.get('id', series.get('series_id', ''))
        if not series_id:
            continue
        name = series.get('name', INDUSTRY_NAMES.get(series_id, series_id))
        series['series_id'] = series_id  # Normalize the key
        
        # Extract components from series ID
        if len(series_id) >= 13:
            supersector = series_id[3:5]
            industry_code = series_id[5:11]
            
            # Determine hierarchy level
            if industry_code == "000000":
                level = "supersector"
            elif industry_code[2:] == "0000":
                level = "sector"
            elif industry_code[4:] == "00":
                level = "subsector"
            else:
                level = "industry"
            
            series['level'] = level
            series['supersector'] = supersector
            series['industry_code'] = industry_code
            
    return series_list

def compress_data(data: Dict) -> Dict:
    """Compress data for efficient storage"""
    compressed = {
        "meta": {
            "generated": datetime.now().isoformat(),
            "start_date": None,
            "end_date": None,
            "series_count": 0
        },
        "series": []
    }
    
    all_dates = set()
    
    for series in data.get('Results', {}).get('series', []):
        series_id = series['seriesID']
        
        # Get series name
        name = INDUSTRY_NAMES.get(series_id, series_id)
        
        # Extract and sort data points
        data_points = {}
        for item in series.get('data', []):
            date_str = f"{item['year']}-{item['period'][1:].zfill(2)}-01"
            if item['period'].startswith('M'):
                data_points[date_str] = float(item['value'])
                all_dates.add(date_str)
        
        if data_points:
            compressed['series'].append({
                'id': series_id,
                'name': name,
                'data': data_points
            })
    
    # Update metadata
    if all_dates:
        compressed['meta']['start_date'] = min(all_dates)
        compressed['meta']['end_date'] = max(all_dates)
    compressed['meta']['series_count'] = len(compressed['series'])
    
    # Add hierarchy information (but don't break if it fails)
    try:
        compressed['series'] = build_hierarchy(compressed['series'])
    except Exception as e:
        print(f"Warning: Could not build hierarchy: {e}")
    
    return compressed

def main():
    parser = argparse.ArgumentParser(description='Fetch CES data from BLS API')
    parser.add_argument('--output', default='data/ces_data.json', help='Output file path')
    parser.add_argument('--start-year', type=int, default=2015, help='Start year for data')
    args = parser.parse_args()
    
    print(f"Fetching CES data from BLS API...")
    print(f"Total series to fetch: {len(CES_SERIES)}")
    
    # Fetch in batches of 50 (BLS API limit)
    batch_size = 50
    all_data = {"Results": {"series": []}}
    
    for i in range(0, len(CES_SERIES), batch_size):
        batch = CES_SERIES[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(CES_SERIES) + batch_size - 1) // batch_size
        
        print(f"Fetching batch {batch_num}/{total_batches} ({len(batch)} series)...")
        batch_data = fetch_from_bls(batch, args.start_year)
        
        if batch_data.get('Results', {}).get('series'):
            all_data['Results']['series'].extend(batch_data['Results']['series'])
            print(f"  Successfully fetched {len(batch_data['Results']['series'])} series")
        else:
            print(f"  Warning: No data received for batch {batch_num}")
        
        # Rate limiting
        if i + batch_size < len(CES_SERIES):
            time.sleep(1)
    
    print(f"\nTotal series fetched: {len(all_data['Results']['series'])}")
    
    # Compress and save data
    print("Compressing data...")
    compressed = compress_data(all_data)
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    
    # Save to file
    with open(args.output, 'w') as f:
        json.dump(compressed, f, separators=(',', ':'))
    
    file_size_mb = os.path.getsize(args.output) / 1024 / 1024
    print(f"Data saved to {args.output} ({file_size_mb:.1f} MB)")
    print(f"Series count: {compressed['meta']['series_count']}")
    print(f"Date range: {compressed['meta']['start_date']} to {compressed['meta']['end_date']}")

if __name__ == '__main__':
    main()