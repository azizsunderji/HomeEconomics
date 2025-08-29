#!/usr/bin/env python3
"""Run metro rankings generation with progress output"""

import sys
import os

print("Starting metro rankings generation...")
print("=" * 60)

# Import the main function
sys.path.append('scripts')

try:
    print("Importing modules...")
    from generate_metro_rankings_final import main
    
    print("Running main function...")
    # Create dummy args
    class Args:
        input_file = 'data/weekly_housing_market_data.parquet'
        output_dir = 'metro-rankings-2025-08-22'
    
    args = Args()
    
    # Call main with args
    import pandas as pd
    from pathlib import Path
    from datetime import datetime
    from generate_metro_rankings_final import (
        METRICS, calculate_changes, format_value, format_change,
        get_region_for_metro, generate_metric_summary, 
        calculate_market_size, generate_html_page
    )
    
    print(f"Loading data from {args.input_file}...")
    df = pd.read_parquet(args.input_file)
    print(f"Loaded {len(df):,} rows")
    
    # Focus on 4-week duration
    if 'DURATION' in df.columns:
        df = df[df['DURATION'] == '4 weeks'].copy()
        print(f"Filtered to 4-week duration: {len(df):,} rows")
    
    # Filter to metros only
    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    print(f"Metro areas only: {len(metros_df):,} rows")
    
    # Get unique metros
    metros = metros_df['REGION_NAME'].unique()
    print(f"Found {len(metros)} unique metro areas")
    
    # Get latest date
    metros_df['PERIOD_END'] = pd.to_datetime(metros_df['PERIOD_END'])
    latest_date = metros_df['PERIOD_END'].max()
    date_str = latest_date.strftime('%B %d, %Y')
    print(f"Latest data date: {date_str}")
    
    # Create output directory
    output_path = Path(args.output_dir)
    output_path.mkdir(exist_ok=True)
    print(f"Output directory: {output_path}")
    
    # Just generate one metric as a test
    print("\nGenerating MEDIAN_SALE_PRICE page as test...")
    
    metric_key = 'MEDIAN_SALE_PRICE'
    metric_info = METRICS[metric_key]
    
    # Build rankings data
    rankings_data = []
    metros_processed = 0
    
    for metro in metros[:50]:  # Just process first 50 for speed
        metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')
        if len(metro_data) > 0:
            latest_data = metro_data.iloc[-1]
            if pd.notna(latest_data[metric_key]):
                current_value = latest_data[metric_key]
                
                periods = {'1month': 1, '3month': 3, '6month': 6, '1year': 13, '3year': 39}
                changes = calculate_changes(metro_data, metric_key, periods)
                
                rankings_data.append({
                    'metro_name': metro.replace(' metro area', ''),
                    'current_value': current_value,
                    'changes': changes,
                    'market_percentile': 25.0  # Dummy value
                })
                metros_processed += 1
                if metros_processed % 10 == 0:
                    print(f"  Processed {metros_processed} metros...")
    
    print(f"Built rankings for {len(rankings_data)} metros")
    
    # Sort and generate HTML
    rankings_data.sort(key=lambda x: x['current_value'], reverse=True)
    
    print("Generating HTML page...")
    html = generate_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)
    
    output_file = output_path / f"{metric_info['slug']}.html"
    with open(output_file, 'w') as f:
        f.write(html)
    
    print(f"\nSuccess! Generated: {output_file}")
    print(f"File size: {len(html):,} bytes")
    
except Exception as e:
    print(f"\nError: {e}")
    import traceback
    traceback.print_exc()