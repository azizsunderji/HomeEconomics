#!/usr/bin/env python3
"""Fix CES hierarchy with absolute correct parent relationships based on BLS structure."""

import json

def fix_hierarchy():
    """Fix hierarchy relationships in CES data with the ABSOLUTE CORRECT structure."""

    # Load data
    print("Loading data...")
    with open('data/ces_historical_data.json', 'r') as f:
        data = json.load(f)

    print(f"Processing {len(data['series'])} series...")

    # Create a lookup for quick access
    series_by_id = {s['id']: s for s in data['series']}

    # CORRECT HIERARCHY based on BLS Table B-1
    correct_parents = {
        # Root
        "CES0000000001": None,  # Total nonfarm (ROOT)

        # Level 1 - Direct children of Total nonfarm
        "CES0500000001": "CES0000000001",  # Total private -> Total nonfarm
        "CES9000000001": "CES0000000001",  # Government -> Total nonfarm

        # Level 2 - Under Total private
        "CES0600000001": "CES0500000001",  # Goods-producing -> Total private
        "CES0800000001": "CES0500000001",  # Service-providing -> Total private

        # Level 3 - Under Goods-producing
        "CES1000000001": "CES0600000001",  # Mining and logging -> Goods-producing
        "CES2000000001": "CES0600000001",  # Construction -> Goods-producing
        "CES3000000001": "CES0600000001",  # Manufacturing -> Goods-producing

        # Level 3 - Under Service-providing
        "CES4000000001": "CES0800000001",  # Trade, transportation, utilities -> Service-providing
        "CES5000000001": "CES0800000001",  # Information -> Service-providing
        "CES5500000001": "CES0800000001",  # Financial activities -> Service-providing
        "CES6000000001": "CES0800000001",  # Professional and business services -> Service-providing
        "CES6500000001": "CES0800000001",  # Private education and health -> Service-providing
        "CES7000000001": "CES0800000001",  # Leisure and hospitality -> Service-providing
        "CES8000000001": "CES0800000001",  # Other services -> Service-providing

        # Level 4 - Under Manufacturing
        "CES3100000001": "CES3000000001",  # Durable goods -> Manufacturing
        "CES3200000001": "CES3000000001",  # Nondurable goods -> Manufacturing

        # Level 4 - Under Trade, transportation, utilities
        "CES4142000001": "CES4000000001",  # Wholesale trade -> Trade/transport
        "CES4200000001": "CES4000000001",  # Retail trade -> Trade/transport
        "CES4300000001": "CES4000000001",  # Transportation and warehousing -> Trade/transport
        "CES4422000001": "CES4000000001",  # Utilities -> Trade/transport

        # Level 3 - Under Government
        "CES9091000001": "CES9000000001",  # Federal -> Government
        "CES9092000001": "CES9000000001",  # State government -> Government
        "CES9093000001": "CES9000000001",  # Local government -> Government
    }

    # Proper names
    proper_names = {
        "CES0000000001": "Total nonfarm",
        "CES0500000001": "Total private",
        "CES0600000001": "Goods-producing",
        "CES0800000001": "Service-providing",
        "CES9000000001": "Government",
        "CES1000000001": "Mining and logging",
        "CES2000000001": "Construction",
        "CES3000000001": "Manufacturing",
        "CES3100000001": "Durable goods",
        "CES3200000001": "Nondurable goods",
        "CES4000000001": "Trade, transportation, and utilities",
        "CES5000000001": "Information",
        "CES5500000001": "Financial activities",
        "CES6000000001": "Professional and business services",
        "CES6500000001": "Private education and health services",
        "CES7000000001": "Leisure and hospitality",
        "CES8000000001": "Other services"
    }

    # Fix each series
    fixed_count = 0
    for series in data['series']:
        sid = series['id']

        # Fix known parent relationships
        if sid in correct_parents:
            old_parent = series.get('parent')
            new_parent = correct_parents[sid]
            if old_parent != new_parent:
                print(f"Fixing {sid}: parent {old_parent} -> {new_parent}")
                series['parent'] = new_parent
                fixed_count += 1

        # Fix known names
        if sid in proper_names:
            old_name = series.get('name')
            new_name = proper_names[sid]
            if old_name != new_name:
                print(f"Fixing {sid}: name '{old_name}' -> '{new_name}'")
                series['name'] = new_name

        # For other series, determine parent based on supersector code
        elif len(sid) >= 13 and sid[5:11] == "000000":  # It's a supersector
            supersector = sid[3:5]

            # Determine parent for other supersectors based on their code
            if supersector in ["10", "20", "30", "31", "32"]:
                # These go under Goods-producing
                if series.get('parent') != "CES0600000001":
                    print(f"Fixing {sid}: parent {series.get('parent')} -> CES0600000001 (Goods-producing)")
                    series['parent'] = "CES0600000001"
                    fixed_count += 1
            elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
                # These go under Service-providing
                if series.get('parent') != "CES0800000001":
                    print(f"Fixing {sid}: parent {series.get('parent')} -> CES0800000001 (Service-providing)")
                    series['parent'] = "CES0800000001"
                    fixed_count += 1
            elif supersector in ["90", "91", "92", "93"]:
                # Government sectors
                if supersector == "90":
                    if series.get('parent') != "CES0000000001":
                        series['parent'] = "CES0000000001"
                        fixed_count += 1
                else:
                    if series.get('parent') != "CES9000000001":
                        series['parent'] = "CES9000000001"
                        fixed_count += 1

    print(f"\nFixed {fixed_count} parent relationships")

    # Validate hierarchy
    print("\nValidating hierarchy...")
    parent_counts = {}
    for series in data['series']:
        parent = series.get('parent')
        if parent:
            parent_counts[parent] = parent_counts.get(parent, 0) + 1

    print("\nParent relationships:")
    for parent_id, count in sorted(parent_counts.items()):
        parent_series = series_by_id.get(parent_id)
        parent_name = parent_series['name'] if parent_series else "Unknown"
        print(f"  {parent_id} ({parent_name}): {count} children")

    # Count root nodes
    root_count = sum(1 for s in data['series'] if not s.get('parent'))
    print(f"\nRoot nodes (no parent): {root_count}")

    # Save fixed data
    print("\nSaving fixed data...")
    with open('data/ces_historical_data.json', 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    print("Done!")

    # Final verification of key series
    print("\n" + "="*60)
    print("VERIFICATION OF KEY SERIES:")
    verify_series = [
        "CES0600000001",  # Goods-producing
        "CES0800000001",  # Service-providing
        "CES1000000001",  # Mining
        "CES2000000001",  # Construction
        "CES3000000001",  # Manufacturing
        "CES4000000001",  # Trade/transport
    ]

    for sid in verify_series:
        series = series_by_id.get(sid)
        if series:
            parent_id = series.get('parent')
            parent_name = series_by_id.get(parent_id, {}).get('name', 'None') if parent_id else 'None'
            print(f"{sid} ({series['name']}): parent = {parent_id} ({parent_name})")

if __name__ == "__main__":
    fix_hierarchy()