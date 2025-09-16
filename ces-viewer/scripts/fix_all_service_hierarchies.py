#!/usr/bin/env python3
"""
Fix all service sector hierarchies comprehensively.
"""

import json
import os

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data['series'])} series from data file")
    
    # Build a set of all series IDs for validation
    all_series_ids = {s['id'] for s in data['series']}
    
    updates = 0
    
    # Fix Management of companies and enterprises (6055)
    print("\nFixing Management of companies hierarchy...")
    for series in data['series']:
        if series['id'].startswith('CES6055') and len(series['id']) == 13:
            # Check if it's a subsector that should be under 6055000001
            if series['id'] != 'CES6055000001':
                industry_code = series['id'][5:11]
                # If it's a direct subsector (like 605511xxxx)
                if industry_code[:2] == '55' and series.get('parent') == 'CES6000000001':
                    print(f"  Fixing {series['id']}: parent -> CES6055000001")
                    series['parent'] = 'CES6055000001'
                    updates += 1
    
    # Fix Administrative and support (6056)
    print("\nFixing Administrative and support hierarchy...")
    for series in data['series']:
        if series['id'].startswith('CES6056') and len(series['id']) == 13:
            if series['id'] != 'CES6056000001':
                industry_code = series['id'][5:11]
                # Check if it's a major subsector (56xx0000)
                if industry_code[:2] == '56' and industry_code[2:] == '0000':
                    if series.get('parent') != 'CES6056000001':
                        print(f"  Fixing {series['id']}: parent -> CES6056000001")
                        print(f"    {series['name']}")
                        series['parent'] = 'CES6056000001'
                        updates += 1
                # Check other 56xx industries
                elif industry_code[:2] == '56':
                    # Find the correct parent (could be 5610, 5611, 5612, etc.)
                    potential_parents = []
                    
                    # Build potential parents from most specific to least
                    if industry_code[4:] != '00':
                        # Try 4-digit parent
                        potential_parents.append(f"CES60{industry_code[:4]}0001")
                    if industry_code[2:] != '0000':
                        # Try 2-digit subsector parent
                        potential_parents.append(f"CES60{industry_code[:2]}00001")
                    # Fallback to main 6056
                    potential_parents.append('CES6056000001')
                    
                    # Find the most specific existing parent
                    for parent in potential_parents:
                        if parent in all_series_ids and parent != series['id']:
                            if series.get('parent') != parent:
                                print(f"  Fixing {series['id']}: parent -> {parent}")
                                series['parent'] = parent
                                updates += 1
                            break
    
    # Fix Educational services (6561) if needed
    print("\nChecking Educational services hierarchy...")
    for series in data['series']:
        if series['id'].startswith('CES6561') and len(series['id']) == 13:
            if series['id'] != 'CES6561000001':
                industry_code = series['id'][5:11]
                # Major subsectors should be under 6561000001
                if industry_code[:2] == '61' and industry_code[2:] == '0000':
                    if series.get('parent') != 'CES6561000001':
                        print(f"  Fixing {series['id']}: parent -> CES6561000001")
                        series['parent'] = 'CES6561000001'
                        updates += 1
    
    print(f"\nTotal updates made: {updates}")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"Data saved back to {data_file}")
    
    # Verify the fixes
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    sectors = [
        'CES6054000001',  # Professional, scientific
        'CES6055000001',  # Management
        'CES6056000001',  # Administrative
        'CES6561000001',  # Educational
    ]
    
    for sector_id in sectors:
        sector = next((s for s in data['series'] if s['id'] == sector_id), None)
        if sector:
            children_count = sum(1 for s in data['series'] if s.get('parent') == sector_id)
            print(f"{sector['name'][:50]}")
            print(f"  ID: {sector_id}")
            print(f"  Has {children_count} immediate children")
            
            # Show first few children
            children = [s for s in data['series'] if s.get('parent') == sector_id]
            children.sort(key=lambda x: x['id'])
            for child in children[:3]:
                print(f"    - {child['name'][:50]}")

if __name__ == "__main__":
    main()