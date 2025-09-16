#!/usr/bin/env python3
"""
Build complete CES hierarchy from series data.
Automatically determines parent relationships based on CES code structure.
"""

import json
import sys
from pathlib import Path

# Import series data
try:
    from all_series import CES_SERIES, INDUSTRY_NAMES
except ImportError:
    print("Error: Could not import series data from all_series.py")
    sys.exit(1)


def determine_parent(series_id, all_series):
    """
    Determine parent for a series based on CES numbering system.

    CES code structure:
    - CES + SS (2-digit supersector) + IIIIII (6-digit industry) + DDDD (4-digit data type)

    Hierarchy rules:
    1. Total nonfarm (00) has no parent
    2. Major categories (05, 06, 07, 08, 09) parent to Total nonfarm or Total private
    3. Supersectors (10-90) parent to major categories
    4. Industries follow numeric hierarchy within supersector
    """

    if series_id == "CES0000000001":
        return None  # Total nonfarm is the root

    supersector = series_id[3:5]
    industry = series_id[5:11]
    data_type = series_id[11:]

    # Special top-level categories
    if series_id == "CES0500000001":  # Total private
        return "CES0000000001"
    if series_id == "CES0600000001":  # Goods-producing
        return "CES0500000001"
    if series_id == "CES0700000001":  # Service-providing
        return "CES0000000001"
    if series_id == "CES0800000001":  # Private service-providing
        return "CES0500000001"
    if series_id == "CES9000000001":  # Government
        return "CES0000000001"

    # Supersector level (industry = 000000)
    if industry == "000000":
        # Map supersectors to their parent major categories
        supersector_parents = {
            "10": "CES0600000001",  # Mining and logging -> Goods-producing
            "20": "CES0600000001",  # Construction -> Goods-producing
            "30": "CES0600000001",  # Manufacturing -> Goods-producing
            "31": "CES3000000001",  # Durable goods -> Manufacturing
            "32": "CES3000000001",  # Nondurable goods -> Manufacturing
            "40": "CES0800000001",  # Trade, transportation, utilities -> Private service
            "41": "CES4000000001",  # Wholesale trade -> TTU
            "42": "CES4000000001",  # Retail trade -> TTU
            "43": "CES4000000001",  # Transportation and warehousing -> TTU
            "44": "CES4000000001",  # Utilities -> TTU
            "50": "CES0800000001",  # Information -> Private service
            "51": "CES5000000001",  # Publishing industries -> Information
            "52": "CES5000000001",  # Motion picture and sound recording -> Information
            "55": "CES0800000001",  # Financial activities -> Private service
            "60": "CES0800000001",  # Professional and business services -> Private service
            "65": "CES0800000001",  # Education and health services -> Private service
            "70": "CES0800000001",  # Leisure and hospitality -> Private service
            "71": "CES7000000001",  # Arts, entertainment, and recreation -> Leisure
            "72": "CES7000000001",  # Accommodation and food services -> Leisure
            "80": "CES0800000001",  # Other services -> Private service
            "90": "CES0000000001",  # Government -> Total nonfarm
            "91": "CES9000000001",  # Federal government -> Government
            "92": "CES9000000001",  # State government -> Government
            "93": "CES9000000001",  # Local government -> Government
        }

        return supersector_parents.get(supersector, "CES0000000001")

    # For detailed industry codes, find the parent by looking for broader categories
    # Try to find parent by progressively zeroing out digits from right
    for i in range(5, -1, -1):
        if i == 5 and industry[i] != '0':
            # Try parent with last digit zeroed
            parent_industry = industry[:i] + '0' + industry[i+1:]
            parent_id = f"CES{supersector}{parent_industry}{data_type}"
            if parent_id in all_series and parent_id != series_id:
                return parent_id

        if i == 4 and industry[i:i+2] != '00':
            # Try parent with last two digits zeroed
            parent_industry = industry[:i] + '00' + industry[i+2:]
            parent_id = f"CES{supersector}{parent_industry}{data_type}"
            if parent_id in all_series and parent_id != series_id:
                return parent_id

        if i == 2 and industry[i:i+4] != '0000':
            # Try parent with last four digits zeroed
            parent_industry = industry[:i] + '0000'
            parent_id = f"CES{supersector}{parent_industry}{data_type}"
            if parent_id in all_series and parent_id != series_id:
                return parent_id

    # If no parent found in detailed hierarchy, parent to supersector
    supersector_id = f"CES{supersector}000000{data_type}"
    if supersector_id in all_series and supersector_id != series_id:
        return supersector_id

    # Last resort: check for special parent relationships
    special_parents = get_special_parent_mappings()
    if series_id in special_parents:
        return special_parents[series_id]

    return None


def get_special_parent_mappings():
    """Handle special cases that don't follow the standard pattern."""
    return {
        # Professional and business services special cases
        "CES6054000001": "CES6000000001",  # Professional, scientific, and technical services
        "CES6055000001": "CES6000000001",  # Management of companies and enterprises
        "CES6056000001": "CES6000000001",  # Administrative and waste management

        # Trade, transportation, and utilities special cases
        "CES4142000001": "CES4000000001",  # Wholesale trade
        "CES4200000001": "CES4000000001",  # Retail trade
        "CES4300000001": "CES4000000001",  # Transportation and warehousing
        "CES4422000001": "CES4000000001",  # Utilities

        # Information special cases
        "CES5051000001": "CES5000000001",  # Publishing industries (except internet)
        "CES5052000001": "CES5000000001",  # Motion picture and sound recording industries
        "CES5053000001": "CES5000000001",  # Broadcasting (except internet)
        "CES5054000001": "CES5000000001",  # Telecommunications

        # Financial activities special cases
        "CES5552000001": "CES5500000001",  # Finance and insurance
        "CES5553000001": "CES5500000001",  # Real estate and rental and leasing

        # Education and health special cases
        "CES6561000001": "CES6500000001",  # Educational services
        "CES6562000001": "CES6500000001",  # Health care and social assistance

        # Leisure and hospitality special cases
        "CES7071000001": "CES7000000001",  # Arts, entertainment, and recreation
        "CES7072000001": "CES7000000001",  # Accommodation and food services

        # Government special cases
        "CES9091000001": "CES9000000001",  # Federal government
        "CES9092000001": "CES9000000001",  # State government
        "CES9093000001": "CES9000000001",  # Local government
    }


def build_hierarchy():
    """Build complete hierarchy for all series."""

    # Convert series list to set for fast lookup
    all_series_set = set(CES_SERIES)

    hierarchy = {}

    # Process each series
    for series_id in CES_SERIES:
        parent = determine_parent(series_id, all_series_set)
        hierarchy[series_id] = parent

        # Debug problematic series
        if series_id.startswith("CES605"):
            series_name = INDUSTRY_NAMES.get(series_id, "Unknown")
            parent_name = INDUSTRY_NAMES.get(parent, "Unknown") if parent else "None"
            print(f"{series_id} ({series_name}) -> {parent} ({parent_name})")

    return hierarchy


def infer_level(series_id):
    """Infer the hierarchical level from series ID."""

    if series_id == "CES0000000001":
        return "total"

    supersector = series_id[3:5]
    industry = series_id[5:11]

    # Major categories
    if series_id in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001", "CES9000000001"]:
        return "major"

    # Supersector level
    if industry == "000000":
        return "supersector"

    # Count non-zero positions to determine depth
    # More specific codes are deeper in hierarchy
    if industry[4:6] == "00":
        if industry[2:4] == "00":
            return "sector"  # XX0000
        else:
            return "subsector"  # XXXX00
    elif industry[5] == "0":
        return "industry_group"  # XXXXX0
    else:
        return "industry"  # XXXXXX


def main():
    # Build hierarchy
    print("Building CES hierarchy...")
    hierarchy = build_hierarchy()

    # Load existing data
    data_dir = Path(__file__).parent.parent / 'data'
    data_file = data_dir / 'ces_data.json'

    if not data_file.exists():
        print(f"Error: {data_file} not found")
        print("Please run fetch_ces_data.py first")
        sys.exit(1)

    with open(data_file, 'r') as f:
        data = json.load(f)

    # Update series with parent and level information
    updated = 0
    for series in data['series']:
        series_id = series['id']

        if series_id in hierarchy:
            new_parent = hierarchy[series_id]
            new_level = infer_level(series_id)

            if series.get('parent') != new_parent:
                print(f"Updating parent for {series_id}: {series.get('parent')} -> {new_parent}")
                series['parent'] = new_parent
                updated += 1

            if series.get('level') != new_level:
                series['level'] = new_level

    # Add order field for proper sorting
    for series in data['series']:
        # Extract numeric part for ordering
        supersector = series['id'][3:5]
        industry = series['id'][5:11]

        if series['id'] == "CES0000000001":
            series['order'] = 0
        elif series['id'] in ["CES0500000001", "CES0600000001", "CES0700000001", "CES0800000001"]:
            series['order'] = int(series['id'][3:5])
        elif series['id'] == "CES9000000001":
            series['order'] = 90
        else:
            # Use supersector and first 2 digits of industry for ordering
            try:
                order_val = int(supersector) * 10000
                if industry[:2] != "00":
                    order_val += int(industry[:2]) * 100
                if industry[2:4] != "00":
                    order_val += int(industry[2:4])
                series['order'] = order_val
            except:
                series['order'] = 99999

    # Save updated data
    print(f"\nSaving updated data to {data_file}")
    with open(data_file, 'w') as f:
        json.dump(data, f, separators=(',', ':'))

    print(f"Updated {updated} parent relationships")

    # Save hierarchy mapping for reference
    mapping_file = Path(__file__).parent / 'ces_complete_hierarchy.json'
    with open(mapping_file, 'w') as f:
        mapping_data = {
            "comment": "Complete CES hierarchy mapping",
            "generated": "2025-09-16",
            "series_count": len(hierarchy),
            "parent_mapping": hierarchy
        }
        json.dump(mapping_data, f, indent=2)

    print(f"Saved hierarchy mapping to {mapping_file}")
    print("Done!")


if __name__ == '__main__':
    main()