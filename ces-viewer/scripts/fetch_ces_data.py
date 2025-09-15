#!/usr/bin/env python3
"""
Fetch Current Employment Statistics (CES) data from BLS API
Version 2: Simplified hierarchy with proper parent-child relationships
"""

import json
import time
import argparse
import os
from datetime import datetime
import requests
from typing import Dict, List, Any

# Import all series IDs and names
try:
    from all_series import CES_SERIES, INDUSTRY_NAMES
except ImportError:
    exec(open(os.path.join(os.path.dirname(__file__), 'all_series.py')).read())

# BLS API configuration
BLS_API_KEY = os.environ.get('BLS_API_KEY', 'a7d81877b6374d11a6b7e15fa63b5f9b')
BLS_API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'

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

def determine_level_from_id(series_id: str) -> str:
    """Determine the hierarchy level from series ID structure"""
    if series_id == "CES0000000001":
        return "total"
    elif series_id in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001", "CES9000000001"]:
        return "major"
    
    industry_code = series_id[5:11] if len(series_id) >= 11 else ""
    
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
    """Compress data with proper hierarchy"""
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
    
    # Load the hierarchy mapping
    hierarchy_mapping = {}
    mapping_file = os.path.join(os.path.dirname(__file__), 'ces_complete_hierarchy.json')
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            mapping_data = json.load(f)
            hierarchy_mapping = mapping_data.get('parent_mapping', {})
        print(f"Loaded hierarchy mapping with {len(hierarchy_mapping)} entries")
    else:
        print("Warning: Hierarchy mapping file not found, using inference")
        # Fallback to collect all series IDs for inference
        all_series_ids = {s['seriesID'] for s in data.get('Results', {}).get('series', [])}
    
    for series in data.get('Results', {}).get('series', []):
        series_id = series['seriesID']
        
        # Get series name
        name = INDUSTRY_NAMES.get(series_id, series_id)
        
        # Use mapping if available, otherwise fall back to inference
        if hierarchy_mapping:
            parent = hierarchy_mapping.get(series_id)
            hierarchy_info = {
                'parent': parent,
                'level': determine_level_from_id(series_id),
                'order': get_order_from_id(series_id)
            }
        else:
            # Fallback to old logic
            hierarchy_info = determine_hierarchy_level(series_id, all_series_ids)
        
        # Extract and sort data points
        data_points = {}
        for item in series.get('data', []):
            if item['period'].startswith('M'):
                date_str = f"{item['year']}-{item['period'][1:].zfill(2)}-01"
                data_points[date_str] = float(item['value'])
                all_dates.add(date_str)
        
        if data_points:
            # Debug: print first few parent relationships
            if len(compressed['series']) < 5:
                print(f"DEBUG: {series_id} -> parent={hierarchy_info.get('parent')}, level={hierarchy_info['level']}")
            
            compressed['series'].append({
                'id': series_id,
                'name': name,
                'level': hierarchy_info['level'],
                'parent': hierarchy_info.get('parent'),
                'order': hierarchy_info.get('order', 999),
                'data': data_points
            })
    
    # Sort series by hierarchy level and order
    level_order = ['total', 'major', 'supersector', 'sector', 'subsector', 'industry_group', 'industry']
    compressed['series'].sort(key=lambda x: (
        level_order.index(x['level']) if x['level'] in level_order else 999,
        x.get('order', 999),
        x['name']
    ))
    
    # Update metadata
    if all_dates:
        compressed['meta']['start_date'] = min(all_dates)
        compressed['meta']['end_date'] = max(all_dates)
    compressed['meta']['series_count'] = len(compressed['series'])
    
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
    
    # Print hierarchy summary
    levels = {}
    for s in compressed['series']:
        level = s['level']
        if level not in levels:
            levels[level] = 0
        levels[level] += 1
    
    print("\nHierarchy summary:")
    for level in ['total', 'major', 'supersector', 'sector', 'subsector', 'industry']:
        if level in levels:
            print(f"  {level}: {levels[level]} series")

if __name__ == '__main__':
    main()