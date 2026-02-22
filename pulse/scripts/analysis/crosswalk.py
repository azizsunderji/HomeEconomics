"""Data lake crosswalk — maps topics to relevant datasets.

Parses data_lake_catalog.md and builds a topic-to-dataset index
so the synthesis step can reference specific data files when
generating story opportunities.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from config import DATA_LAKE_CATALOG_PATH, DATA_LAKE_PATH, TOPICS

logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).parent.parent.parent / "data" / "data_lake_index.json"

# Manual mapping of topics to data lake folders/files
# This supplements the automated catalog parsing
TOPIC_TO_DATASETS = {
    "mortgage_rates": [
        "FRED/mortgage*.parquet",
        "FRED/*rate*.parquet",
    ],
    "home_prices": [
        "Price/Zillow/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet",
        "Price/Zillow/zillow_zhvi_zip.parquet",
        "Price/FHFA/hpi_metro_quarterly.parquet",
    ],
    "inventory_supply": [
        "Redfin/monthly_metro.parquet",
    ],
    "affordability": [
        "Price/FHFA/*.parquet",
        "Price/Zillow/*.parquet",
        "ACS_1Y/acs_1y.parquet",
    ],
    "rent_market": [
        "Price/Zillow/*rent*.parquet",
        "Price/Zillow/*zori*.parquet",
        "ACS_1Y/acs_1y.parquet",
    ],
    "construction": [
        "FRED/*starts*.parquet",
        "FRED/*permits*.parquet",
    ],
    "migration_population": [
        "State_Migration/state_to_state_migration_2005_2024.parquet",
        "PopulationEstimates/state_v2025.parquet",
        "PopulationEstimates/metro_cbsa_v2024.parquet",
        "PopulationEstimates/county_v2024.parquet",
        "ACS_1Y/acs_1y.parquet",
    ],
    "federal_reserve": [
        "FRED/*.parquet",
    ],
    "inflation_cpi": [
        "FRED/*cpi*.parquet",
        "FRED/*pce*.parquet",
        "BLS/*.parquet",
    ],
    "employment_labor": [
        "BLS/*.parquet",
        "FRED/*employ*.parquet",
        "FRED/*payroll*.parquet",
    ],
    "recession_economy": [
        "FRED/*gdp*.parquet",
        "FRED/*recession*.parquet",
    ],
    "housing_policy": [
        "ACS_1Y/acs_1y.parquet",
        "ACS_5Y/*.parquet",
    ],
    "commercial_real_estate": [
        "FRED/*commercial*.parquet",
    ],
    "demographics": [
        "ACS_1Y/acs_1y.parquet",
        "CPS_ASEC/*.parquet",
        "PopulationEstimates/*.parquet",
        "Decennial/*.parquet",
    ],
    "wealth_inequality": [
        "FRED/*wealth*.parquet",
        "FRED/*networth*.parquet",
        "ACS_1Y/acs_1y.parquet",
        "CPS_ASEC/*.parquet",
    ],
    "regional_markets": [
        "Price/Zillow/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet",
        "Redfin/monthly_metro.parquet",
        "Price/FHFA/hpi_metro_quarterly.parquet",
        "PopulationEstimates/metro_cbsa_v2024.parquet",
    ],
    "mortgage_industry": [
        "FRED/*mortgage*.parquet",
    ],
    "climate_insurance": [
        "CDC_Mortality/*.parquet",
    ],
    "consumer_sentiment": [
        "FRED/*sentiment*.parquet",
        "FRED/*michigan*.parquet",
        "GSS/gss_cumulative.parquet",
    ],
}


def _parse_catalog() -> dict:
    """Parse data_lake_catalog.md to extract file listings with descriptions."""
    catalog_path = Path(DATA_LAKE_CATALOG_PATH)
    if not catalog_path.exists():
        logger.warning(f"Data lake catalog not found at {catalog_path}")
        return {}

    content = catalog_path.read_text()
    files = {}

    # Parse lines like: `- filename.parquet — description`
    for match in re.finditer(r"-\s+`?([^`\n]+\.parquet)`?\s*[—–-]\s*(.+)", content):
        filename = match.group(1).strip()
        description = match.group(2).strip()
        files[filename] = description

    logger.info(f"Parsed {len(files)} parquet files from catalog")
    return files


def build_index() -> dict:
    """Build the complete topic-to-dataset index.

    Combines manual mapping with catalog descriptions.
    Saves to data/data_lake_index.json.
    """
    catalog_files = _parse_catalog()

    index = {}
    for topic, patterns in TOPIC_TO_DATASETS.items():
        datasets = []
        for pattern in patterns:
            # Find matching files from catalog
            import fnmatch
            for filename, description in catalog_files.items():
                if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(filename, f"*/{pattern}"):
                    datasets.append({
                        "file": filename,
                        "description": description,
                    })

            # Also add the pattern itself if no matches (for files not in catalog)
            if not any(d["file"].endswith(pattern.replace("*", "")) for d in datasets):
                datasets.append({
                    "file": pattern,
                    "description": f"Data matching {pattern}",
                })

        index[topic] = {
            "label": TOPICS[topic]["label"],
            "datasets": datasets,
        }

    # Save to file
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2))
    logger.info(f"Saved data lake index to {INDEX_PATH}")

    return index


def get_datasets_for_topics(topics: list[str]) -> list[dict]:
    """Look up data lake files relevant to a set of topics.

    Returns list of {file, description, topic} dicts.
    """
    # Load cached index
    if INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text())
    else:
        index = build_index()

    results = []
    seen_files = set()

    for topic in topics:
        if topic in index:
            for dataset in index[topic]["datasets"]:
                if dataset["file"] not in seen_files:
                    results.append({
                        **dataset,
                        "topic": topic,
                    })
                    seen_files.add(dataset["file"])

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    index = build_index()
    for topic, info in index.items():
        print(f"\n{info['label']}:")
        for ds in info["datasets"][:3]:
            print(f"  - {ds['file']}: {ds['description'][:60]}")
