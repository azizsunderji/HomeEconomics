#!/usr/bin/env python3
"""Fix hierarchy in existing CES data file."""

import json
import sys

def determine_hierarchy_level(series_id):
    """Determine hierarchy level and parent series for a CES series."""
    # Handle special root series
    if series_id == "CES0000000001":
        return {"level": "root", "parent": None, "order": 0}

    # Extract components
    prefix = series_id[:3]  # "CES"
    supersector = series_id[3:5]  # "00", "05", "08", etc.
    industry = series_id[5:11]  # "000000", specific industry code
    datatype = series_id[11:13]  # "01" for all employees

    # Special handling for top-level series
    if series_id == "CES0500000001":  # Goods-producing
        return {"level": "supersector", "parent": "CES0000000001", "order": 5}
    if series_id == "CES0800000001":  # Service-providing
        return {"level": "supersector", "parent": "CES0000000001", "order": 8}

    # For regular series
    if industry == "000000":
        # This is a supersector
        if supersector in ["10", "20", "30", "31", "32"]:
            # Under Goods-producing
            return {"level": "supersector", "parent": "CES0500000001", "order": int(supersector)}
        elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
            # Under Service-providing
            return {"level": "supersector", "parent": "CES0800000001", "order": int(supersector)}
        elif supersector == "90":
            # Government is under Total nonfarm
            return {"level": "supersector", "parent": "CES0000000001", "order": 90}
        else:
            return {"level": "supersector", "parent": "CES0000000001", "order": int(supersector) if supersector.isdigit() else 999}
    else:
        # This is an industry under a supersector
        parent_id = f"CES{supersector}00000001"
        return {"level": "industry", "parent": parent_id, "order": int(industry) if industry.isdigit() else 999}

def main():
    # Load existing data
    print("Loading existing data...")
    with open('data/ces_historical_data_backup.json', 'r') as f:
        data = json.load(f)

    print(f"Loaded {len(data['series'])} series")

    # Fix hierarchy for each series
    print("Fixing hierarchy...")
    for series in data['series']:
        series_id = series['id']
        hierarchy = determine_hierarchy_level(series_id)

        # Update series with corrected hierarchy
        series['level'] = hierarchy['level']
        series['parent'] = hierarchy['parent']
        series['order'] = hierarchy['order']

    # Count hierarchy levels
    level_counts = {}
    parent_counts = {}
    for series in data['series']:
        level = series.get('level', 'unknown')
        parent = series.get('parent', 'None')
        level_counts[level] = level_counts.get(level, 0) + 1
        parent_counts[parent] = parent_counts.get(parent, 0) + 1

    print("\nHierarchy levels:")
    for level, count in sorted(level_counts.items()):
        print(f"  {level}: {count}")

    print("\nTop parent nodes:")
    for parent, count in sorted(parent_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {parent}: {count} children")

    # Save corrected data
    print("\nSaving corrected data...")
    with open('data/ces_historical_data.json', 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    print("Done! Hierarchy has been fixed.")

if __name__ == "__main__":
    main()