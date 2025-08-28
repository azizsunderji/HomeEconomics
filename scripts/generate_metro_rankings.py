#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages from Redfin data.
Creates clean, minimal ranking tables with Home Economics branding.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import argparse
import sys

# Color gradient for percentage changes (matching map colors)
def get_color_for_change(change_pct):
    """Get background color based on percentage change."""
    if pd.isna(change_pct):
        return '#F6F7F3'  # Background cream for no data
    
    # Define breakpoints and colors
    if change_pct <= -10:
        return '#3D3733'  # Black
    elif change_pct <= -5:
        return '#6B635C'  # Dark grey-brown
    elif change_pct <= -2:
        return '#A09B95'  # Medium grey
    elif change_pct <= 0:
        return '#DADFCE'  # Light cream
    elif change_pct <= 2:
        return '#C6E4FF'  # Very light blue
    elif change_pct <= 5:
        return '#8CCFFF'  # Light blue
    elif change_pct <= 10:
        return '#52B9FF'  # Medium blue
    else:
        return '#0BB4FF'  # Full blue

def get_text_color(bg_color):
    """Get text color based on background."""
    dark_colors = ['#3D3733', '#6B635C']
    return '#F6F7F3' if bg_color in dark_colors else '#3D3733'

# Metric definitions with display names and formatting
METRICS = {
    'MEDIAN_SALE_PRICE': {
        'display': 'Median Sale Price',
        'format': 'currency',
        'suffix': '',
        'button_label': 'Price',
        'slug': 'median_sale_price'
    },
    'ACTIVE_LISTINGS': {
        'display': 'Active Listings',
        'format': 'number',
        'suffix': '',
        'button_label': 'Listings',
        'slug': 'active_listings'
    },
    'WEEKS_OF_SUPPLY': {
        'display': 'Weeks of Supply',
        'format': 'decimal',
        'suffix': ' weeks',
        'button_label': 'Supply',
        'slug': 'weeks_supply'
    },
    'ADJUSTED_AVERAGE_HOMES_SOLD': {
        'display': 'Homes Sold',
        'format': 'number',
        'suffix': '',
        'button_label': 'Sold',
        'slug': 'homes_sold'
    },
    'ADJUSTED_AVERAGE_NEW_LISTINGS': {
        'display': 'New Listings',
        'format': 'number',
        'suffix': '',
        'button_label': 'New',
        'slug': 'new_listings'
    },
    'MEDIAN_DAYS_ON_MARKET': {
        'display': 'Days on Market',
        'format': 'number',
        'suffix': ' days',
        'button_label': 'DOM',
        'slug': 'median_days_on_market'
    },
    'AVERAGE_PENDING_SALES_LISTING_UPDATES': {
        'display': 'Pending Sales',
        'format': 'number',
        'suffix': '',
        'button_label': 'Pending',
        'slug': 'pending_sales'
    },
    'OFF_MARKET_IN_TWO_WEEKS': {
        'display': 'Off Market in 2 Weeks',
        'format': 'percent',
        'suffix': '',
        'button_label': '2 Weeks',
        'slug': 'off_market_in_2_weeks'
    },
    'MEDIAN_DAYS_TO_CLOSE': {
        'display': 'Days to Close',
        'format': 'number',
        'suffix': ' days',
        'button_label': 'Close',
        'slug': 'median_days_to_close'
    },
    'AVERAGE_SALE_TO_LIST_RATIO': {
        'display': 'Sale to List Ratio',
        'format': 'percent',
        'suffix': '',
        'button_label': 'Ratio',
        'slug': 'sale_to_list_ratio'
    },
    'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS': {
        'display': 'Price Drops',
        'format': 'percent',
        'suffix': '',
        'button_label': 'Drops',
        'slug': 'pct_listings_w__price_drops'
    },
    'AGE_OF_INVENTORY': {
        'display': 'Age of Inventory',
        'format': 'number',
        'suffix': ' days',
        'button_label': 'Age',
        'slug': 'age_of_inventory'
    }
}

def format_value(value, format_type):
    """Format values for display."""
    if pd.isna(value):
        return 'N/A'
    
    if format_type == 'currency':
        return f'${value:,.0f}'
    elif format_type == 'number':
        return f'{value:,.0f}'
    elif format_type == 'decimal':
        return f'{value:.1f}'
    elif format_type == 'percent':
        return f'{value:.1f}%'
    return str(value)

def format_change(value):
    """Format percentage change with + or - sign."""
    if pd.isna(value):
        return '—'
    sign = '+' if value >= 0 else ''
    return f'{sign}{value:.1f}%'

def calculate_changes(df, metric, periods):
    """Calculate percentage changes for different time periods."""
    changes = {}
    
    # Sort by date
    df = df.sort_values('PERIOD_END')
    
    # Get latest value
    latest = df.iloc[-1][metric]
    
    # Calculate changes for each period
    for period_name, weeks in periods.items():
        if len(df) > weeks:
            past_value = df.iloc[-weeks-1][metric]
            if pd.notna(past_value) and past_value != 0:
                change = ((latest - past_value) / past_value) * 100
                changes[period_name] = change
            else:
                changes[period_name] = None
        else:
            changes[period_name] = None
    
    return changes

def calculate_market_size(df):
    """Calculate 5-year average homes sold for market sizing."""
    # Get last 5 years of data (260 weeks)
    recent_data = df.tail(260)
    if 'ADJUSTED_AVERAGE_HOMES_SOLD' in recent_data.columns and len(recent_data) > 0:
        try:
            avg = float(recent_data['ADJUSTED_AVERAGE_HOMES_SOLD'].mean())
            if np.isnan(avg):
                return 0.0
            return avg
        except (TypeError, ValueError):
            return 0.0
    return 0.0

def generate_html_page(rankings_data, metric_key, metric_info, all_metrics, date_str):
    """Generate HTML page for a single metric."""
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{metric_info['display']} - Metro Rankings | Home Economics</title>
    <style>
        @font-face {{
            font-family: 'Oracle';
            src: url('/fonts/Oracle-Regular.woff2') format('woff2'),
                 url('/fonts/Oracle-Regular.woff') format('woff');
            font-weight: normal;
        }}
        
        @font-face {{
            font-family: 'Oracle';
            src: url('/fonts/Oracle-Bold.woff2') format('woff2'),
                 url('/fonts/Oracle-Bold.woff') format('woff');
            font-weight: bold;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Oracle', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: #F6F7F3;
            color: #3D3733;
            line-height: 1.6;
            padding: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        h1 {{
            font-size: 24px;
            font-weight: normal;
            margin-bottom: 20px;
            letter-spacing: -0.5px;
        }}
        
        .highlight {{
            color: #0BB4FF;
        }}
        
        .controls {{
            margin-bottom: 30px;
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            align-items: center;
        }}
        
        .metric-buttons {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        
        .metric-button {{
            padding: 8px 16px;
            background: transparent;
            border: 1px solid #DADFCE;
            color: #3D3733;
            cursor: pointer;
            font-family: inherit;
            font-size: 14px;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-block;
        }}
        
        .metric-button:hover {{
            background: #DADFCE;
        }}
        
        .metric-button.active {{
            background: #0BB4FF;
            border-color: #0BB4FF;
            color: #F6F7F3;
        }}
        
        .filters {{
            display: flex;
            gap: 15px;
            align-items: center;
            margin-left: auto;
        }}
        
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .filter-label {{
            font-size: 14px;
            color: #6B635C;
        }}
        
        select {{
            padding: 6px 12px;
            border: 1px solid #DADFCE;
            background: #F6F7F3;
            font-family: inherit;
            font-size: 14px;
            color: #3D3733;
            cursor: pointer;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        
        th {{
            text-align: left;
            padding: 12px;
            border-bottom: 1px solid #DADFCE;
            font-weight: normal;
            font-size: 14px;
            color: #6B635C;
        }}
        
        td {{
            padding: 12px;
            border-bottom: 1px solid #F0F0EC;
            font-size: 15px;
        }}
        
        tr:hover {{
            background: rgba(218, 223, 206, 0.2);
        }}
        
        .rank {{
            width: 60px;
            color: #6B635C;
        }}
        
        .metro-name {{
            font-weight: 500;
        }}
        
        .metro-name a {{
            color: inherit;
            text-decoration: none;
        }}
        
        .metro-name a:hover {{
            color: #0BB4FF;
        }}
        
        .value {{
            text-align: right;
            font-variant-numeric: tabular-nums;
        }}
        
        .change {{
            text-align: right;
            padding: 8px 12px;
            font-variant-numeric: tabular-nums;
            font-size: 14px;
        }}
        
        .metadata {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #DADFCE;
            font-size: 13px;
            color: #6B635C;
        }}
        
        @media (max-width: 768px) {{
            .controls {{
                flex-direction: column;
                align-items: flex-start;
            }}
            
            .filters {{
                margin-left: 0;
                flex-direction: column;
                align-items: flex-start;
                width: 100%;
            }}
            
            table {{
                font-size: 14px;
            }}
            
            th, td {{
                padding: 8px;
            }}
        }}
    </style>
</head>
<body>
    <h1><span class="highlight">{metric_info['display'].upper()}</span></h1>
    
    <div class="controls">
        <div class="metric-buttons">
"""
    
    # Add metric buttons
    for other_metric, other_info in all_metrics.items():
        active = 'active' if other_metric == metric_key else ''
        filename = other_info['slug'] + '.html'
        html += f"""            <a href="{filename}" class="metric-button {active}">{other_info['button_label']}</a>
"""
    
    html += f"""        </div>
        
        <div class="filters">
            <div class="filter-group">
                <span class="filter-label">Sort by:</span>
                <select id="sortPeriod" onchange="sortTable()">
                    <option value="current">Current Value</option>
                    <option value="1month" selected>1 Month Change</option>
                    <option value="3month">3 Month Change</option>
                    <option value="6month">6 Month Change</option>
                    <option value="1year">1 Year Change</option>
                    <option value="3year">3 Year Change</option>
                </select>
            </div>
            
            <div class="filter-group">
                <span class="filter-label">Filter:</span>
                <select id="marketSize" onchange="filterTable()">
                    <option value="top10">Large Markets (Top 10%)</option>
                    <option value="top25" selected>Major Markets (Top 25%)</option>
                    <option value="top50">Mid-Size (Top 50%)</option>
                    <option value="all">All Markets</option>
                </select>
            </div>
        </div>
    </div>
    
    <table id="rankingsTable">
        <thead>
            <tr>
                <th class="rank">Rank</th>
                <th>Metro Area</th>
                <th class="value">Current Value</th>
                <th class="change">1 Mo</th>
                <th class="change">3 Mo</th>
                <th class="change">6 Mo</th>
                <th class="change">1 Yr</th>
                <th class="change">3 Yr</th>
            </tr>
        </thead>
        <tbody>
"""
    
    # Add data rows
    for idx, row in enumerate(rankings_data[:100], 1):  # Top 100 metros
        metro_slug = row['metro_name'].lower().replace(', ', '_').replace(' ', '_').replace('.', '')
        chart_url = f"https://www.home-economics.us/live/mobile/{metro_slug}/{metric_info['slug']}.png"
        
        # Format values
        current_value = format_value(row['current_value'], metric_info['format'])
        if metric_info['suffix']:
            current_value += metric_info['suffix']
        
        # Create row with proper coloring for each change cell
        html += f"""            <tr data-rank="{idx}" data-marketsize="{row['market_percentile']}" """
        
        # Add data attributes for sorting
        for period in ['1month', '3month', '6month', '1year', '3year']:
            change_val = row['changes'].get(period, 0) or 0
            html += f'data-{period}="{change_val}" '
        
        html += f'data-current="{row["current_value"]}">\n'
        html += f"""                <td class="rank">{idx}</td>
                <td class="metro-name"><a href="{chart_url}" target="_blank">{row['metro_name']}</a></td>
                <td class="value">{current_value}</td>
"""
        
        # Add change cells with individual coloring
        for period in ['1month', '3month', '6month', '1year', '3year']:
            change_val = row['changes'].get(period)
            change_text = format_change(change_val)
            bg_color = get_color_for_change(change_val)
            text_color = get_text_color(bg_color)
            
            if bg_color != '#F6F7F3':  # Only add inline style if not default
                html += f"""                <td class="change" style="background-color: {bg_color}; color: {text_color};">{change_text}</td>
"""
            else:
                html += f"""                <td class="change">{change_text}</td>
"""
        
        html += """            </tr>
"""
    
    html += f"""        </tbody>
    </table>
    
    <div class="metadata">
        <p>Data updated: {date_str} | Source: Redfin | <a href="https://www.home-economics.us">Home Economics</a></p>
    </div>
    
    <script>
        let currentData = [];
        
        function initializeTable() {{
            const rows = document.querySelectorAll('#rankingsTable tbody tr');
            rows.forEach(row => {{
                currentData.push(row);
            }});
            filterTable();
        }}
        
        function filterTable() {{
            const filterValue = document.getElementById('marketSize').value;
            const tbody = document.querySelector('#rankingsTable tbody');
            
            let thresholds = {{
                'top10': 10,
                'top25': 25,
                'top50': 50,
                'all': 100
            }};
            
            let threshold = thresholds[filterValue];
            let visibleRank = 1;
            
            currentData.forEach(row => {{
                const percentile = parseFloat(row.dataset.marketsize);
                if (percentile <= threshold) {{
                    row.style.display = '';
                    row.querySelector('.rank').textContent = visibleRank++;
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }}
        
        function sortTable() {{
            const sortBy = document.getElementById('sortPeriod').value;
            const tbody = document.querySelector('#rankingsTable tbody');
            
            // Sort the data array
            currentData.sort((a, b) => {{
                const aVal = parseFloat(a.dataset[sortBy]) || -999;
                const bVal = parseFloat(b.dataset[sortBy]) || -999;
                return bVal - aVal;  // Descending order
            }});
            
            // Reorder DOM elements
            currentData.forEach(row => {{
                tbody.appendChild(row);
            }});
            
            // Reapply filter and update ranks
            filterTable();
        }}
        
        // Initialize on load
        initializeTable();
    </script>
</body>
</html>"""
    
    return html

def generate_index_page(all_metrics, date_str):
    """Generate index page that redirects to median sale price."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url=median_sale_price.html">
    <title>Metro Rankings | Home Economics</title>
</head>
<body>
    <p>Redirecting to <a href="median_sale_price.html">Metro Rankings</a>...</p>
</body>
</html>"""
    return html

def main():
    parser = argparse.ArgumentParser(description='Generate metro rankings HTML pages')
    parser.add_argument('--data-path', default='data/weekly_housing_market_data.parquet',
                       help='Path to parquet data file')
    parser.add_argument('--output-dir', default='rankings',
                       help='Output directory for HTML files')
    args = parser.parse_args()
    
    print("Loading Redfin data...")
    # Only load the columns we need for efficiency
    needed_cols = ['REGION_NAME', 'REGION_TYPE', 'PERIOD_END', 'ADJUSTED_AVERAGE_HOMES_SOLD'] + list(METRICS.keys())
    
    # Load the full dataframe first
    df_full = pd.read_parquet(args.data_path)
    
    # Check which columns actually exist
    available_cols = [col for col in needed_cols if col in df_full.columns]
    missing_cols = [col for col in needed_cols if col not in df_full.columns]
    
    if missing_cols:
        print(f"Warning: Missing columns: {missing_cols}")
    
    print(f"Loading {len(available_cols)} columns...")
    df = df_full[available_cols].copy()
    del df_full  # Free memory
    
    # Filter to metro areas only
    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    
    # Get latest date
    latest_date = metros_df['PERIOD_END'].max()
    date_str = pd.to_datetime(latest_date).strftime('%B %d, %Y')
    
    print(f"Generating rankings for {date_str}")
    
    # Calculate market sizes (5-year average homes sold)
    print("Calculating market sizes...")
    market_sizes = {}
    for metro in metros_df['REGION_NAME'].unique():
        metro_data = metros_df[metros_df['REGION_NAME'] == metro]
        market_sizes[metro] = calculate_market_size(metro_data)
    
    # Calculate percentiles for market sizing
    sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'avg_homes_sold'])
    # Handle NaN and zero values before ranking
    sizes_df['avg_homes_sold'] = sizes_df['avg_homes_sold'].fillna(0)
    sizes_df['percentile'] = sizes_df['avg_homes_sold'].rank(pct=True, method='dense') * 100
    sizes_df['percentile'] = 100 - sizes_df['percentile']  # Invert so 1 = largest
    
    # Time periods for changes (in weeks)
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
    
    # Process each metric
    for metric_key, metric_info in METRICS.items():
        print(f"Processing {metric_info['display']}...")
        
        rankings_data = []
        
        for metro in metros_df['REGION_NAME'].unique():
            metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')
            
            # Skip if no recent data
            if len(metro_data) == 0:
                continue
            
            latest_data = metro_data.iloc[-1]
            
            # Skip if current value is missing
            if pd.isna(latest_data[metric_key]):
                continue
            
            # Calculate changes
            changes = calculate_changes(metro_data, metric_key, periods)
            
            # Get market percentile
            market_percentile = sizes_df[sizes_df['metro'] == metro]['percentile'].values[0]
            
            rankings_data.append({
                'metro_name': metro.replace(' metro area', ''),
                'current_value': latest_data[metric_key],
                'changes': changes,
                'market_percentile': market_percentile
            })
        
        # Sort by 1-month change by default
        rankings_data.sort(key=lambda x: x['changes'].get('1month') or -999, reverse=True)
        
        # Generate HTML page
        html = generate_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)
        
        # Save HTML file
        output_file = output_path / f"{metric_info['slug']}.html"
        with open(output_file, 'w') as f:
            f.write(html)
        
        print(f"  Saved {output_file}")
    
    # Generate index page
    index_html = generate_index_page(METRICS, date_str)
    with open(output_path / 'index.html', 'w') as f:
        f.write(index_html)
    
    print(f"\nGenerated {len(METRICS)} ranking pages in {output_path}/")

if __name__ == '__main__':
    main()