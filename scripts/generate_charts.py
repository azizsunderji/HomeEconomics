#!/usr/bin/env python3
"""
Generate charts for metros using exact Denver styling
"""

import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime
import logging

# Add scripts directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from exact_metro_chart_generator import create_exact_metro_chart
from social_media_chart_generator import create_social_media_chart

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Metrics configuration
METRICS = [
    {
        'name': 'weeks_supply',
        'display_name': 'Weeks of Supply',
        'column': 'WEEKS_OF_SUPPLY',
        'unit': 'weeks',
        'decimals': 1,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'new_listings',
        'display_name': 'New Listings',
        'column': 'ADJUSTED_AVERAGE_NEW_LISTINGS',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': 'per_100_active',
        'normalized_unit_label': '% of Active'
    },
    {
        'name': 'active_listings',
        'display_name': 'Active Listings',
        'column': 'ACTIVE_LISTINGS',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'age_of_inventory',
        'display_name': 'Age of Inventory',
        'column': 'AGE_OF_INVENTORY',
        'unit': 'days',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'homes_sold',
        'display_name': 'Homes Sold',
        'column': 'ADJUSTED_AVERAGE_HOMES_SOLD',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': 'per_100_active',
        'normalized_unit_label': '% of Active'
    },
    {
        'name': 'pending_sales',
        'display_name': 'Pending Sales',
        'column': 'AVERAGE_PENDING_SALES_LISTING_UPDATES',
        'unit': '',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'off_market_in_2_weeks',
        'display_name': 'Off Market in 2 Weeks',
        'column': 'OFF_MARKET_IN_TWO_WEEKS',
        'unit': '',  # It's a count, not a percentage
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'median_sale_price',
        'display_name': 'Median Sale Price',
        'column': 'MEDIAN_SALE_PRICE',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'median_days_on_market',
        'display_name': 'Median Days on Market',
        'column': 'MEDIAN_DAYS_ON_MARKET',
        'unit': 'days',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'median_days_to_close',
        'display_name': 'Median Days to Close',
        'column': 'MEDIAN_DAYS_TO_CLOSE',
        'unit': 'days',
        'decimals': 0,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'sale_to_list_ratio',
        'display_name': 'Sale to List Ratio',
        'column': 'AVERAGE_SALE_TO_LIST_RATIO',
        'unit': '',
        'decimals': 1,
        'is_percentage': True,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    },
    {
        'name': 'pct_listings_w__price_drops',  # Note double underscore
        'display_name': '% Listings with Price Drops',
        'column': 'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS',
        'unit': '%',
        'decimals': 1,
        'is_percentage': False,
        'normalize_for_histogram': None,
        'normalized_unit_label': None
    }
]

def slug_to_metro_name(slug: str) -> str:
    """Convert city slug back to Redfin metro name format"""
    # Split by underscore
    parts = slug.rsplit('_', 1)  # Split from right to get state
    if len(parts) != 2:
        raise ValueError(f"Invalid slug format: {slug}")
    
    city_part, state_part = parts
    
    # Convert city part
    city = city_part.replace('_', ' ').title()
    # Handle special cases
    city = city.replace(' Ny ', ' NY ').replace(' Ca ', ' CA ').replace(' Tx ', ' TX ')
    city = city.replace(' Co ', ' CO ').replace(' Fl ', ' FL ').replace(' Ga ', ' GA ')
    city = city.replace(' Il ', ' IL ').replace(' Oh ', ' OH ').replace(' Pa ', ' PA ')
    city = city.replace(' Dc ', ' DC ').replace(' Nc ', ' NC ').replace(' Va ', ' VA ')
    
    # State should be uppercase
    state = state_part.upper()
    
    # Return in Redfin format
    return f"{city}, {state} metro area"

def render_city(city_slug: str, date: str, out_dir: str, metrics: list = None, 
                chart_type: str = 'mobile') -> None:
    """
    Generate charts for a single city
    
    Args:
        city_slug: City identifier (e.g., 'denver_co')
        date: Date string (YYYY-MM-DD)
        out_dir: Output directory path
        metrics: List of metric names to generate (or None for all)
        chart_type: Type of chart to generate ('mobile' or 'social')
    """
    # Load data (should be cached in production)
    data_file = Path(__file__).parent.parent / 'data' / 'weekly_housing_market_data.parquet'
    if not data_file.exists():
        raise FileNotFoundError(f"Data file not found: {data_file}")
    
    logger.info(f"Generating {chart_type} charts for {city_slug}")
    
    # Load data
    df = pd.read_parquet(data_file)
    df['PERIOD_END'] = pd.to_datetime(df['PERIOD_END'])
    
    # Convert slug to metro name
    metro_name = slug_to_metro_name(city_slug)
    
    # Create output directory
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Generate each metric
    metrics_to_gen = METRICS if metrics is None else [m for m in METRICS if m['name'] in metrics]
    
    successful = 0
    failed = 0
    
    for metric in metrics_to_gen:
        try:
            # Create metric config for generator
            metric_config = {
                'name': metric['display_name'],
                'column': metric['column'],
                'unit': metric['unit'],
                'decimals': metric['decimals'],
                'is_percentage': metric['is_percentage'],
            }
            
            if chart_type == 'mobile':
                # Add extra fields for mobile charts
                metric_config['normalize_for_histogram'] = metric['normalize_for_histogram']
                metric_config['normalized_unit_label'] = metric['normalized_unit_label']
                
                # Output filename
                output_file = out_path / f"{city_slug}_{metric['name']}_mobile.png"
                
                # Generate mobile chart
                success = create_exact_metro_chart(df, metro_name, metric_config, str(output_file))
            
            elif chart_type == 'social':
                # Output filename
                output_file = out_path / f"{city_slug}_{metric['name']}_social.png"
                
                # Generate social media chart
                success = create_social_media_chart(df, metro_name, metric_config, str(output_file))
            
            else:
                raise ValueError(f"Unknown chart type: {chart_type}")
            
            if success:
                successful += 1
                logger.debug(f"  ✓ {metric['display_name']}")
            else:
                failed += 1
                logger.warning(f"  ✗ {metric['display_name']}: Generation failed")
                
        except Exception as e:
            failed += 1
            logger.error(f"  ✗ {metric['display_name']}: {str(e)}")
    
    logger.info(f"  Completed {city_slug} ({chart_type}): {successful} success, {failed} failed")

def render_national(date: str, out_dir: str, metrics: list = None) -> None:
    """
    Generate national aggregate charts (if needed)
    
    For now, this is a placeholder as we focus on metro charts
    """
    logger.info("National charts not implemented yet")
    pass

def main():
    """Main function for standalone testing"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate metro charts')
    parser.add_argument('--city', type=str, required=True, help='City slug (e.g., denver_co)')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'),
                       help='Date (YYYY-MM-DD)')
    parser.add_argument('--out', type=str, default='out/reports',
                       help='Output directory')
    parser.add_argument('--metrics', nargs='+', help='Specific metrics to generate')
    parser.add_argument('--type', type=str, default='mobile', choices=['mobile', 'social', 'both'],
                       help='Chart type to generate: mobile (email), social (square), or both')
    
    args = parser.parse_args()
    
    try:
        if args.type == 'both':
            # Generate both mobile and social charts
            render_city(args.city, args.date, f"{args.out}/{args.date}/{args.city}/mobile", 
                       args.metrics, chart_type='mobile')
            render_city(args.city, args.date, f"{args.out}/{args.date}/{args.city}/social", 
                       args.metrics, chart_type='social')
            print(f"✅ Both mobile and social charts generated for {args.city}")
        else:
            # Generate single type
            output_dir = f"{args.out}/{args.date}/{args.city}"
            if args.type == 'social':
                output_dir += "/social"
            render_city(args.city, args.date, output_dir, args.metrics, chart_type=args.type)
            print(f"✅ {args.type.capitalize()} charts generated for {args.city}")
        return 0
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())