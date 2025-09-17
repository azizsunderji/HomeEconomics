#!/usr/bin/env python3
"""Complete fix for CES hierarchy and names."""

import json

# Proper names for key series
PROPER_NAMES = {
    "CES0000000001": "Total nonfarm",
    "CES0500000001": "Total private",
    "CES0600000001": "Goods-producing",
    "CES0800000001": "Service-providing",
    "CES9000000001": "Government",
    "CES1000000001": "Mining and logging",
    "CES2000000001": "Construction",
    "CES3000000001": "Manufacturing",
    "CES3100000001": "Durable goods",
    "CES3200000001": "Nondurable goods"
}

def fix_hierarchy_and_names():
    """Fix hierarchy relationships and names in CES data."""

    # Load data
    print("Loading data...")
    with open('src/ces_historical_data.json', 'r') as f:
        data = json.load(f)

    print(f"Processing {len(data['series'])} series...")

    # Fix each series
    for series in data['series']:
        sid = series['id']

        # Fix names for known series
        if sid in PROPER_NAMES:
            series['name'] = PROPER_NAMES[sid]
            print(f"Fixed name: {sid} -> {PROPER_NAMES[sid]}")

        # Fix hierarchy based on correct BLS structure
        supersector = sid[3:5]
        industry = sid[5:11]

        # Root level
        if sid == "CES0000000001":
            series['level'] = 'root'
            series['parent'] = None
            series['order'] = 0

        # Total private (05) - directly under Total nonfarm
        elif sid == "CES0500000001":
            series['level'] = 'supersector'
            series['parent'] = "CES0000000001"
            series['order'] = 5

        # Goods-producing (06) - under Total private
        elif sid == "CES0600000001":
            series['level'] = 'supersector'
            series['parent'] = "CES0500000001"
            series['order'] = 6

        # Service-providing (08) - under Total private
        elif sid == "CES0800000001":
            series['level'] = 'supersector'
            series['parent'] = "CES0500000001"
            series['order'] = 8

        # Government (90) - directly under Total nonfarm
        elif sid == "CES9000000001":
            series['level'] = 'supersector'
            series['parent'] = "CES0000000001"
            series['order'] = 90

        # Industry supersectors
        elif industry == "000000":
            series['level'] = 'supersector'

            # Mining (10), Construction (20), Manufacturing (30-32) under Goods-producing
            if supersector in ["10", "20", "30", "31", "32"]:
                series['parent'] = "CES0600000001"
                series['order'] = int(supersector)

            # Service sectors (40-80) under Service-providing
            elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
                series['parent'] = "CES0800000001"
                series['order'] = int(supersector)

            # Government sectors (90+) under Government
            elif supersector.startswith("9"):
                series['parent'] = "CES9000000001"
                series['order'] = int(supersector)

    # Validate hierarchy
    print("\nValidating hierarchy...")
    parent_counts = {}
    for series in data['series']:
        parent = series.get('parent', 'None')
        parent_counts[parent] = parent_counts.get(parent, 0) + 1

    print("\nParent relationships:")
    for parent, count in sorted(parent_counts.items(), key=lambda x: (x[0] if x[0] else '')):
        if parent and parent != 'None':
            parent_name = next((s['name'] for s in data['series'] if s['id'] == parent), parent)
            print(f"  {parent} ({parent_name}): {count} children")
        else:
            print(f"  Root level: {count}")

    # Save fixed data
    print("\nSaving fixed data...")
    with open('src/ces_historical_data.json', 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    print("Done!")

if __name__ == "__main__":
    fix_hierarchy_and_names()