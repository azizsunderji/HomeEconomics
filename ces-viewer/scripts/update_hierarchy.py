#!/usr/bin/env python3
"""
Update the hierarchy in the existing data file with the corrected mapping.
"""

import json
import os

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    # Load the new hierarchy mapping
    mapping_file = 'ces_complete_hierarchy.json'
    with open(mapping_file, 'r') as f:
        hierarchy_data = json.load(f)
        parent_mapping = hierarchy_data.get('parent_mapping', {})
    
    print(f"Loaded {len(data['series'])} series from data file")
    print(f"Loaded {len(parent_mapping)} parent mappings")
    
    # Update each series with correct parent
    updates = 0
    for series in data['series']:
        series_id = series['id']
        if series_id in parent_mapping:
            new_parent = parent_mapping[series_id]
            if series.get('parent') != new_parent:
                print(f"Updating {series_id}: {series.get('parent')} -> {new_parent}")
                series['parent'] = new_parent
                updates += 1
    
    print(f"\nMade {updates} parent updates")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"Data saved back to {data_file}")
    
    # Print Construction hierarchy to verify
    print("\nConstruction hierarchy verification:")
    construction_series = [s for s in data['series'] if s['id'].startswith('CES20')]
    for series in sorted(construction_series, key=lambda x: x['id'])[:20]:
        print(f"  {series['id']}: {series['name']}")
        print(f"    Parent: {series.get('parent')}")

if __name__ == "__main__":
    main()