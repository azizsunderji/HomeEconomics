#!/usr/bin/env python3
"""
Build a complete hierarchy mapping for CES series based on BLS structure.
This creates an authoritative parent-child mapping to fix hierarchy issues.
"""

import json
import os
from typing import Dict, Optional

def build_hierarchy_rules() -> Dict[str, Optional[str]]:
    """
    Build comprehensive hierarchy rules based on BLS Table B-1a structure.
    The BLS hierarchy follows this pattern:
    - Total nonfarm (00)
    - Total private (05)
      - Goods-producing (06) - child of 05
        - Mining and logging (10) - child of 06
        - Construction (20) - child of 06
        - Manufacturing (30) - child of 06
      - Private service-providing (08) - child of 05
        - Trade, transportation, utilities (40) - child of 08
        - Information (50) - child of 08
        - Financial activities (55) - child of 08
        - Professional and business services (60) - child of 08
          - Professional, scientific, technical (6054) - child of 60
            - Legal services (605411) - child of 6054
            - Accounting services (605412) - child of 6054
            - etc.
    - Government (90) - child of 00
    """
    
    mapping = {}
    
    # Top level
    mapping["CES0000000001"] = None  # Total nonfarm
    
    # Major categories
    mapping["CES0500000001"] = "CES0000000001"  # Total private
    mapping["CES0600000001"] = "CES0500000001"  # Goods-producing
    mapping["CES0700000001"] = "CES0000000001"  # Service-providing
    mapping["CES0800000001"] = "CES0500000001"  # Private service-providing
    mapping["CES9000000001"] = "CES0000000001"  # Government
    mapping["CES9100000001"] = "CES9000000001"  # Federal government
    mapping["CES9200000001"] = "CES9000000001"  # State and local government
    
    # Goods-producing supersectors (under 06)
    mapping["CES1000000001"] = "CES0600000001"  # Mining and logging
    mapping["CES2000000001"] = "CES0600000001"  # Construction
    
    # Construction hierarchy (20)
    mapping["CES2023600001"] = "CES2000000001"  # Construction of buildings
    mapping["CES2023610001"] = "CES2023600001"  # Residential building construction
    mapping["CES2023611501"] = "CES2023610001"  # New single-family housing
    mapping["CES2023611601"] = "CES2023610001"  # New multifamily housing
    mapping["CES2023611701"] = "CES2023610001"  # New housing for-sale builders
    mapping["CES2023611801"] = "CES2023610001"  # Residential remodelers
    mapping["CES2023620001"] = "CES2023600001"  # Nonresidential building construction
    mapping["CES2023621001"] = "CES2023620001"  # Industrial building construction
    mapping["CES2023622001"] = "CES2023620001"  # Commercial and institutional
    
    mapping["CES2023700001"] = "CES2000000001"  # Heavy and civil engineering construction
    mapping["CES2023710001"] = "CES2023700001"  # Utility system construction
    mapping["CES2023711001"] = "CES2023710001"  # Water and sewer line
    mapping["CES2023712001"] = "CES2023710001"  # Oil and gas pipeline
    mapping["CES2023713001"] = "CES2023710001"  # Power and communication line
    mapping["CES2023720001"] = "CES2023700001"  # Land subdivision
    mapping["CES2023730001"] = "CES2023700001"  # Highway, street, and bridge
    mapping["CES2023790001"] = "CES2023700001"  # Other heavy and civil
    
    mapping["CES2023800001"] = "CES2000000001"  # Specialty trade contractors
    mapping["CES2023800101"] = "CES2023800001"  # Residential specialty trade contractors
    mapping["CES2023800201"] = "CES2023800001"  # Nonresidential specialty trade contractors
    mapping["CES2023810001"] = "CES2023800001"  # Foundation, structure, and building exterior
    mapping["CES2023811001"] = "CES2023810001"  # Poured concrete foundation
    mapping["CES2023812001"] = "CES2023810001"  # Structural steel
    mapping["CES2023813001"] = "CES2023810001"  # Framing
    mapping["CES2023814001"] = "CES2023810001"  # Masonry
    mapping["CES2023815001"] = "CES2023810001"  # Glass and glazing
    mapping["CES2023816001"] = "CES2023810001"  # Roofing
    mapping["CES2023817001"] = "CES2023810001"  # Siding
    mapping["CES2023819001"] = "CES2023810001"  # Other foundation
    
    mapping["CES2023820001"] = "CES2023800001"  # Building equipment contractors
    mapping["CES2023821001"] = "CES2023820001"  # Electrical contractors
    mapping["CES2023822001"] = "CES2023820001"  # Plumbing, heating
    mapping["CES2023829001"] = "CES2023820001"  # Other building equipment
    
    mapping["CES2023830001"] = "CES2023800001"  # Building finishing contractors
    mapping["CES2023831001"] = "CES2023830001"  # Drywall and insulation
    mapping["CES2023832001"] = "CES2023830001"  # Painting and wall
    mapping["CES2023833001"] = "CES2023830001"  # Flooring
    mapping["CES2023834001"] = "CES2023830001"  # Tile and terrazzo
    mapping["CES2023835001"] = "CES2023830001"  # Finish carpentry
    mapping["CES2023839001"] = "CES2023830001"  # Other building finishing
    
    mapping["CES2023890001"] = "CES2023800001"  # Other specialty trade contractors
    mapping["CES2023891001"] = "CES2023890001"  # Site preparation
    mapping["CES2023899001"] = "CES2023890001"  # All other specialty trade
    
    mapping["CES3000000001"] = "CES0600000001"  # Manufacturing
    mapping["CES3100000001"] = "CES3000000001"  # Durable goods
    mapping["CES3200000001"] = "CES3000000001"  # Nondurable goods
    
    # Service-providing supersectors (under 08)
    mapping["CES4000000001"] = "CES0800000001"  # Trade, transportation, utilities
    mapping["CES4100000001"] = "CES4000000001"  # Wholesale trade
    mapping["CES4200000001"] = "CES4000000001"  # Retail trade  
    mapping["CES4300000001"] = "CES4000000001"  # Transportation and warehousing
    mapping["CES4400000001"] = "CES4000000001"  # Utilities
    
    mapping["CES5000000001"] = "CES0800000001"  # Information
    mapping["CES5500000001"] = "CES0800000001"  # Financial activities
    mapping["CES6000000001"] = "CES0800000001"  # Professional and business services
    mapping["CES6500000001"] = "CES0800000001"  # Education and health services
    mapping["CES7000000001"] = "CES0800000001"  # Leisure and hospitality
    mapping["CES8000000001"] = "CES0800000001"  # Other services
    
    # Professional and business services sectors (under 60)
    mapping["CES6054000001"] = "CES6000000001"  # Professional, scientific, and technical services
    mapping["CES6055000001"] = "CES6000000001"  # Management of companies and enterprises
    mapping["CES6056000001"] = "CES6000000001"  # Administrative and support and waste management
    
    return mapping

def determine_parent_from_rules(series_id: str, known_parents: Dict) -> Optional[str]:
    """
    Determine parent for a series using hierarchical rules.
    """
    # If we have an explicit mapping, use it
    if series_id in known_parents:
        return known_parents[series_id]
    
    # Otherwise, try to infer based on patterns
    if len(series_id) < 13:
        return None
        
    supersector = series_id[3:5]
    industry = series_id[5:11]
    
    # For Professional, scientific, technical services subsectors
    if supersector == "60" and industry.startswith("54"):
        # All 5411xx, 5412xx, etc. should be under 6054
        if industry[:4] in ["5411", "5412", "5413", "5414", "5415", "5416", "5417", "5418", "5419"]:
            parent_base = f"CES60{industry[:4]}0001"
            if parent_base in known_parents or industry[4:] == "00":
                # This is a major group like 5411
                return "CES6054000001"
            else:
                # This is a detail under a major group
                return f"CES60{industry[:4]}0001"
    
    # For other sectors, build potential parents and check existence
    potential_parents = []
    
    # Build from most specific to least specific
    if industry != "000000":
        # Try 5-digit parent
        if industry[5:] != "0":
            potential_parents.append(f"CES{supersector}{industry[:5]}001")
        # Try 4-digit parent
        if industry[4:] != "00":
            potential_parents.append(f"CES{supersector}{industry[:4]}0001")
        # Try 3-digit parent (for sectors like 54)
        if industry[2:] != "0000":
            potential_parents.append(f"CES{supersector}{industry[:2]}00001")
        # Try 2-digit parent (for major sectors)
        if industry[:2] != "00":
            # Special handling for professional services
            if supersector == "60" and industry[:2] == "54":
                potential_parents.append("CES6054000001")
            elif supersector == "60" and industry[:2] == "55":
                potential_parents.append("CES6055000001")
            elif supersector == "60" and industry[:2] == "56":
                potential_parents.append("CES6056000001")
    
    # Default to supersector
    potential_parents.append(f"CES{supersector}00000001")
    
    # Return first potential parent that exists
    for parent in potential_parents:
        if parent in known_parents:
            return parent
    
    # Final fallback based on supersector ranges
    if supersector in ["10", "20", "30", "31", "32"]:
        if supersector == "31" or supersector == "32":
            return "CES3000000001"  # Manufacturing
        return "CES0600000001"  # Goods-producing
    elif supersector in ["40", "41", "42", "43", "44", "50", "55", "60", "65", "70", "80"]:
        return "CES0800000001"  # Private service-providing
    elif supersector in ["90", "91", "92"]:
        return "CES9000000001"  # Government
        
    return None

def main():
    """Build complete hierarchy mapping from all series."""
    
    # Load all series IDs
    from all_series import CES_SERIES, INDUSTRY_NAMES
    
    print(f"Building hierarchy mapping for {len(CES_SERIES)} series...")
    
    # Start with known hierarchical rules
    known_parents = build_hierarchy_rules()
    
    # Build complete mapping
    complete_mapping = {}
    
    # First pass: add all series with known parents
    for series_id in CES_SERIES:
        parent = determine_parent_from_rules(series_id, known_parents)
        complete_mapping[series_id] = parent
        
    # Second pass: verify and fix any issues
    print("\nVerifying hierarchy...")
    issues = []
    
    for series_id, parent in complete_mapping.items():
        if parent and parent not in CES_SERIES and parent not in known_parents:
            issues.append(f"{series_id} -> {parent} (parent not in dataset)")
    
    if issues:
        print(f"Found {len(issues)} potential issues:")
        for issue in issues[:10]:
            print(f"  {issue}")
    
    # Save the complete mapping
    output = {
        "comment": "Complete CES hierarchy mapping based on BLS structure",
        "generated": "2025-09-15",
        "series_count": len(complete_mapping),
        "parent_mapping": complete_mapping
    }
    
    output_path = "ces_complete_hierarchy.json"
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nMapping saved to {output_path}")
    
    # Print summary
    levels = {}
    for series_id in complete_mapping:
        if complete_mapping[series_id] is None:
            level = "root"
        elif series_id[5:11] == "000000":
            level = "supersector"
        elif series_id[7:11] == "0000":
            level = "sector"
        elif series_id[9:11] == "00":
            level = "subsector"
        else:
            level = "industry"
        
        if level not in levels:
            levels[level] = 0
        levels[level] += 1
    
    print("\nHierarchy summary:")
    for level, count in sorted(levels.items()):
        print(f"  {level}: {count} series")
    
    # Test Professional services hierarchy
    print("\nProfessional, scientific, technical services hierarchy:")
    prof_sci = [s for s in CES_SERIES if s.startswith("CES6054")]
    for series_id in sorted(prof_sci)[:20]:
        parent = complete_mapping.get(series_id)
        name = INDUSTRY_NAMES.get(series_id, "Unknown")
        print(f"  {series_id}: {name}")
        print(f"    Parent: {parent}")

if __name__ == "__main__":
    main()