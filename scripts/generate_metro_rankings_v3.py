#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages - Version 3
- Fixed market size calculation using consistent duration
- Fixed JavaScript sorting functionality
- Proper 5-year homes sold aggregation
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
    
    # Use only 4-week duration data for consistency
    df = df[df['DURATION'] == '4 weeks'].copy() if 'DURATION' in df.columns else df
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

def calculate_market_size(metro_data):
    """Calculate total homes sold over last 5 years."""
    # Use 4-week duration data for consistency
    if 'DURATION' in metro_data.columns:
        metro_data = metro_data[metro_data['DURATION'] == '4 weeks'].copy()
    
    # Get last 5 years of data
    # Ensure PERIOD_END is datetime
    metro_data['PERIOD_END'] = pd.to_datetime(metro_data['PERIOD_END'])
    latest_date = metro_data['PERIOD_END'].max()
    five_years_ago = latest_date - pd.Timedelta(days=365*5)
    recent_data = metro_data[metro_data['PERIOD_END'] > five_years_ago]
    
    if 'ADJUSTED_AVERAGE_HOMES_SOLD' in recent_data.columns and len(recent_data) > 0:
        # Since it's average homes sold per period, multiply by number of periods
        # Each row represents 4 weeks of sales
        total = recent_data['ADJUSTED_AVERAGE_HOMES_SOLD'].sum() * 4  # Convert weekly average to 4-week total
        return total
    return 0

def generate_html_page(rankings_data, metric_key, metric_info, all_metrics, date_str):
    """Generate HTML page with working column sorting."""
    
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
            white-space: nowrap;
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
        
        .arrow {{
            display: inline-block;
            margin-left: 4px;
            font-size: 10px;
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
                <th data-sort="metro">Metro Area <span class="arrow"></span></th>
                <th class="number sorted" data-sort="current">Current <span class="arrow">↓</span></th>
                <th class="number" data-sort="month1">1 Mo <span class="arrow"></span></th>
                <th class="number" data-sort="month3">3 Mo <span class="arrow"></span></th>
                <th class="number" data-sort="month6">6 Mo <span class="arrow"></span></th>
                <th class="number" data-sort="year1">1 Yr <span class="arrow"></span></th>
                <th class="number" data-sort="year3">3 Yr <span class="arrow"></span></th>
            </tr>
        </thead>
        <tbody>
"""
    
    # Add all data rows
    for i, row in enumerate(rankings_data, 1):
        metro_slug = row['metro_name'].lower().replace(', ', '_').replace(' ', '_').replace('.', '')
        
        # Format current value
        current_val = format_value(row['current_value'], metric_info['format'])
        
        # Build row HTML with proper data attributes
        html += f'''            <tr data-percentile="{row['market_percentile']:.1f}" '''
        html += f'data-current="{row["current_value"]}" '
        html += f'data-metro="{row["metro_name"].lower()}" '
        
        # Add data for each change period - MUST match header data-sort values
        html += f'data-month1="{row["changes"].get("1month", 0) or 0:.2f}" '
        html += f'data-month3="{row["changes"].get("3month", 0) or 0:.2f}" '
        html += f'data-month6="{row["changes"].get("6month", 0) or 0:.2f}" '
        html += f'data-year1="{row["changes"].get("1year", 0) or 0:.2f}" '
        html += f'data-year3="{row["changes"].get("3year", 0) or 0:.2f}" '
        
        html += f'>\n'
        html += f'                <td class="rank">{i}</td>\n'
        html += f'                <td class="metro">{row["metro_name"]}</td>\n'
        html += f'                <td class="number col-current">{current_val}</td>\n'
        
        # Add change columns with class names matching data-sort values
        periods_map = [
            ('1month', 'month1'),
            ('3month', 'month3'),
            ('6month', 'month6'),
            ('1year', 'year1'),
            ('3year', 'year3')
        ]
        for data_key, col_class in periods_map:
            change_val = row['changes'].get(data_key)
            change_text = format_change(change_val)
            html += f'                <td class="number col-{col_class}">{change_text}</td>\n'
        
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
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            allRows = Array.from(tbody.querySelectorAll('tr'));
            
            // Add click handlers to headers
            document.querySelectorAll('th[data-sort]').forEach(th => {{
                th.addEventListener('click', function() {{
                    sortBy(this.dataset.sort);
                }});
            }});
            
            filterTable();
        }});
        
        // Sort by column
        function sortBy(column) {{
            console.log('Sorting by:', column); // Debug
            
            // Toggle direction if same column
            if (currentSort === column) {{
                sortAscending = !sortAscending;
            }} else {{
                sortAscending = false; // Default descending
                currentSort = column;
            }}
            
            // Update header styles and arrows
            document.querySelectorAll('th').forEach(th => {{
                th.classList.remove('sorted');
                const arrow = th.querySelector('.arrow');
                if (arrow) arrow.textContent = '';
            }});
            
            const currentTh = document.querySelector(`th[data-sort="${{column}}"]`);
            if (currentTh) {{
                currentTh.classList.add('sorted');
                const arrow = currentTh.querySelector('.arrow');
                if (arrow) arrow.textContent = sortAscending ? '↑' : '↓';
            }}
            
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
            
            // Apply coloring
            updateColoring(column);
            
            // Re-render
            filterTable();
        }}
        
        // Update coloring
        function updateColoring(column) {{
            allRows.forEach(row => {{
                // Clear all colors
                row.querySelectorAll('td').forEach(td => {{
                    td.style.backgroundColor = '';
                    td.style.color = '';
                    td.classList.remove('colored');
                }});
                
                // Color only change columns when sorted
                if (column !== 'current' && column !== 'metro') {{
                    const td = row.querySelector('.col-' + column);
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
        
        // Get color for change
        function getColorForChange(val) {{
            if (val <= -5) return '#3D3733';
            if (val <= -2) return '#A09B95';
            if (val <= 0) return '#DADFCE';
            if (val <= 2) return '#C6E4FF';
            if (val <= 5) return '#8CCFFF';
            return '#0BB4FF';
        }}
        
        // Get text color
        function getTextColor(bgColor) {{
            return (bgColor === '#3D3733' || bgColor === '#6B635C') ? '#F6F7F3' : '#3D3733';
        }}
        
        // Filter table
        function filterTable() {{
            const filter = parseFloat(document.getElementById('marketFilter').value);
            const tbody = document.querySelector('#rankingsTable tbody');
            tbody.innerHTML = '';
            
            let rank = 1;
            allRows.forEach(row => {{
                const percentile = parseFloat(row.dataset.percentile);
                if (percentile <= filter) {{
                    row.querySelector('.rank').textContent = rank++;
                    tbody.appendChild(row);
                }}
            }});
        }}
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
    
    # Focus on 4-week duration data for consistency
    if 'DURATION' in df.columns:
        print(f"Duration values: {df['DURATION'].unique()}")
        df = df[df['DURATION'] == '4 weeks'].copy()
        print(f"Using 4-week duration data: {len(df)} rows")
    
    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    print(f"Found {len(metros_df['REGION_NAME'].unique())} unique metros")
    
    # Ensure PERIOD_END is datetime
    metros_df['PERIOD_END'] = pd.to_datetime(metros_df['PERIOD_END'])
    
    # Get latest date
    latest_date = metros_df['PERIOD_END'].max()
    date_str = latest_date.strftime('%B %d, %Y')
    
    # Calculate market sizes properly
    print("Calculating market sizes (5-year total homes sold)...")
    market_sizes = {}
    
    for metro in metros_df['REGION_NAME'].unique():
        metro_data = metros_df[metros_df['REGION_NAME'] == metro]
        market_sizes[metro] = calculate_market_size(metro_data)
    
    # Create percentiles (larger markets = lower percentile)
    sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'total_homes'])
    sizes_df = sizes_df.sort_values('total_homes', ascending=False)
    sizes_df['rank'] = range(1, len(sizes_df) + 1)
    sizes_df['percentile'] = sizes_df['rank'] / len(sizes_df) * 100
    
    # Show top 10 markets for verification
    print("\nTop 10 markets by 5-year homes sold:")
    for _, row in sizes_df.head(10).iterrows():
        print(f"  {row['metro'].replace(' metro area', '')}: {row['total_homes']:,.0f} homes (percentile: {row['percentile']:.1f}%)")
    
    # Time periods
    periods = {
        '1month': 1,  # 4 weeks = 1 month
        '3month': 3,
        '6month': 6,
        '1year': 13,  # 52 weeks / 4
        '3year': 39   # 156 weeks / 4
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
    
    print(f"\nGenerated {len(METRICS)} ranking pages")

if __name__ == '__main__':
    main()