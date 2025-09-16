#!/usr/bin/env python3
"""
Systematically fix the hierarchy by ensuring all series are assigned to their immediate parent
based on the NAICS code structure.
"""

import json
import os

def get_immediate_parent(series_id, all_series_ids):
    """
    Find the immediate parent based on NAICS code hierarchy.
    The parent should be the most specific existing series that encompasses this one.
    """
    if series_id == "CES0000000001":
        return None
    elif series_id in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001", "CES9000000001"]:
        return "CES0000000001"
    
    supersector = series_id[3:5]
    industry_code = series_id[5:11] if len(series_id) >= 11 else ""
    
    # If this is a supersector (000000), parent is a major category
    if industry_code == "000000":
        # Map supersectors to their major categories
        if supersector in ["10", "20", "30", "31", "32"]:
            # Mining/Construction/Manufacturing under Goods-producing
            if supersector in ["31", "32"]:
                return "CES3000000001"  # Durable/Nondurable under Manufacturing
            return "CES0600000001"  # Under Goods-producing
        elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
            # Service sectors
            if supersector in ["41", "42", "43", "44"]:
                return "CES4000000001"  # Trade/Transport subsectors under main
            return "CES0800000001"  # Under Private service-providing
        elif supersector in ["90", "91", "92"]:
            if supersector in ["91", "92"]:
                return "CES9000000001"  # Federal/State under Government
            return "CES0000000001"  # Government under Total
        else:
            return "CES0500000001"  # Default to Total private
    
    # For all other series, find the most specific parent that exists
    # Build potential parents from most specific to least specific
    potential_parents = []
    
    # Try progressively shorter codes
    # For 236115 -> try 23611, 2361, 236, 23, then 000000
    if len(industry_code) >= 6 and industry_code[5] != "0":
        # 6-digit code, try 5-digit parent
        potential_parents.append(f"CES{supersector}{industry_code[:5]}001")
    
    if len(industry_code) >= 5 and industry_code[4:] != "00":
        # 5-digit or 6-digit code, try 4-digit parent
        potential_parents.append(f"CES{supersector}{industry_code[:4]}0001")
    
    if len(industry_code) >= 4 and industry_code[3:] != "000":
        # 4-digit code, try 3-digit parent
        potential_parents.append(f"CES{supersector}{industry_code[:3]}0001")
    
    if len(industry_code) >= 3 and industry_code[2:] != "0000":
        # Has industry code, try sector parent (first 2 digits)
        potential_parents.append(f"CES{supersector}{industry_code[:2]}00001")
    
    # Finally try supersector
    potential_parents.append(f"CES{supersector}00000001")
    
    # Find the first potential parent that actually exists
    for parent in potential_parents:
        if parent in all_series_ids and parent != series_id:
            return parent
    
    # If no parent found, return the supersector
    return f"CES{supersector}00000001"

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data['series'])} series from data file")
    
    # Get all series IDs for existence checking
    all_series_ids = {s['id'] for s in data['series']}
    
    # Fix parent relationships
    parent_updates = 0
    missing_parents = set()
    
    for series in data['series']:
        series_id = series['id']
        current_parent = series.get('parent')
        correct_parent = get_immediate_parent(series_id, all_series_ids)
        
        if current_parent != correct_parent:
            print(f"Updating {series_id}: parent {current_parent} -> {correct_parent}")
            series['parent'] = correct_parent
            parent_updates += 1
            
            # Check if parent exists
            if correct_parent and correct_parent not in all_series_ids:
                missing_parents.add(correct_parent)
    
    print(f"\nMade {parent_updates} parent updates")
    
    if missing_parents:
        print(f"\nWarning: {len(missing_parents)} parents are missing from dataset:")
        for mp in sorted(missing_parents)[:10]:
            print(f"  {mp}")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"\nData saved back to {data_file}")
    
    # Verify Construction hierarchy specifically
    print("\n" + "="*60)
    print("CONSTRUCTION HIERARCHY VERIFICATION:")
    print("="*60)
    
    construction = [s for s in data['series'] if s['id'].startswith('CES20')]
    
    # Group by parent
    by_parent = {}
    for series in construction:
        parent = series.get('parent', 'None')
        if parent not in by_parent:
            by_parent[parent] = []
        by_parent[parent].append(series)
    
    # Print hierarchically
    def print_tree(parent_id, indent=0):
        if parent_id in by_parent:
            for child in sorted(by_parent[parent_id], key=lambda x: x['id']):
                print("  " * indent + f"- {child['id']}: {child['name'][:40]}")
                # Recursively print children
                print_tree(child['id'], indent + 1)
    
    # Start with Construction supersector
    construction_root = next((s for s in data['series'] if s['id'] == 'CES2000000001'), None)
    if construction_root:
        print(f"ROOT: {construction_root['id']}: {construction_root['name']}")
        print_tree('CES2000000001', 1)
    
    # Also check for orphans
    print("\n" + "="*60)
    print("ORPHANED SERIES (incorrect parents):")
    orphans = []
    for series in construction:
        if series['id'] != 'CES2000000001':
            parent_id = series.get('parent')
            if parent_id and not parent_id.startswith('CES20') and parent_id != 'CES0600000001':
                orphans.append(series)
    
    if orphans:
        for orphan in sorted(orphans, key=lambda x: x['id'])[:20]:
            print(f"  {orphan['id']}: parent={orphan.get('parent')} (should be under Construction)")

if __name__ == "__main__":
    main()