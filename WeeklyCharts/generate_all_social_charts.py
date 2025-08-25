#!/usr/bin/env python3
"""
Generate social media charts (1200x1200) for all metros and metrics.
"""

import sys
import os
import pandas as pd
from pathlib import Path
from datetime import datetime
from dateutil import tz
import json

# Add WeeklyCharts to path for imports
sys.path.insert(0, '/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/WeeklyCharts')
from social_media_chart_generator_v2 import create_exact_metro_chart

# Define all metrics to generate
METRICS = [
    {
        'column': 'ACTIVE_LISTINGS',
        'name': 'Active Listings',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'active_listings'
    },
    {
        'column': 'ADJUSTED_AVERAGE_NEW_LISTINGS',
        'name': 'New Listings',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': 'per_100_active',
        'normalized_unit_label': '% of Active',
        'filename_suffix': 'new_listings'
    },
    {
        'column': 'ADJUSTED_AVERAGE_HOMES_SOLD',
        'name': 'Homes Sold',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': 'per_100_active',
        'normalized_unit_label': '% of Active',
        'filename_suffix': 'homes_sold'
    },
    {
        'column': 'MEDIAN_SALE_PRICE',
        'name': 'Median Sale Price',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'median_sale_price'
    },
    {
        'column': 'MEDIAN_NEW_LISTING_PRICE',
        'name': 'Median List Price',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'median_list_price'
    },
    {
        'column': 'MEDIAN_SALE_PPSF',
        'name': 'Median Sale Price Per Sq Ft',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'median_sale_ppsf'
    },
    {
        'column': 'MEDIAN_NEW_LISTING_PPSF',
        'name': 'Median List Price Per Sq Ft',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'median_list_ppsf'
    },
    {
        'column': 'MEDIAN_DAYS_TO_CLOSE',
        'name': 'Median Days to Close',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'days_to_close'
    },
    {
        'column': 'OFF_MARKET_IN_TWO_WEEKS',
        'name': 'Off Market in Two Weeks',
        'unit': '%',
        'decimals': 1,
        'is_percentage': True,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'off_market_two_weeks'
    },
    {
        'column': 'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS',
        'name': 'Listings with Price Drops',
        'unit': '%',
        'decimals': 1,
        'is_percentage': True,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'price_drops'
    },
    {
        'column': 'SALE_TO_LIST_RATIO',
        'name': 'Sale to List Ratio',
        'unit': '%',
        'decimals': 1,
        'is_percentage': True,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'sale_to_list'
    },
    {
        'column': 'SOLD_ABOVE_LIST_RATIO',
        'name': 'Sold Above List',
        'unit': '%',
        'decimals': 1,
        'is_percentage': True,
        'normalize_for_histogram': None,
        'normalized_unit_label': None,
        'filename_suffix': 'sold_above_list'
    }
]

def get_today_eastern():
    """Get today's date in Eastern time."""
    et = tz.gettz("America/New_York")
    return datetime.now(et).strftime("%Y-%m-%d")

def main():
    # Get today's date for output folder
    today = get_today_eastern()
    
    # Create output directory
    output_dir = Path(f'/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/{today}')
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")
    
    # Load data
    print("Loading data...")
    df = pd.read_parquet('/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/data/weekly_housing_market_data.parquet')
    
    # Get unique metros
    metro_df = df[df['REGION_TYPE'] == 'Metro Area']
    metros = sorted(metro_df['REGION_NAME'].unique())
    print(f"Found {len(metros)} metros")
    
    # Track success and failures
    success_count = 0
    failed_charts = []
    
    # Generate charts for each metro and metric
    total_charts = len(metros) * len(METRICS)
    chart_num = 0
    
    for metro in metros:
        # Create metro subdirectory
        metro_slug = metro.lower().replace(' ', '_').replace(',', '').replace('-', '_')
        metro_dir = output_dir / metro_slug
        metro_dir.mkdir(exist_ok=True)
        
        for metric in METRICS:
            chart_num += 1
            output_file = metro_dir / f"{metric['filename_suffix']}.png"
            
            # Create metric config without filename_suffix
            metric_config = {k: v for k, v in metric.items() if k != 'filename_suffix'}
            
            try:
                print(f"[{chart_num}/{total_charts}] {metro} - {metric['name']}...", end='', flush=True)
                success = create_exact_metro_chart(df, metro, metric_config, str(output_file))
                if success:
                    print(" ✓")
                    success_count += 1
                else:
                    print(" ✗ (no data)")
                    failed_charts.append((metro, metric['name'], "No data"))
            except Exception as e:
                print(f" ✗ ({str(e)[:50]})")
                failed_charts.append((metro, metric['name'], str(e)))
    
    # Summary
    print(f"\n{'='*60}")
    print(f"✅ Successfully generated {success_count}/{total_charts} charts")
    print(f"📁 Output directory: {output_dir}")
    
    if failed_charts:
        print(f"\n⚠️  Failed charts ({len(failed_charts)}):")
        for metro, metric, error in failed_charts[:10]:  # Show first 10
            print(f"  - {metro} / {metric}: {error[:50]}")
        if len(failed_charts) > 10:
            print(f"  ... and {len(failed_charts) - 10} more")
    
    # Create index.json for Raycast script
    index_data = {
        'date': today,
        'metros': metros,
        'metrics': [m['filename_suffix'] for m in METRICS]
    }
    with open(output_dir / 'index.json', 'w') as f:
        json.dump(index_data, f, indent=2)
    print(f"\n📋 Created index.json for Raycast integration")

if __name__ == "__main__":
    main()