#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages - Version 2
- Clickable column headers for sorting
- Compact rows for better data density
- Fixed market size filtering
- Color only the sorted column
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import argparse
import sys

# Color gradient for percentage changes
def get_color_for_change(change_pct):
    """Get background color based on percentage change."""
    if pd.isna(change_pct):
        return 'transparent'
    
    # Simplified gradient
    if change_pct <= -5:
        return '#3D3733'  # Black
    elif change_pct <= -2:
        return '#A09B95'  # Medium grey
    elif change_pct <= 0:
        return '#DADFCE'  # Light cream
    elif change_pct <= 2:
        return '#C6E4FF'  # Very light blue
    elif change_pct <= 5:
        return '#8CCFFF'  # Light blue
    else:
        return '#0BB4FF'  # Full blue

def get_text_color(bg_color):
    """Get text color based on background."""
    dark_colors = ['#3D3733', '#6B635C']
    return '#F6F7F3' if bg_color in dark_colors else '#3D3733'

# Metric definitions
METRICS = {
    'MEDIAN_SALE_PRICE': {
        'display': 'Median Sale Price',
        'format': 'currency',
        'slug': 'median_sale_price'
    },
    'ACTIVE_LISTINGS': {
        'display': 'Active Listings', 
        'format': 'number',
        'slug': 'active_listings'
    },
    'WEEKS_OF_SUPPLY': {
        'display': 'Weeks of Supply',
        'format': 'decimal1',
        'slug': 'weeks_supply'
    },
    'ADJUSTED_AVERAGE_HOMES_SOLD': {
        'display': 'Homes Sold',
        'format': 'number',
        'slug': 'homes_sold'
    },
    'ADJUSTED_AVERAGE_NEW_LISTINGS': {
        'display': 'New Listings',
        'format': 'number',
        'slug': 'new_listings'
    },
    'MEDIAN_DAYS_ON_MARKET': {
        'display': 'Days on Market',
        'format': 'number',
        'slug': 'median_days_on_market'
    },
    'AVERAGE_PENDING_SALES_LISTING_UPDATES': {
        'display': 'Pending Sales',
        'format': 'number',
        'slug': 'pending_sales'
    },
    'OFF_MARKET_IN_TWO_WEEKS': {
        'display': 'Off Market in 2 Weeks',
        'format': 'percent',
        'slug': 'off_market_in_2_weeks'
    },
    'MEDIAN_DAYS_TO_CLOSE': {
        'display': 'Days to Close',
        'format': 'number',
        'slug': 'median_days_to_close'
    },
    'AVERAGE_SALE_TO_LIST_RATIO': {
        'display': 'Sale to List Ratio',
        'format': 'percent',
        'slug': 'sale_to_list_ratio'
    },
    'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS': {
        'display': 'Price Drops',
        'format': 'percent',
        'slug': 'pct_listings_w__price_drops'
    },
    'AGE_OF_INVENTORY': {
        'display': 'Age of Inventory',
        'format': 'number',
        'slug': 'age_of_inventory'
    }
}

def format_value(value, format_type):
    """Format values for display."""
    if pd.isna(value):
        return '—'
    
    if format_type == 'currency':
        if value >= 1000000:
            return f'${value/1000000:.1f}M'
        elif value >= 1000:
            return f'${value/1000:.0f}K'
        else:
            return f'${value:.0f}'
    elif format_type == 'number':
        return f'{value:,.0f}'
    elif format_type == 'decimal1':
        return f'{value:.1f}'
    elif format_type == 'percent':
        return f'{value:.1f}%'
    return str(value)

def format_change(value):
    """Format percentage change."""
    if pd.isna(value):
        return '—'
    sign = '+' if value >= 0 else ''
    return f'{sign}{value:.1f}%'

def calculate_changes(df, metric, periods):
    """Calculate percentage changes for different time periods."""
    changes = {}
    df = df.sort_values('PERIOD_END')
    
    if len(df) == 0:
        return {p: None for p in periods}
    
    latest = df.iloc[-1][metric]
    if pd.isna(latest):
        return {p: None for p in periods}
    
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

def generate_html_page(rankings_data, metric_key, metric_info, all_metrics, date_str):
    """Generate HTML page with clickable column sorting."""
    
    # Build metric navigation buttons
    metric_buttons = []
    for m_key, m_info in all_metrics.items():
        is_active = 'active' if m_key == metric_key else ''
        metric_buttons.append(
            f'<a href="{m_info["slug"]}.html" class="metric-btn {is_active}">'
            f'{m_info["display"].replace(" ", "&nbsp;")}</a>'
        )
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{metric_info['display']} Rankings | Home Economics</title>
    <style>
        @font-face {{
            font-family: 'Oracle';
            src: url('https://www.home-economics.us/fonts/Oracle-Regular.woff2') format('woff2'),
                 url('https://www.home-economics.us/fonts/Oracle-Regular.woff') format('woff');
            font-weight: normal;
            font-style: normal;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Oracle', -apple-system, system-ui, sans-serif;
            background: #F6F7F3;
            color: #3D3733;
            font-size: 13px;
            line-height: 1.4;
            padding: 15px;
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        h1 {{
            font-size: 20px;
            font-weight: normal;
            margin-bottom: 15px;
            color: #3D3733;
        }}
        
        .controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        
        .metrics {{
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
        }}
        
        .metric-btn {{
            padding: 5px 10px;
            background: transparent;
            border: 1px solid #DADFCE;
            color: #3D3733;
            text-decoration: none;
            font-size: 12px;
            transition: all 0.2s;
            white-space: nowrap;
        }}
        
        .metric-btn:hover {{
            background: #DADFCE;
        }}
        
        .metric-btn.active {{
            background: #0BB4FF;
            border-color: #0BB4FF;
            color: #F6F7F3;
        }}
        
        .filter {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 12px;
        }}
        
        select {{
            padding: 4px 8px;
            border: 1px solid #DADFCE;
            background: #F6F7F3;
            font-family: inherit;
            font-size: 12px;
            color: #3D3733;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        th {{
            text-align: left;
            padding: 6px 8px;
            border-bottom: 1px solid #DADFCE;
            font-weight: normal;
            font-size: 11px;
            color: #6B635C;
            cursor: pointer;
            user-select: none;
            position: sticky;
            top: 0;
            background: #F6F7F3;
        }}
        
        th:hover {{
            background: #DADFCE;
        }}
        
        th.sorted {{
            background: #E8F4FF;
            color: #3D3733;
        }}
        
        th.number {{
            text-align: right;
        }}
        
        td {{
            padding: 5px 8px;
            border-bottom: 1px solid #F0F0EC;
            font-size: 12px;
        }}
        
        td.number {{
            text-align: right;
            font-variant-numeric: tabular-nums;
        }}
        
        td.rank {{
            color: #6B635C;
            width: 40px;
        }}
        
        td.metro {{
            font-weight: 500;
        }}
        
        td.colored {{
            padding: 3px 8px;
        }}
        
        tr:hover {{
            background: rgba(218, 223, 206, 0.2);
        }}
        
        .footer {{
            margin-top: 20px;
            padding-top: 15px;
            border-top: 1px solid #DADFCE;
            font-size: 11px;
            color: #6B635C;
        }}
        
        @media (max-width: 768px) {{
            body {{ font-size: 11px; }}
            th {{ font-size: 10px; }}
            td {{ font-size: 11px; padding: 4px 6px; }}
            .metric-btn {{ font-size: 11px; padding: 4px 8px; }}
        }}
    </style>
</head>
<body>
    <h1>{metric_info['display'].upper()}</h1>
    
    <div class="controls">
        <div class="metrics">
            {''.join(metric_buttons)}
        </div>
        
        <div class="filter">
            <label>Show:</label>
            <select id="marketFilter" onchange="filterTable()">
                <option value="10">Large Markets (Top 10%)</option>
                <option value="25" selected>Major Markets (Top 25%)</option>
                <option value="50">Mid-Size Markets (Top 50%)</option>
                <option value="100">All Markets</option>
            </select>
        </div>
    </div>
    
    <table id="rankingsTable">
        <thead>
            <tr>
                <th class="rank">#</th>
                <th onclick="sortBy('metro')">Metro Area</th>
                <th class="number sorted" onclick="sortBy('current')">Current</th>
                <th class="number" onclick="sortBy('1mo')">1 Mo</th>
                <th class="number" onclick="sortBy('3mo')">3 Mo</th>
                <th class="number" onclick="sortBy('6mo')">6 Mo</th>
                <th class="number" onclick="sortBy('1yr')">1 Yr</th>
                <th class="number" onclick="sortBy('3yr')">3 Yr</th>
            </tr>
        </thead>
        <tbody>
"""
    
    # Add all data rows (JavaScript will handle filtering)
    for i, row in enumerate(rankings_data, 1):
        metro_slug = row['metro_name'].lower().replace(', ', '_').replace(' ', '_').replace('.', '')
        
        # Format current value
        current_val = format_value(row['current_value'], metric_info['format'])
        
        # Build row HTML
        html += f'''            <tr data-percentile="{row['market_percentile']:.1f}" '''
        html += f'data-current="{row["current_value"]}" '
        html += f'data-metro="{row["metro_name"].lower()}" '
        
        # Add data for each change period
        for period in ['1mo', '3mo', '6mo', '1yr', '3yr']:
            change_key = period.replace('mo', 'month').replace('yr', 'year')
            val = row['changes'].get(change_key, 0) or 0
            html += f'data-{period}="{val:.2f}" '
        
        html += f'>\n'
        html += f'                <td class="rank">{i}</td>\n'
        html += f'                <td class="metro">{row["metro_name"]}</td>\n'
        html += f'                <td class="number current-col">{current_val}</td>\n'
        
        # Add change columns
        for period in ['1mo', '3mo', '6mo', '1yr', '3yr']:
            change_key = period.replace('mo', 'month').replace('yr', 'year')
            change_val = row['changes'].get(change_key)
            change_text = format_change(change_val)
            
            # Only color the sorted column (handled by JavaScript)
            html += f'                <td class="number {period}-col">{change_text}</td>\n'
        
        html += '            </tr>\n'
    
    html += f"""        </tbody>
    </table>
    
    <div class="footer">
        Data updated: {date_str} | Source: Redfin | <a href="https://www.home-economics.us">Home Economics</a>
    </div>
    
    <script>
        let currentSort = 'current';
        let sortAscending = false;
        let allRows = [];
        
        // Initialize table data
        function init() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            allRows = Array.from(tbody.querySelectorAll('tr'));
            filterTable();
        }}
        
        // Sort by column
        function sortBy(column) {{
            // Toggle sort direction if same column
            if (currentSort === column) {{
                sortAscending = !sortAscending;
            }} else {{
                sortAscending = false; // Default to descending
                currentSort = column;
            }}
            
            // Update header styles
            document.querySelectorAll('th').forEach(th => th.classList.remove('sorted'));
            event.target.classList.add('sorted');
            
            // Sort rows
            allRows.sort((a, b) => {{
                let aVal, bVal;
                
                if (column === 'metro') {{
                    aVal = a.dataset.metro;
                    bVal = b.dataset.metro;
                    return sortAscending ? 
                        aVal.localeCompare(bVal) : 
                        bVal.localeCompare(aVal);
                }} else {{
                    aVal = parseFloat(a.dataset[column]) || -999;
                    bVal = parseFloat(b.dataset[column]) || -999;
                    return sortAscending ? aVal - bVal : bVal - aVal;
                }}
            }});
            
            // Apply coloring only to sorted column
            updateColoring(column);
            
            // Re-render filtered table
            filterTable();
        }}
        
        // Update cell coloring based on sorted column
        function updateColoring(column) {{
            allRows.forEach(row => {{
                // Remove all existing colors
                row.querySelectorAll('td').forEach(td => {{
                    td.style.backgroundColor = '';
                    td.style.color = '';
                    td.classList.remove('colored');
                }});
                
                // Color only the change columns when sorted
                if (column !== 'current' && column !== 'metro') {{
                    const td = row.querySelector('.' + column + '-col');
                    if (td) {{
                        const val = parseFloat(row.dataset[column]);
                        if (!isNaN(val) && val !== 0) {{
                            const bgColor = getColorForChange(val);
                            if (bgColor !== 'transparent') {{
                                td.style.backgroundColor = bgColor;
                                td.style.color = getTextColor(bgColor);
                                td.classList.add('colored');
                            }}
                        }}
                    }}
                }}
            }});
        }}
        
        // Get color for percentage change
        function getColorForChange(val) {{
            if (val <= -5) return '#3D3733';
            if (val <= -2) return '#A09B95';
            if (val <= 0) return '#DADFCE';
            if (val <= 2) return '#C6E4FF';
            if (val <= 5) return '#8CCFFF';
            return '#0BB4FF';
        }}
        
        // Get text color based on background
        function getTextColor(bgColor) {{
            return (bgColor === '#3D3733' || bgColor === '#6B635C') ? '#F6F7F3' : '#3D3733';
        }}
        
        // Filter table by market size
        function filterTable() {{
            const filter = parseFloat(document.getElementById('marketFilter').value);
            const tbody = document.querySelector('#rankingsTable tbody');
            tbody.innerHTML = '';
            
            let rank = 1;
            allRows.forEach(row => {{
                const percentile = parseFloat(row.dataset.percentile);
                
                // Show if within percentile threshold
                if (percentile <= filter) {{
                    row.querySelector('.rank').textContent = rank++;
                    tbody.appendChild(row);
                }}
            }});
        }}
        
        // Start
        init();
    </script>
</body>
</html>"""
    
    return html

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-path', default='data/weekly_housing_market_data.parquet')
    parser.add_argument('--output-dir', default='rankings')
    args = parser.parse_args()
    
    print("Loading Redfin data...")
    df = pd.read_parquet(args.data_path)
    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    
    # Get latest date
    latest_date = metros_df['PERIOD_END'].max()
    date_str = pd.to_datetime(latest_date).strftime('%B %d, %Y')
    
    # Calculate market sizes (5-year average homes sold)
    print("Calculating market sizes...")
    market_sizes = {}
    for metro in metros_df['REGION_NAME'].unique():
        metro_data = metros_df[metros_df['REGION_NAME'] == metro]
        if 'ADJUSTED_AVERAGE_HOMES_SOLD' in metro_data.columns:
            recent = metro_data.tail(260)  # 5 years
            if len(recent) > 0:
                market_sizes[metro] = recent['ADJUSTED_AVERAGE_HOMES_SOLD'].mean()
            else:
                market_sizes[metro] = 0
        else:
            market_sizes[metro] = 0
    
    # Calculate percentiles (FIXED: larger markets get LOWER percentile numbers)
    sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'avg_homes_sold'])
    sizes_df['avg_homes_sold'] = sizes_df['avg_homes_sold'].fillna(0)
    sizes_df = sizes_df.sort_values('avg_homes_sold', ascending=False)  # Largest first
    sizes_df['percentile'] = (sizes_df.index + 1) / len(sizes_df) * 100  # Rank 1 = percentile 0.1%
    
    print(f"Market size range: {sizes_df['avg_homes_sold'].max():.0f} to {sizes_df['avg_homes_sold'].min():.0f}")
    print(f"Top 10% threshold: {sizes_df[sizes_df['percentile'] <= 10]['avg_homes_sold'].min():.0f} homes/week")
    
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
    
    # Process each metric
    for metric_key, metric_info in METRICS.items():
        if metric_key not in metros_df.columns:
            print(f"Skipping {metric_key} - not in data")
            continue
            
        print(f"Processing {metric_info['display']}...")
        
        rankings_data = []
        for metro in metros_df['REGION_NAME'].unique():
            metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')
            
            if len(metro_data) == 0:
                continue
                
            latest_data = metro_data.iloc[-1]
            if pd.isna(latest_data[metric_key]):
                continue
            
            # Calculate changes
            changes = calculate_changes(metro_data, metric_key, periods)
            
            # Get market percentile
            percentile = sizes_df[sizes_df['metro'] == metro]['percentile'].values
            if len(percentile) > 0:
                market_percentile = percentile[0]
            else:
                market_percentile = 100.0
            
            rankings_data.append({
                'metro_name': metro.replace(' metro area', ''),
                'current_value': latest_data[metric_key],
                'changes': changes,
                'market_percentile': market_percentile
            })
        
        # Sort by current value initially
        rankings_data.sort(key=lambda x: x['current_value'], reverse=True)
        
        # Generate and save HTML
        html = generate_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)
        output_file = output_path / f"{metric_info['slug']}.html"
        with open(output_file, 'w') as f:
            f.write(html)
        print(f"  Saved {output_file}")
    
    # Create index redirect
    with open(output_path / 'index.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0; url=median_sale_price.html"></head>
<body>Redirecting...</body></html>''')
    
    print(f"Generated {len(METRICS)} ranking pages")

if __name__ == '__main__':
    main()