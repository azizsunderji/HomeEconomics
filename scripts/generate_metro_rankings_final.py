#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages - FINAL VERSION
- Simple, bulletproof sorting without template literal complexity
- Direct onclick handlers instead of addEventListener
- Clear, debuggable JavaScript
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
        return ''
    
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
    if bg_color in ['#3D3733', '#6B635C']:
        return '#F6F7F3'
    return '#3D3733'

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
        'slug': 'off_market_in_2_weeks',
        'is_calculated': True  # This is a count that needs to be converted to percentage
    },
    'MEDIAN_DAYS_TO_CLOSE': {
        'display': 'Days to Close',
        'format': 'number',
        'slug': 'median_days_to_close'
    },
    'AVERAGE_SALE_TO_LIST_RATIO': {
        'display': 'Sale to List Ratio',
        'format': 'percent',
        'slug': 'sale_to_list_ratio',
        'multiplier': 100  # Data is stored as decimal, needs *100 for percentage
    },
    'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS': {
        'display': 'Price Drops',
        'format': 'percent',
        'slug': 'pct_listings_w__price_drops',
        'multiplier': 100  # Data is stored as decimal (0.10 = 10%), needs *100 for display
    },
    'AGE_OF_INVENTORY': {
        'display': 'Age of Inventory',
        'format': 'number',
        'slug': 'age_of_inventory'
    }
}

def format_value(value, format_type, multiplier=1):
    """Format values for display."""
    if pd.isna(value):
        return '—'
    
    # Apply multiplier if provided
    value = value * multiplier
    
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
    if 'DURATION' in df.columns:
        df = df[df['DURATION'] == '4 weeks'].copy()
    
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

def get_region_for_metro(metro_name):
    """Categorize metro into region based on state."""
    # Extract state from metro name
    if ',' in metro_name:
        state = metro_name.split(',')[1].strip().split()[0]
    else:
        return 'Other'
    
    # Regional mappings
    northeast = ['NY', 'NJ', 'PA', 'CT', 'MA', 'RI', 'VT', 'NH', 'ME']
    southeast = ['FL', 'GA', 'SC', 'NC', 'VA', 'WV', 'KY', 'TN', 'AL', 'MS', 'AR', 'LA']
    midwest = ['OH', 'MI', 'IN', 'IL', 'WI', 'MN', 'IA', 'MO', 'ND', 'SD', 'NE', 'KS']
    southwest = ['TX', 'OK', 'NM', 'AZ']
    west = ['CA', 'OR', 'WA', 'NV', 'ID', 'UT', 'CO', 'WY', 'MT']
    
    if state in northeast:
        return 'Northeast'
    elif state in southeast:
        return 'Southeast'
    elif state in midwest:
        return 'Midwest'
    elif state in southwest:
        return 'Southwest'
    elif state in west:
        return 'West'
    else:
        return 'Other'

def generate_metric_summary(rankings_data, metric_key, metric_info):
    """Generate insightful summary text for a metric."""
    # Group metros by region
    regional_data = {}
    state_data = {}
    
    for metro in rankings_data:
        # Get region
        region = get_region_for_metro(metro['metro_name'])
        if region not in regional_data:
            regional_data[region] = []
        regional_data[region].append(metro)
        
        # Get state
        if ',' in metro['metro_name']:
            state = metro['metro_name'].split(',')[1].strip().split()[0]
            if state not in state_data:
                state_data[state] = []
            state_data[state].append(metro)
    
    # Analyze trends
    summary_parts = []
    
    # Find top/bottom metros by current value
    top_5 = rankings_data[:5]
    bottom_5 = rankings_data[-5:]
    
    # Regional trends analysis
    regional_trends = {}
    for region, metros in regional_data.items():
        if len(metros) >= 3:  # Only analyze regions with enough data
            # Get median changes for different time periods
            changes_1m = [m['changes'].get('1month', 0) for m in metros if m['changes'].get('1month') is not None]
            changes_3m = [m['changes'].get('3month', 0) for m in metros if m['changes'].get('3month') is not None]
            changes_1y = [m['changes'].get('1year', 0) for m in metros if m['changes'].get('1year') is not None]
            
            if changes_3m:
                regional_trends[region] = {
                    'median_3m': np.median(changes_3m),
                    'median_1y': np.median(changes_1y) if changes_1y else None,
                    'count': len(metros)
                }
    
    # State-level notable trends - use median and filter outliers
    state_trends = {}
    for state, metros in state_data.items():
        if len(metros) >= 3:  # Need at least 3 metros for reliable state-level claims
            # Filter out data quality issues
            valid_metros = []
            for m in metros:
                # Skip metros with unrealistic current values (likely data errors)
                if metric_key == 'MEDIAN_SALE_PRICE' and m['current_value'] < 50000:
                    continue  # Skip unrealistic low prices
                # Skip metros with extreme changes (likely data errors)
                if m['changes'].get('3month') and abs(m['changes']['3month']) > 100:
                    continue  # Skip > 100% changes as likely errors
                valid_metros.append(m)
            
            if len(valid_metros) >= 3:  # Still need 3 valid metros
                changes_3m = [m['changes'].get('3month', 0) for m in valid_metros if m['changes'].get('3month') is not None]
                if changes_3m:
                    # Use median instead of mean to be robust against outliers
                    median_change = np.median(changes_3m)
                    if abs(median_change) > 8:  # Higher threshold for "notable"
                        state_trends[state] = median_change
    
    # Build summary text based on metric type
    metric_name = metric_info['display'].lower()
    
    # Opening - describe current levels
    if metric_key == 'MEDIAN_SALE_PRICE':
        top_metros = ', '.join([m['metro_name'].split(',')[0] for m in top_5[:3]])
        summary_parts.append(f"Home prices are currently highest in {top_metros}, with values exceeding ${int(top_5[0]['current_value']/1000)}K.")
    elif metric_key == 'ACTIVE_LISTINGS':
        summary_parts.append(f"Active inventory varies dramatically across markets, with {top_5[0]['metro_name'].split(',')[0]} showing {int(top_5[0]['current_value']):,} active listings.")
    elif metric_key == 'OFF_MARKET_IN_TWO_WEEKS':
        summary_parts.append(f"Market velocity shows {top_5[0]['metro_name'].split(',')[0]} leading with {top_5[0]['current_value']:.1f}% of homes going off market within two weeks.")
    
    # Regional trends
    trending_regions = []
    declining_regions = []
    for region, trend in regional_trends.items():
        if trend['median_3m'] > 3:
            trending_regions.append((region, trend['median_3m']))
        elif trend['median_3m'] < -3:
            declining_regions.append((region, trend['median_3m']))
    
    if trending_regions:
        trending_regions.sort(key=lambda x: x[1], reverse=True)
        region_text = trending_regions[0][0]
        change = trending_regions[0][1]
        summary_parts.append(f"The {region_text} is showing strong growth with a median {change:.1f}% increase over the past 3 months.")
    
    if declining_regions:
        declining_regions.sort(key=lambda x: x[1])
        region_text = declining_regions[0][0]
        change = abs(declining_regions[0][1])
        summary_parts.append(f"The {region_text} has seen declines, with a median {change:.1f}% decrease over 3 months.")
    
    # State-specific notable changes
    if state_trends:
        sorted_states = sorted(state_trends.items(), key=lambda x: abs(x[1]), reverse=True)[:2]
        for state, change in sorted_states:
            state_name = {'FL': 'Florida', 'TX': 'Texas', 'CA': 'California', 'NY': 'New York', 
                         'AZ': 'Arizona', 'NV': 'Nevada', 'CO': 'Colorado', 'WA': 'Washington',
                         'OR': 'Oregon', 'GA': 'Georgia', 'NC': 'North Carolina', 'TN': 'Tennessee'}.get(state, state)
            if change > 0:
                summary_parts.append(f"{state_name} metros are experiencing notable increases, averaging {change:.1f}% growth over 3 months.")
            else:
                summary_parts.append(f"{state_name} metros have cooled, with an average {abs(change):.1f}% decline over 3 months.")
    
    # Time horizon analysis - look for trend reversals
    metros_with_reversal = []
    for metro in rankings_data[:50]:  # Focus on larger markets
        if (metro['changes'].get('1year') and metro['changes'].get('3month') and 
            metro['changes'].get('1year') * metro['changes'].get('3month') < 0):  # Different signs = reversal
            metros_with_reversal.append(metro)
    
    if len(metros_with_reversal) >= 3:
        examples = ', '.join([m['metro_name'].split(',')[0] for m in metros_with_reversal[:3]])
        summary_parts.append(f"Several markets including {examples} have reversed their year-over-year trends in recent months.")
    
    # Notable individual metros
    outliers = []
    for metro in rankings_data:
        if metro['changes'].get('3month'):
            if abs(metro['changes']['3month']) > 15:  # Very large change
                outliers.append((metro['metro_name'], metro['changes']['3month']))
    
    if outliers:
        outliers.sort(key=lambda x: abs(x[1]), reverse=True)
        metro_name = outliers[0][0].split(',')[0]
        change = outliers[0][1]
        if change > 0:
            summary_parts.append(f"{metro_name} stands out with an exceptional {change:.1f}% increase over 3 months.")
        else:
            summary_parts.append(f"{metro_name} has experienced a significant {abs(change):.1f}% decline over 3 months.")
    
    # Join all parts into a paragraph
    if summary_parts:
        return ' '.join(summary_parts)
    else:
        return f"The {metric_name} metric shows varied performance across markets with regional differences becoming more pronounced."

def calculate_market_size(metro_data):
    """Calculate total homes sold over last 5 years."""
    # Use 4-week duration data for consistency
    if 'DURATION' in metro_data.columns:
        metro_data = metro_data[metro_data['DURATION'] == '4 weeks'].copy()
    
    # Ensure PERIOD_END is datetime
    metro_data['PERIOD_END'] = pd.to_datetime(metro_data['PERIOD_END'])
    latest_date = metro_data['PERIOD_END'].max()
    five_years_ago = latest_date - pd.Timedelta(days=365*5)
    recent_data = metro_data[metro_data['PERIOD_END'] > five_years_ago]
    
    if 'ADJUSTED_AVERAGE_HOMES_SOLD' in recent_data.columns and len(recent_data) > 0:
        # Sum all weekly averages over 5 years
        total = recent_data['ADJUSTED_AVERAGE_HOMES_SOLD'].sum() * 4
        return total
    return 0

def generate_html_page(rankings_data, metric_key, metric_info, all_metrics, date_str):
    """Generate HTML page with simple, working sorting."""
    
    # Generate summary text
    summary_text = generate_metric_summary(rankings_data, metric_key, metric_info)
    
    # Build metric navigation buttons
    metric_buttons = []
    for m_key, m_info in all_metrics.items():
        is_active = 'active' if m_key == metric_key else ''
        metric_buttons.append(
            f'<a href="{m_info["slug"]}.html" class="metric-btn {is_active}">'
            f'{m_info["display"]}</a>'
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
            background: white;
            color: #3D3733;
            font-size: 13px;
            line-height: 1.4;
            padding: 0;
            max-width: none;
            margin: 0;
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
        
        .summary-box {{
            background: #F8F9FA;
            border: 1px solid #DADFCE;
            border-radius: 4px;
            margin: 15px 0;
            overflow: hidden;
        }}
        
        .summary-toggle {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 15px;
            cursor: pointer;
            user-select: none;
            background: transparent;
            transition: background 0.2s;
        }}
        
        .summary-toggle:hover {{
            background: #F0F0EC;
        }}
        
        .summary-toggle-text {{
            font-size: 14px;
            color: #6B635C;
            font-weight: 500;
        }}
        
        .summary-arrow {{
            transition: transform 0.3s;
            color: #6B635C;
        }}
        
        .summary-content {{
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out;
            padding: 0 15px;
        }}
        
        .summary-content.expanded {{
            max-height: 500px;
            padding: 0 15px 15px 15px;
            transition: max-height 0.3s ease-in;
        }}
        
        .summary-text {{
            font-size: 14px;
            line-height: 1.6;
            color: #3D3733;
        }}
        
        select {{
            padding: 4px 8px;
            border: 1px solid #DADFCE;
            background: white;
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
            background: white;
            white-space: nowrap;
            z-index: 10;
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
            font-size: 10px;
            margin-left: 2px;
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
            <input type="text" id="searchBox" placeholder="Search metros..." onkeyup="searchTable()" style="padding: 4px 8px; border: 1px solid #DADFCE; background: white; font-family: inherit; font-size: 12px; margin-right: 10px;">
            <label>Show:</label>
            <select id="marketFilter" onchange="filterTable()">
                <option value="10">Large Markets (Top 10%)</option>
                <option value="25" selected>Major Markets (Top 25%)</option>
                <option value="50">Mid-Size Markets (Top 50%)</option>
                <option value="100">All Markets</option>
            </select>
        </div>
    </div>
    
    <div class="summary-box">
        <div class="summary-toggle" onclick="toggleSummary()">
            <span class="summary-toggle-text">Market Analysis Summary</span>
            <span class="summary-arrow" id="summaryArrow">▼</span>
        </div>
        <div class="summary-content" id="summaryContent">
            <p class="summary-text">{summary_text}</p>
        </div>
    </div>
    
    <table id="rankingsTable">
        <thead>
            <tr>
                <th onclick="sortTable('metro')">#</th>
                <th onclick="sortTable('metro')">Metro Area <span class="arrow" id="arrow-metro"></span></th>
                <th class="number" onclick="sortTable('current')">Current <span class="arrow" id="arrow-current"></span></th>
                <th class="number" onclick="sortTable('month1')">1 Mo <span class="arrow" id="arrow-month1"></span></th>
                <th class="number" onclick="sortTable('month3')">3 Mo <span class="arrow" id="arrow-month3"></span></th>
                <th class="number" onclick="sortTable('month6')">6 Mo <span class="arrow" id="arrow-month6"></span></th>
                <th class="number" onclick="sortTable('year1')">1 Yr <span class="arrow" id="arrow-year1"></span></th>
                <th class="number" onclick="sortTable('year3')">3 Yr <span class="arrow" id="arrow-year3"></span></th>
            </tr>
        </thead>
        <tbody>
"""
    
    # Add all data rows with proper data attributes
    for i, row in enumerate(rankings_data, 1):
        # Build row with clean data attributes
        html += f'''            <tr data-percentile="{row['market_percentile']:.1f}" '''
        html += f'data-metro="{row["metro_name"].lower()}" '
        html += f'data-current="{row["current_value"]}" '
        # Use 'null' for missing data, actual number for real values
        month1_val = row["changes"].get("1month")
        html += f'data-month1="{month1_val:.2f}" ' if month1_val is not None else 'data-month1="null" '
        month3_val = row["changes"].get("3month")
        html += f'data-month3="{month3_val:.2f}" ' if month3_val is not None else 'data-month3="null" '
        month6_val = row["changes"].get("6month")
        html += f'data-month6="{month6_val:.2f}" ' if month6_val is not None else 'data-month6="null" '
        year1_val = row["changes"].get("1year")
        html += f'data-year1="{year1_val:.2f}" ' if year1_val is not None else 'data-year1="null" '
        year3_val = row["changes"].get("3year")
        html += f'data-year3="{year3_val:.2f}">\n' if year3_val is not None else 'data-year3="null">\n'
        
        html += f'                <td class="rank">{i}</td>\n'
        html += f'                <td class="metro">{row["metro_name"]}</td>\n'
        
        # Current value with class for targeting
        multiplier = metric_info.get('multiplier', 1)
        current_val = format_value(row['current_value'], metric_info['format'], multiplier)
        html += f'                <td class="number current-value">{current_val}</td>\n'
        
        # Change columns - no initial coloring
        for period_key, period_name in [('1month', 'month1'), ('3month', 'month3'), 
                                        ('6month', 'month6'), ('1year', 'year1'), ('3year', 'year3')]:
            change_val = row['changes'].get(period_key)
            change_text = format_change(change_val)
            
            # No initial styling - colors will be applied by JavaScript when sorted
            html += f'                <td class="number change-{period_name}">{change_text}</td>\n'
        
        html += '            </tr>\n'
    
    html += f"""        </tbody>
    </table>
    
    <div class="footer">
        <strong>Data:</strong> Redfin weekly housing market data | <strong>Updated:</strong> {date_str} | <strong>Methodology:</strong> Based on rolling 4-week windows<br>
        <a href="https://www.home-economics.us">Home Economics</a>
    </div>
    
    <script>
        // Global state
        let allRows = [];
        let currentSort = 'current';
        let sortAscending = false;
        
        // Initialize on page load
        window.onload = function() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            allRows = Array.from(tbody.querySelectorAll('tr'));
            
            // Initial sort by current value
            sortTable('current');
        }};
        
        // Simple, bulletproof sorting function
        function sortTable(column) {{
            console.log('Sorting by:', column);
            
            // Toggle sort direction if same column
            if (currentSort === column) {{
                sortAscending = !sortAscending;
            }} else {{
                sortAscending = false;  // Default to descending
                currentSort = column;
            }}
            
            // Clear all arrows
            document.querySelectorAll('.arrow').forEach(arrow => {{
                arrow.textContent = '';
            }});
            
            // Set current arrow
            const arrow = document.getElementById('arrow-' + column);
            if (arrow) {{
                arrow.textContent = sortAscending ? '↑' : '↓';
            }}
            
            // Update header styles
            document.querySelectorAll('th').forEach(th => {{
                th.classList.remove('sorted');
            }});
            event.target.classList.add('sorted');
            
            // Sort the rows
            allRows.sort((a, b) => {{
                let aVal, bVal;
                
                if (column === 'metro') {{
                    aVal = a.dataset.metro;
                    bVal = b.dataset.metro;
                    return sortAscending ? 
                        aVal.localeCompare(bVal) : 
                        bVal.localeCompare(aVal);
                }} else {{
                    // Get numeric values from data attributes
                    // Handle 'null' string as missing data
                    const aStr = a.dataset[column];
                    const bStr = b.dataset[column];
                    
                    // For descending: valid numbers first, then nulls
                    // For ascending: nulls first, then valid numbers
                    if (aStr === 'null' && bStr === 'null') return 0;
                    if (aStr === 'null') return sortAscending ? -1 : 1;
                    if (bStr === 'null') return sortAscending ? 1 : -1;
                    
                    const aVal = parseFloat(aStr);
                    const bVal = parseFloat(bStr);
                    
                    console.log('Comparing:', aVal, 'vs', bVal);
                    
                    return sortAscending ? aVal - bVal : bVal - aVal;
                }}
            }});
            
            // Clear all coloring from all columns
            document.querySelectorAll('td').forEach(td => {{
                if (td.className.includes('change-') || td.className.includes('number')) {{
                    td.style.backgroundColor = '';
                    td.style.color = '';
                }}
            }});
            
            // Apply coloring to sorted column only
            if (column === 'current') {{
                // Color the current value column using a value-based gradient
                allRows.forEach(row => {{
                    const td = row.querySelector('.current-value');
                    if (td) {{
                        const value = parseFloat(row.dataset.current);
                        if (!isNaN(value)) {{
                            // Create a gradient based on relative position in sorted list
                            const allValues = allRows.map(r => parseFloat(r.dataset.current)).filter(v => !isNaN(v));
                            const max = Math.max(...allValues);
                            const min = Math.min(...allValues);
                            const range = max - min;
                            const percent = range > 0 ? ((value - min) / range) * 100 : 50;
                            
                            // Apply gradient from cream (low) to blue (high)
                            let bgColor;
                            if (percent <= 20) bgColor = '#DADFCE';  // Cream
                            else if (percent <= 40) bgColor = '#E8F4FF';  // Very light blue
                            else if (percent <= 60) bgColor = '#C6E4FF';  // Light blue  
                            else if (percent <= 80) bgColor = '#8CCFFF';  // Medium blue
                            else bgColor = '#0BB4FF';  // Full blue
                            
                            td.style.backgroundColor = bgColor;
                            td.style.color = (bgColor === '#0BB4FF' || bgColor === '#8CCFFF') ? '#F6F7F3' : '#3D3733';
                        }}
                    }}
                }});
            }} else if (column !== 'metro') {{
                // Color the change column that's being sorted
                allRows.forEach(row => {{
                    const value = parseFloat(row.dataset[column]) || 0;
                    if (value !== 0) {{
                        const td = row.querySelector('.change-' + column);
                        if (td) {{
                            const bgColor = getColorForChange(value);
                            if (bgColor) {{
                                td.style.backgroundColor = bgColor;
                                td.style.color = getTextColor(bgColor);
                            }}
                        }}
                    }}
                }});
            }}
            
            // Re-render the table with filtered rows
            filterTable();
        }}
        
        // Get color for change value
        function getColorForChange(val) {{
            if (val <= -5) return '#3D3733';
            if (val <= -2) return '#A09B95';
            if (val <= 0) return '#DADFCE';
            if (val <= 2) return '#C6E4FF';
            if (val <= 5) return '#8CCFFF';
            return '#0BB4FF';
        }}
        
        // Get text color for background
        function getTextColor(bgColor) {{
            return (bgColor === '#3D3733' || bgColor === '#6B635C') ? '#F6F7F3' : '#3D3733';
        }}
        
        // Filter table by market size and search
        function filterTable() {{
            const filter = parseFloat(document.getElementById('marketFilter').value);
            const searchTerm = document.getElementById('searchBox').value.toLowerCase();
            const tbody = document.querySelector('#rankingsTable tbody');
            tbody.innerHTML = '';
            
            let rank = 1;
            allRows.forEach(row => {{
                const percentile = parseFloat(row.dataset.percentile);
                const metroName = row.dataset.metro;
                const matchesSearch = !searchTerm || metroName.includes(searchTerm);
                
                if (percentile <= filter && matchesSearch) {{
                    row.querySelector('.rank').textContent = rank++;
                    tbody.appendChild(row);
                }}
            }});
        }}
        
        // Search function
        function searchTable() {{
            filterTable();  // Reuse filter function which now handles search too
        }}
        
        // Toggle summary box
        function toggleSummary() {{
            const content = document.getElementById('summaryContent');
            const arrow = document.getElementById('summaryArrow');
            
            if (content.classList.contains('expanded')) {{
                content.classList.remove('expanded');
                arrow.style.transform = 'rotate(0deg)';
            }} else {{
                content.classList.add('expanded');
                arrow.style.transform = 'rotate(180deg)';
            }}
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
            
            # Special handling for OFF_MARKET_IN_TWO_WEEKS - calculate as percentage
            if metric_key == 'OFF_MARKET_IN_TWO_WEEKS':
                if pd.isna(latest_data[metric_key]) or pd.isna(latest_data.get('ADJUSTED_AVERAGE_NEW_LISTINGS')):
                    continue
                if latest_data['ADJUSTED_AVERAGE_NEW_LISTINGS'] == 0:
                    continue
                # Calculate as percentage of new listings
                current_value = (latest_data[metric_key] / latest_data['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
            else:
                if pd.isna(latest_data[metric_key]):
                    continue
                current_value = latest_data[metric_key]
            
            # Calculate changes
            if metric_key == 'OFF_MARKET_IN_TWO_WEEKS':
                # For OFF_MARKET_IN_TWO_WEEKS, calculate percentage for each period
                changes = {}
                for period_name, weeks in periods.items():
                    if len(metro_data) > weeks:
                        past_data = metro_data.iloc[-weeks-1]
                        latest = metro_data.iloc[-1]
                        
                        if (pd.notna(past_data[metric_key]) and pd.notna(past_data.get('ADJUSTED_AVERAGE_NEW_LISTINGS')) and
                            pd.notna(latest[metric_key]) and pd.notna(latest.get('ADJUSTED_AVERAGE_NEW_LISTINGS')) and
                            past_data['ADJUSTED_AVERAGE_NEW_LISTINGS'] > 0 and latest['ADJUSTED_AVERAGE_NEW_LISTINGS'] > 0):
                            
                            past_pct = (past_data[metric_key] / past_data['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
                            current_pct = (latest[metric_key] / latest['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
                            changes[period_name] = current_pct - past_pct  # Percentage point change
                        else:
                            changes[period_name] = None
                    else:
                        changes[period_name] = None
            else:
                changes = calculate_changes(metro_data, metric_key, periods)
            
            # Get market percentile
            percentile = sizes_df[sizes_df['metro'] == metro]['percentile'].values
            if len(percentile) > 0:
                market_percentile = percentile[0]
            else:
                market_percentile = 100.0
            
            rankings_data.append({
                'metro_name': metro.replace(' metro area', ''),
                'current_value': current_value,
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