#!/usr/bin/env python3
"""
Universal hierarchy fix for CES data based on NAICS code structure.
This fixes the parent-child relationships for ALL series systematically.
"""

import json
import os

def get_correct_parent(series_id, all_series_ids):
    """
    Determine the correct parent based on NAICS code hierarchy.
    
    CES series ID structure: CESxxyyyyyyzz
    - xx = supersector code (2 digits)
    - yyyyyy = industry code (6 digits)
    - zz = data type code (2 digits)
    
    Hierarchy levels:
    - xx000000 = Supersector
    - xxyy0000 = 2-digit sector/major group
    - xxyyyy00 = 4-digit industry
    - xxyyyyy0 = 5-digit industry
    - xxyyyyyy = 6-digit detailed industry
    """
    
    # Special cases for top-level aggregates
    if series_id == "CES0000000001":
        return None  # Total nonfarm - root
    elif series_id in ["CES0500000001", "CES0700000001", "CES9000000001"]:
        return "CES0000000001"  # Major categories under Total
    elif series_id in ["CES0600000001", "CES0800000001"]:
        # Goods-producing and Private service-providing under Total private
        return "CES0500000001"
    elif series_id in ["CES9100000001", "CES9200000001"]:
        # Federal and State/local under Government
        return "CES9000000001"
    
    # Parse the series ID
    if len(series_id) < 13:
        return None
    
    supersector = series_id[3:5]
    industry = series_id[5:11]
    
    # Determine hierarchy level and parent
    if industry == "000000":
        # This is a supersector
        # Determine parent based on supersector code ranges
        if supersector in ["10", "20", "30"]:
            return "CES0600000001"  # Under Goods-producing
        elif supersector in ["31", "32"]:
            return "CES3000000001"  # Durable/Nondurable under Manufacturing
        elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
            return "CES0800000001"  # Under Private service-providing
        elif supersector in ["90", "91", "92"]:
            return "CES0000000001"  # Government sectors
        else:
            return "CES0500000001"  # Default to Total private
    
    # For industries, build potential parents from most specific to least specific
    potential_parents = []
    
    if industry[5:] != "0" and industry[5:] != "00":
        # 6-digit industry - parent is 5-digit
        potential_parents.append(f"CES{supersector}{industry[:5]}001")
    
    if industry[4:] not in ["0", "00", "000"]:
        # 5 or 6-digit - parent could be 4-digit
        potential_parents.append(f"CES{supersector}{industry[:4]}0001")
    
    if industry[3:] != "000" and industry[3:] != "0000":
        # Could have a 3-digit parent (like Construction 236, 237, 238)
        potential_parents.append(f"CES{supersector}{industry[:3]}0001")
    
    if industry[2:] != "0000":
        # 4, 5, or 6-digit - parent could be 2-digit sector
        potential_parents.append(f"CES{supersector}{industry[:2]}00001")
    
    # Always add supersector as fallback
    potential_parents.append(f"CES{supersector}00000001")
    
    # Find the first potential parent that exists
    for parent in potential_parents:
        if parent in all_series_ids:
            return parent
    
    # If no parent found in dataset, return supersector
    return f"CES{supersector}00000001"

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data['series'])} series from data file")
    
    # Get all series IDs for existence checking
    all_series_ids = {s['id'] for s in data['series']}
    
    # Fix parent relationships for ALL series
    updates = 0
    hierarchy_stats = {}
    
    for series in data['series']:
        series_id = series['id']
        current_parent = series.get('parent')
        correct_parent = get_correct_parent(series_id, all_series_ids)
        
        if current_parent != correct_parent:
            # Track what level this is for statistics
            industry = series_id[5:11] if len(series_id) >= 11 else ""
            if industry == "000000":
                level = "supersector"
            elif industry[2:] == "0000":
                level = "2-digit"
            elif industry[4:] == "00":
                level = "4-digit"
            elif industry[5:] in ["0", "00"]:
                level = "5-digit"
            else:
                level = "6-digit"
            
            if level not in hierarchy_stats:
                hierarchy_stats[level] = 0
            hierarchy_stats[level] += 1
            
            if updates < 20:  # Show first 20 updates as examples
                print(f"Updating {series_id}: {current_parent} -> {correct_parent}")
                print(f"  {series['name'][:50]}")
            
            series['parent'] = correct_parent
            updates += 1
    
    print(f"\nTotal updates made: {updates}")
    print("\nUpdates by level:")
    for level, count in sorted(hierarchy_stats.items()):
        print(f"  {level}: {count}")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"\nData saved back to {data_file}")
    
    # Validate hierarchy
    print("\n" + "="*60)
    print("HIERARCHY VALIDATION")
    print("="*60)
    
    # Check for orphans (series with parents that don't exist)
    orphans = []
    for series in data['series']:
        parent = series.get('parent')
        if parent and parent not in all_series_ids:
            orphans.append((series['id'], parent))
    
    if orphans:
        print(f"WARNING: Found {len(orphans)} orphaned series:")
        for sid, parent in orphans[:10]:
            print(f"  {sid} -> {parent} (parent doesn't exist)")
    else:
        print("✓ No orphaned series found")
    
    # Sample hierarchy check for Professional services
    print("\nSample: Professional services hierarchy")
    prof_services = [s for s in data['series'] if s['id'].startswith('CES60')]
    
    # Build tree
    by_parent = {}
    for s in prof_services:
        parent = s.get('parent')
        if parent not in by_parent:
            by_parent[parent] = []
        by_parent[parent].append(s)
    
    def print_tree(sid, indent=0, max_depth=3, limit=5):
        if indent > max_depth:
            return
        series = next((s for s in data['series'] if s['id'] == sid), None)
        if not series:
            return
        print("  " * indent + f"- {series['name'][:40]}")
        if sid in by_parent:
            children = sorted(by_parent[sid], key=lambda x: x['id'])[:limit]
            for child in children:
                print_tree(child['id'], indent + 1, max_depth, limit)
            if len(by_parent[sid]) > limit:
                print("  " * (indent + 1) + f"... and {len(by_parent[sid]) - limit} more")
    
    print_tree('CES6000000001')

if __name__ == "__main__":
    main()