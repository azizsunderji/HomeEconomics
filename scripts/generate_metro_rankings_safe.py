#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages from Redfin data.
SAFE VERSION with extensive error handling and debugging.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import argparse
import sys
import traceback

# Import functions from original script
from generate_metro_rankings import (
    get_color_for_change, get_text_color, format_value, 
    format_change, METRICS, generate_html_page, generate_index_page
)

def safe_mean(series):
    """Safely calculate mean of a pandas Series."""
    try:
        if len(series) == 0:
            return 0.0
        
        # Drop NaN values first
        clean = series.dropna()
        if len(clean) == 0:
            return 0.0
            
        # Calculate mean and ensure it's a scalar
        result = clean.mean()
        
        # Force to scalar if needed
        if hasattr(result, '__iter__'):
            result = float(result.iloc[0]) if len(result) > 0 else 0.0
        else:
            result = float(result)
            
        return result if not np.isnan(result) else 0.0
    except Exception as e:
        print(f"Warning: safe_mean failed: {e}")
        return 0.0

def safe_calculate_market_size(df):
    """Calculate 5-year average homes sold with extensive error handling."""
    try:
        recent_data = df.tail(260)
        if 'ADJUSTED_AVERAGE_HOMES_SOLD' not in recent_data.columns:
            return 0.0
        
        col_data = recent_data['ADJUSTED_AVERAGE_HOMES_SOLD']
        return safe_mean(col_data)
    except Exception as e:
        print(f"Warning: market size calculation failed: {e}")
        return 0.0

def safe_calculate_changes(df, metric, periods):
    """Calculate percentage changes with error handling."""
    changes = {}
    
    try:
        df = df.sort_values('PERIOD_END')
        
        # Get latest value safely
        if len(df) == 0 or metric not in df.columns:
            return {p: None for p in periods}
            
        latest_val = df.iloc[-1][metric]
        
        # Skip if latest value is invalid
        if pd.isna(latest_val):
            return {p: None for p in periods}
        
        # Calculate changes for each period
        for period_name, weeks in periods.items():
            try:
                if len(df) > weeks:
                    past_val = df.iloc[-weeks-1][metric]
                    if pd.notna(past_val) and past_val != 0:
                        change = ((latest_val - past_val) / past_val) * 100
                        changes[period_name] = float(change)
                    else:
                        changes[period_name] = None
                else:
                    changes[period_name] = None
            except Exception:
                changes[period_name] = None
                
    except Exception as e:
        print(f"Warning: change calculation failed: {e}")
        return {p: None for p in periods}
    
    return changes

def main():
    parser = argparse.ArgumentParser(description='Generate metro rankings HTML pages (safe version)')
    parser.add_argument('--data-path', default='data/weekly_housing_market_data.parquet',
                       help='Path to parquet data file')
    parser.add_argument('--output-dir', default='rankings',
                       help='Output directory for HTML files')
    args = parser.parse_args()
    
    print("Loading Redfin data (safe mode)...")
    
    try:
        df = pd.read_parquet(args.data_path)
        print(f"Loaded {len(df)} rows")
    except Exception as e:
        print(f"Error loading data: {e}")
        sys.exit(1)
    
    # Filter to metro areas only
    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    print(f"Found {len(metros_df)} metro records")
    
    # Get latest date
    latest_date = metros_df['PERIOD_END'].max()
    date_str = pd.to_datetime(latest_date).strftime('%B %d, %Y')
    print(f"Generating rankings for {date_str}")
    
    # Calculate market sizes with error handling
    print("Calculating market sizes...")
    market_sizes = {}
    metro_list = metros_df['REGION_NAME'].unique()
    
    for i, metro in enumerate(metro_list):
        if i % 100 == 0:
            print(f"  Processing metro {i}/{len(metro_list)}...")
        
        try:
            metro_data = metros_df[metros_df['REGION_NAME'] == metro]
            market_sizes[metro] = safe_calculate_market_size(metro_data)
        except Exception as e:
            print(f"  Warning: Failed for {metro}: {e}")
            market_sizes[metro] = 0.0
    
    # Calculate percentiles
    print("Calculating percentiles...")
    sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'avg_homes_sold'])
    sizes_df['avg_homes_sold'] = sizes_df['avg_homes_sold'].fillna(0)
    
    # Safe ranking
    try:
        sizes_df['percentile'] = sizes_df['avg_homes_sold'].rank(pct=True, method='dense') * 100
        sizes_df['percentile'] = 100 - sizes_df['percentile']
    except Exception as e:
        print(f"Warning: Ranking failed, using default: {e}")
        sizes_df['percentile'] = 50.0  # Default to middle
    
    # Time periods
    periods = {
        '1month': 4,
        '3month': 13,
        '6month': 26,
        '1year': 52,
        '3year': 156
    }
    
    # Create output directory
    output_path = Path(args.output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Process each metric with error handling
    successful_metrics = []
    failed_metrics = []
    
    for metric_key, metric_info in METRICS.items():
        print(f"Processing {metric_info['display']}...")
        
        try:
            rankings_data = []
            
            # Check if metric exists
            if metric_key not in metros_df.columns:
                print(f"  Warning: Column {metric_key} not found in data")
                failed_metrics.append(metric_key)
                continue
            
            for metro in metro_list[:]:  # Process all metros
                try:
                    metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')
                    
                    if len(metro_data) == 0:
                        continue
                    
                    latest_data = metro_data.iloc[-1]
                    
                    # Get current value safely
                    current_val = latest_data.get(metric_key, None)
                    if pd.isna(current_val):
                        continue
                    
                    # Calculate changes safely
                    changes = safe_calculate_changes(metro_data, metric_key, periods)
                    
                    # Get market percentile
                    market_percentile = 50.0  # Default
                    if metro in sizes_df['metro'].values:
                        market_percentile = float(sizes_df[sizes_df['metro'] == metro]['percentile'].values[0])
                    
                    rankings_data.append({
                        'metro_name': metro.replace(' metro area', ''),
                        'current_value': float(current_val),
                        'changes': changes,
                        'market_percentile': market_percentile
                    })
                    
                except Exception as e:
                    print(f"  Warning: Failed for metro {metro}: {e}")
                    continue
            
            # Sort by 1-month change
            rankings_data.sort(key=lambda x: x['changes'].get('1month') or -999, reverse=True)
            
            # Generate HTML
            html = generate_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)
            
            # Save HTML file
            output_file = output_path / f"{metric_info['slug']}.html"
            with open(output_file, 'w') as f:
                f.write(html)
            
            print(f"  Saved {output_file}")
            successful_metrics.append(metric_key)
            
        except Exception as e:
            print(f"  ERROR processing {metric_key}: {e}")
            print(f"  Traceback: {traceback.format_exc()}")
            failed_metrics.append(metric_key)
    
    # Generate index
    try:
        index_html = generate_index_page(METRICS, date_str)
        with open(output_path / 'index.html', 'w') as f:
            f.write(index_html)
        print("Generated index.html")
    except Exception as e:
        print(f"Failed to generate index: {e}")
    
    # Summary
    print(f"\n=== Summary ===")
    print(f"Successful: {len(successful_metrics)} metrics")
    print(f"Failed: {len(failed_metrics)} metrics")
    if failed_metrics:
        print(f"Failed metrics: {failed_metrics}")
    
    # Exit with error if any metrics failed
    if failed_metrics:
        sys.exit(1)

if __name__ == '__main__':
    main()