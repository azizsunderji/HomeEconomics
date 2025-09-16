#!/usr/bin/env python3
"""
Fix Professional, scientific, and technical services hierarchy.
The major subsectors should be under 6054, not directly under 60.
"""

import json
import os

def main():
    # Load the existing data
    data_file = '../data/ces_data.json'
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data['series'])} series from data file")
    
    # These are the major subsectors that should be under 6054000001
    # Format: series_id -> correct_parent
    corrections = {
        'CES6054110001': 'CES6054000001',  # Legal services
        'CES6054120001': 'CES6054000001',  # Accounting, tax preparation...
        'CES6054130001': 'CES6054000001',  # Architectural, engineering...
        'CES6054140001': 'CES6054000001',  # Specialized design services
        'CES6054150001': 'CES6054000001',  # Computer systems design...
        'CES6054160001': 'CES6054000001',  # Management, scientific, and technical consulting
        'CES6054170001': 'CES6054000001',  # Scientific research and development
        'CES6054180001': 'CES6054000001',  # Advertising, public relations...
        'CES6054190001': 'CES6054000001',  # Other professional, scientific...
    }
    
    # Apply corrections
    updates = 0
    for series in data['series']:
        series_id = series['id']
        
        if series_id in corrections:
            old_parent = series.get('parent')
            new_parent = corrections[series_id]
            
            if old_parent != new_parent:
                print(f"Fixing {series_id}: {old_parent} -> {new_parent}")
                print(f"  {series['name']}")
                series['parent'] = new_parent
                updates += 1
    
    print(f"\nTotal updates made: {updates}")
    
    # Save the updated data
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    
    print(f"Data saved back to {data_file}")
    
    # Verify the fix
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    # Check that 6054000001 now has children
    children_count = 0
    for series in data['series']:
        if series.get('parent') == 'CES6054000001':
            children_count += 1
    
    print(f"Professional, scientific, and technical services (6054000001)")
    print(f"  Now has {children_count} immediate children")
    
    # Show the hierarchy
    print("\nHierarchy structure:")
    prof_sci = next((s for s in data['series'] if s['id'] == 'CES6054000001'), None)
    if prof_sci:
        print(f"- {prof_sci['name']}")
        
        # Get immediate children
        children = [s for s in data['series'] if s.get('parent') == 'CES6054000001']
        children.sort(key=lambda x: x['id'])
        
        for child in children[:10]:
            print(f"  - {child['name']}")
            
            # Get grandchildren (sample)
            grandchildren = [s for s in data['series'] if s.get('parent') == child['id']]
            for gc in grandchildren[:2]:
                print(f"    - {gc['name']}")

if __name__ == "__main__":
    main()