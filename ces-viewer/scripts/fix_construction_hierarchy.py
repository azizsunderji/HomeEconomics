#!/usr/bin/env python3
"""
Fix the Construction sector hierarchy based on official BLS structure.
"""

import json
import os

def get_correct_level(series_id):
    """Determine the correct hierarchy level based on BLS structure"""
    if series_id == "CES0000000001":
        return "total"
    elif series_id in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001", "CES9000000001"]:
        return "major"
    
    supersector = series_id[3:5] if len(series_id) >= 5 else ""
    industry_code = series_id[5:11] if len(series_id) >= 11 else ""
    
    if industry_code == "000000":
        return "supersector"
    
    # Construction (20) hierarchy based on NAICS codes
    if supersector == "20":
        first3 = industry_code[:3]
        first4 = industry_code[:4]
        
        # 236, 237, 238 are sectors (main divisions)
        if first3 in ["236", "237", "238"] and industry_code[3:] == "000":
            return "sector"
        
        # 2361, 2362 are subsectors under 236
        elif first4 in ["2361", "2362"] and industry_code[4:] == "00":
            return "subsector"
        
        # 2371, 2372, 2373, 2379 are subsectors under 237
        elif first4 in ["2371", "2372", "2373", "2379"] and industry_code[4:] == "00":
            return "subsector"
        
        # 2381, 2382, 2383, 2389 are subsectors under 238
        elif first4 in ["2381", "2382", "2383", "2389"] and industry_code[4:] == "00":
            return "subsector"
        
        # Everything else under Construction is industry level
        else:
            return "industry"
    
    # Standard hierarchy for other supersectors
    elif industry_code[2:] == "0000":
        return "sector"
    elif industry_code[4:] == "00":
        return "subsector"
    elif industry_code[5:] == "0":
        return "industry_group"
    else:
        return "industry"

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data['series'])} series from data file")
    
    # Fix levels for all series
    level_updates = 0
    for series in data['series']:
        series_id = series['id']
        correct_level = get_correct_level(series_id)
        if series['level'] != correct_level:
            print(f"Updating level for {series_id}: {series['level']} -> {correct_level}")
            series['level'] = correct_level
            level_updates += 1
    
    print(f"\nMade {level_updates} level updates")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"Data saved back to {data_file}")
    
    # Print Construction hierarchy to verify
    print("\nConstruction hierarchy verification:")
    construction_series = [s for s in data['series'] if s['id'].startswith('CES20')]
    
    # Group by level
    by_level = {}
    for series in construction_series:
        level = series['level']
        if level not in by_level:
            by_level[level] = []
        by_level[level].append(series)
    
    # Print in hierarchical order
    level_order = ['supersector', 'sector', 'subsector', 'industry']
    for level in level_order:
        if level in by_level:
            print(f"\n{level.upper()}:")
            for series in sorted(by_level[level], key=lambda x: x['id'])[:10]:
                print(f"  {series['id']}: {series['name'][:50]}")

if __name__ == "__main__":
    main()