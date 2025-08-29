#!/usr/bin/env python3
"""Quick test for generating a single ranking page."""

import pandas as pd
import numpy as np
from pathlib import Path
from generate_metro_rankings import (
    METRICS, format_value, format_change, get_color_for_change, 
    get_text_color, calculate_market_size, generate_html_page
)

print("Loading data...")
df = pd.read_parquet('data/weekly_housing_market_data.parquet')

# Filter to metros
metros_df = df[df['REGION_TYPE'] == 'metro'].copy()

# Get latest date
latest_date = metros_df['PERIOD_END'].max()
date_str = pd.to_datetime(latest_date).strftime('%B %d, %Y')
print(f"Latest data: {date_str}")

# Calculate market sizes for filtering
print("Calculating market sizes...")
market_sizes = {}
for metro in metros_df['REGION_NAME'].unique()[:100]:  # Just top 100 for speed
    metro_data = metros_df[metros_df['REGION_NAME'] == metro]
    if 'ADJUSTED_AVERAGE_HOMES_SOLD' in metro_data.columns:
        # Use last 52 weeks instead of 260 for speed
        recent = metro_data.tail(52)
        market_sizes[metro] = recent['ADJUSTED_AVERAGE_HOMES_SOLD'].mean()

# Get percentiles
sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'avg_homes_sold'])
sizes_df['percentile'] = sizes_df['avg_homes_sold'].rank(pct=True) * 100
sizes_df['percentile'] = 100 - sizes_df['percentile']

# Process just MEDIAN_SALE_PRICE
metric_key = 'MEDIAN_SALE_PRICE'
metric_info = METRICS[metric_key]

print(f"Processing {metric_info['display']}...")

# Get latest values for top metros
latest_metros = metros_df[metros_df['PERIOD_END'] == latest_date]
top_metros = latest_metros.nlargest(50, metric_key)['REGION_NAME'].unique()

rankings_data = []
for metro in top_metros[:30]:  # Just top 30 for test
    metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')
    
    if len(metro_data) == 0:
        continue
        
    latest = metro_data.iloc[-1]
    
    # Simple change calculation (1 month only for test)
    changes = {}
    if len(metro_data) > 4:
        past_val = metro_data.iloc[-5][metric_key]
        if pd.notna(past_val) and past_val > 0:
            changes['1month'] = ((latest[metric_key] - past_val) / past_val) * 100
    
    # Add placeholder changes
    for period in ['3month', '6month', '1year', '3year']:
        changes[period] = np.random.uniform(-5, 5)  # Random for test
    
    # Get market percentile if available
    market_percentile = 25  # Default to major market
    if metro in sizes_df['metro'].values:
        market_percentile = sizes_df[sizes_df['metro'] == metro]['percentile'].values[0]
    
    rankings_data.append({
        'metro_name': metro.replace(' metro area', ''),
        'current_value': latest[metric_key],
        'changes': changes,
        'market_percentile': market_percentile
    })

# Sort by current value
rankings_data.sort(key=lambda x: x['current_value'], reverse=True)

print(f"Generating HTML for {len(rankings_data)} metros...")

# Generate HTML
html = generate_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)

# Save
output_path = Path('test_rankings')
output_path.mkdir(exist_ok=True)
output_file = output_path / f"{metric_info['slug']}.html"

with open(output_file, 'w') as f:
    f.write(html)

print(f"Saved to {output_file}")
print(f"Open with: open {output_file}")