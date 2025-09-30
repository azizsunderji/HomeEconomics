#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages - MOBILE PAID VERSION
- Optimized for mobile devices
- Expandable rows for additional time periods
- Touch-optimized interface
- Only median_sale_price accessible, others locked
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import sys
import json

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
    ,
        'accessible': False},
    'WEEKS_OF_SUPPLY': {
        'display': 'Weeks of Supply',
        'format': 'decimal1',
        'slug': 'weeks_supply'
    ,
        'accessible': False},
    'ADJUSTED_AVERAGE_HOMES_SOLD': {
        'display': 'Homes Sold',
        'format': 'number',
        'slug': 'homes_sold'
    ,
        'accessible': False},
    'ADJUSTED_AVERAGE_NEW_LISTINGS': {
        'display': 'New Listings',
        'format': 'number',
        'slug': 'new_listings'
    ,
        'accessible': False},
    'MEDIAN_DAYS_ON_MARKET': {
        'display': 'Days on Market',
        'format': 'number',
        'slug': 'median_days_on_market'
    ,
        'accessible': False},
    'AVERAGE_PENDING_SALES_LISTING_UPDATES': {
        'display': 'Pending Sales',
        'format': 'number',
        'slug': 'pending_sales'
    ,
        'accessible': False},
    'OFF_MARKET_IN_TWO_WEEKS': {
        'display': 'Off Market in 2 Weeks',
        'format': 'percent',
        'slug': 'off_market_in_2_weeks',
        'is_calculated': True
    ,
        'accessible': False},
    'MEDIAN_DAYS_TO_CLOSE': {
        'display': 'Days to Close',
        'format': 'number',
        'slug': 'median_days_to_close'
    ,
        'accessible': False},
    'AVERAGE_SALE_TO_LIST_RATIO': {
        'display': 'Sale to List Ratio',
        'format': 'percent',
        'slug': 'sale_to_list_ratio',
        'multiplier': 100
    ,
        'accessible': False},
    'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS': {
        'display': 'Price Drops',
        'format': 'percent',
        'slug': 'pct_listings_w__price_drops',
        'multiplier': 100
    ,
        'accessible': False},
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

    if 'DURATION' in df.columns:
        df = df[df['DURATION'] == '4 weeks'].copy()

    df = df.sort_values('PERIOD_END')

    if len(df) == 0:
        return {p: None for p in periods}

    latest_row = df.iloc[-1]
    latest_value = latest_row[metric]
    latest_date = latest_row['PERIOD_END']

    if pd.isna(latest_value):
        return {p: None for p in periods}

    target_dates = {
        '1month': latest_date - timedelta(days=30),
        '3month': latest_date - timedelta(days=90),
        '6month': latest_date - timedelta(days=180),
        '1year': latest_date - timedelta(days=365),
        '3year': latest_date - timedelta(days=365*3)
    }

    for period_name in periods.keys():
        if period_name in target_dates:
            target_date = target_dates[period_name]
            past_data = df[df['PERIOD_END'] <= target_date]

            if len(past_data) > 0:
                past_value = past_data.iloc[-1][metric]
                if pd.notna(past_value) and past_value != 0:
                    change = ((latest_value - past_value) / past_value) * 100
                    changes[period_name] = change
                else:
                    changes[period_name] = None
            else:
                changes[period_name] = None
        else:
            changes[period_name] = None

    return changes

def format_metro_for_url(metro_name):
    """Convert metro name to URL format for charts."""
    clean_name = metro_name.replace(' metro area', '')
    url_name = clean_name.lower().replace(', ', '_').replace(' ', '_')
    url_name = ''.join(c if c.isalnum() or c == '_' else '' for c in url_name)
    return url_name

def calculate_market_size(metro_data):
    """Calculate total homes sold over last 5 years."""
    if 'DURATION' in metro_data.columns:
        metro_data = metro_data[metro_data['DURATION'] == '4 weeks'].copy()

    metro_data['PERIOD_END'] = pd.to_datetime(metro_data['PERIOD_END'])
    latest_date = metro_data['PERIOD_END'].max()
    five_years_ago = latest_date - pd.Timedelta(days=365*5)
    recent_data = metro_data[metro_data['PERIOD_END'] > five_years_ago]

    if 'ADJUSTED_AVERAGE_HOMES_SOLD' in recent_data.columns and len(recent_data) > 0:
        total = recent_data['ADJUSTED_AVERAGE_HOMES_SOLD'].sum() * 4
        return total
    return 0

def generate_mobile_html_page(rankings_data, metric_key, metric_info, all_metrics, date_str):
    """Generate mobile-optimized HTML page."""

    import time
    version = int(time.time())

    # Build metric options for dropdown (free version: lock all except median_sale_price)
    metric_options = []
    for m_key, m_info in all_metrics.items():
        selected = 'selected' if m_key == metric_key else ''
        # For free version, only median_sale_price is unlocked
        if m_key == 'MEDIAN_SALE_PRICE':
            metric_options.append(
                f'<option value="{m_info["slug"]}_mobile_free.html?v={version}" {selected}>{m_info["display"]}</option>'
            )
        else:
            # Locked metrics - use lock emoji
            metric_options.append(
                f'<option value="locked" data-locked="true" {selected}>🔒 {m_info["display"]}</option>'
            )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>{metric_info['display']} | Home Economics Mobile</title>
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
            -webkit-tap-highlight-color: transparent;
        }}

        body {{
            font-family: 'Oracle', -apple-system, system-ui, sans-serif;
            background: white;
            color: #3D3733;
            font-size: 14px;
            line-height: 1.4;
            padding: 0;
            margin: 0;
            overflow-x: hidden;
            -webkit-text-size-adjust: 100%;
        }}

        .fixed-header {{
            position: sticky;
            top: 0;
            background: white;
            z-index: 100;
            padding: 12px;
            border-bottom: 1px solid #DADFCE;
        }}

        h1 {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 12px;
            color: #3D3733;
        }}

        .controls {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}

        .metric-dropdown {{
            width: 100%;
            padding: 12px;
            font-size: 14px;
            border: 1px solid #DADFCE;
            background: white;
            border-radius: 6px;
            font-family: inherit;
            color: #3D3733;
            appearance: none;
            background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
            background-repeat: no-repeat;
            background-position: right 10px center;
            background-size: 20px;
        }}

        #searchBox {{
            width: 100%;
            padding: 12px;
            font-size: 14px;
            border: 1px solid #DADFCE;
            border-radius: 6px;
            font-family: inherit;
        }}

        #marketFilter, #timePeriod {{
            width: 100%;
            padding: 12px;
            font-size: 14px;
            border: 1px solid #DADFCE;
            background: white;
            border-radius: 6px;
            font-family: inherit;
            color: #3D3733;
            appearance: none;
            background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
            background-repeat: no-repeat;
            background-position: right 10px center;
            background-size: 20px;
        }}

        .table-container {{
            overflow-x: auto;
            padding: 0;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        thead {{
            position: sticky;
            top: 0;
            background: white;
            z-index: 10;
        }}

        th {{
            text-align: left;
            padding: 10px 8px;
            border-bottom: 2px solid #0BB4FF;
            font-weight: 600;
            font-size: 12px;
            color: #6B635C;
            background: white;
            white-space: nowrap;
        }}

        th.number {{
            text-align: right;
        }}

        td {{
            padding: 12px 8px;
            border-bottom: 1px solid #F0F0EC;
            font-size: 13px;
        }}

        td.number {{
            text-align: right;
            font-variant-numeric: tabular-nums;
        }}

        td.rank {{
            color: #6B635C;
            font-weight: 600;
            width: 30px;
            text-align: center;
        }}

        td.metro {{
            font-weight: 500;
            color: #3D3733;
            min-width: 120px;
        }}

        tr {{
            cursor: pointer;
            transition: background 0.2s;
        }}

        tr:active {{
            background: rgba(11, 180, 255, 0.1);
        }}

        tr.expanded {{
            background: #F8F9FA;
        }}

        .expansion-row {{
            display: none;
        }}

        .expansion-row.visible {{
            display: table-row;
        }}

        .expansion-row td {{
            padding: 16px;
            background: #F8F9FA;
            border-bottom: 2px solid #DADFCE;
        }}

        .timeline-container {{
            padding: 20px 15px;
            background: #F0F2F5;
            border-top: 2px solid #DADFCE;
            border-bottom: 2px solid #DADFCE;
        }}

        .timeline {{
            position: relative;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 20px 0 25px 0;
            padding: 0 5px;
        }}

        .timeline::before {{
            content: '';
            position: absolute;
            left: 5px;
            right: 5px;
            top: 50%;
            height: 2px;
            background: #D0D0D0;
            z-index: 0;
        }}

        .timeline-item {{
            position: relative;
            text-align: center;
            flex: 1;
            z-index: 1;
        }}

        .timeline-dot {{
            width: 52px;
            height: 52px;
            border-radius: 50%;
            margin: 0 auto 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 11px;
            color: white;
            box-shadow: 0 3px 6px rgba(0,0,0,0.15);
            border: 2px solid white;
        }}

        .timeline-label {{
            font-size: 11px;
            color: #6B635C;
            font-weight: 600;
            white-space: nowrap;
        }}

        .chart-button {{
            width: 100%;
            margin-top: 12px;
            padding: 12px;
            background: #0BB4FF;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
        }}

        .chart-button:active {{
            background: #0995D6;
        }}

        .footer {{
            text-align: center;
            padding: 20px 12px;
            font-size: 11px;
            color: #6B635C;
            border-top: 1px solid #DADFCE;
            background: white;
        }}

        .footer a {{
            color: #0BB4FF;
            text-decoration: none;
        }}

        /* Swipe hint */
        .swipe-hint {{
            display: none;
            text-align: center;
            padding: 8px;
            font-size: 12px;
            color: #6B635C;
            background: #F8F9FA;
            margin: 12px;
            border-radius: 6px;
        }}

        /* Loading state */
        .loading {{
            opacity: 0.5;
            pointer-events: none;
        }}

        /* Upgrade Modal Styles */
        .modal {{
            display: none;
            position: fixed;
            z-index: 10000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.4);
        }}

        .modal-content {{
            background-color: #fefefe;
            margin: 15% auto;
            padding: 20px;
            border: 1px solid #888;
            width: 80%;
            max-width: 400px;
            border-radius: 8px;
        }}

        .modal-content ul {{
            padding-left: 30px;
            margin: 15px 0;
        }}

        .modal-buttons {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-top: 20px;
        }}

        .close {{
            color: #aaa;
            float: right;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
        }}

        .close:hover {{
            color: #000;
        }}

        .upgrade-button {{
            display: block;
            width: 100%;
            padding: 12px;
            background: #0BB4FF;
            color: white;
            text-align: center;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            border: none;
            font-family: inherit;
            font-size: 14px;
            cursor: pointer;
        }}

        .login-button {{
            display: block;
            width: 100%;
            padding: 12px;
            background: white;
            color: #0BB4FF;
            text-align: center;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            border: 2px solid #0BB4FF;
            font-family: inherit;
            font-size: 14px;
            cursor: pointer;
        }}

        .filter-label {{
            font-size: 11px;
            font-weight: 600;
            color: #6B635C;
            margin-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
    </style>
</head>
<body>
    <div class="fixed-header">
        <h1>{metric_info['display'].upper()}</h1>

        <div class="controls">
            <select class="metric-dropdown" id="metricDropdown" onchange="handleMetricChange(this)">
                {''.join(metric_options)}
            </select>

            <input type="text" id="searchBox" placeholder="Search metros..." onkeyup="searchTable()">

            <div style="display: flex; gap: 10px;">
                <div style="flex: 1;">
                    <div class="filter-label">Market</div>
                    <select id="marketFilter" onchange="filterTable()" style="width: 100%;">
                        <option value="10">Top 10%</option>
                        <option value="25">Top 25%</option>
                        <option value="50" selected>Top 50%</option>
                        <option value="100">All Markets</option>
                    </select>
                </div>

                <div style="flex: 1;">
                    <div class="filter-label">Period</div>
                    <select id="timePeriod" onchange="updateTimePeriod()" style="width: 100%;">
                        <option value="1month">1 Month</option>
                        <option value="3month">3 Months</option>
                        <option value="6month">6 Months</option>
                        <option value="1year" selected>1 Year</option>
                        <option value="3year">3 Years</option>
                    </select>
                </div>
            </div>
        </div>
    </div>

    <div class="table-container">
        <table id="rankingsTable">
        <thead>
            <tr>
                <th onclick="sortTable('rank')">#</th>
                <th onclick="sortTable('metro')">Metro</th>
                <th class="number" onclick="sortTable('current')">Current ↓</th>
                <th class="number" id="changeHeader" onclick="sortTable('change')">1 Yr</th>
            </tr>
        </thead>
        <tbody>
"""

    # Add data rows with expandable details
    for i, row in enumerate(rankings_data, 1):
        # Main row with all time period data
        html += f'''            <tr data-percentile="{row['market_percentile']:.1f}" '''
        html += f'data-metro="{row["metro_name"].lower()}" '
        html += f'data-current="{row["current_value"]}" '
        html += f'data-1month="{row["changes"].get("1month", 0)}" '
        html += f'data-3month="{row["changes"].get("3month", 0)}" '
        html += f'data-6month="{row["changes"].get("6month", 0)}" '
        html += f'data-1year="{row["changes"].get("1year", 0)}" '
        html += f'data-3year="{row["changes"].get("3year", 0)}" '
        html += f'data-rank="{i}" style="cursor: pointer;">\n'

        html += f'                <td class="rank">{i}</td>\n'
        html += f'                <td class="metro">{row["metro_name"]}</td>\n'

        multiplier = metric_info.get('multiplier', 1)
        current_val = format_value(row['current_value'], metric_info['format'], multiplier)
        html += f'                <td class="number">{current_val}</td>\n'

        year1_val = row['changes'].get('1year')
        year1_text = format_change(year1_val)

        html += f'                <td class="number">{year1_text}</td>\n'
        html += '            </tr>\n'

        # Expansion row with timeline visualization
        metro_url = format_metro_for_url(row["metro_name"])
        html += f'''            <tr class="expansion-row" id="expand-{i}">
                <td colspan="4">
                    <div class="timeline-container">
                        <div class="timeline">
'''

        # Add all time periods as timeline items
        periods = [('1month', '1M'), ('3month', '3M'),
                  ('6month', '6M'), ('1year', '1Y'), ('3year', '3Y')]

        for period_key, period_label in periods:
            change_val = row['changes'].get(period_key)
            change_text = format_change(change_val)
            change_color = get_color_for_change(change_val)

            # Use the color directly for the dot background
            dot_bg = change_color if change_color else '#E0E0E0'

            html += f'''                            <div class="timeline-item">
                                <div class="timeline-dot" style="background-color: {dot_bg};">
                                    {change_text}
                                </div>
                                <div class="timeline-label">{period_label}</div>
                            </div>
'''

        html += f'''                        </div>
                        <button class="chart-button" onclick="openChart('{metro_url}', '{metric_info["slug"]}')">
                            View Historical Chart
                        </button>
                    </div>
                </td>
            </tr>
'''

    html += f"""        </tbody>
    </table>
    </div>

    <div class="footer">
        <strong>Data:</strong> Redfin | <strong>Updated:</strong> {date_str}<br>
        <a href="https://www.home-economics.us">Home Economics</a>
    </div>

    <script>
        let allRows = [];
        let allExpansionRows = [];
        let expandedRow = null;
        let currentSort = 'current';
        let sortAscending = false;
        let currentTimePeriod = '1year';

        // Use document-level event delegation that will ALWAYS work
        document.addEventListener('click', function(e) {{
            // Check if click is on a table row
            const clickedElement = e.target;
            const clickedRow = clickedElement.closest('tr');

            // Make sure it's from our rankings table and not an expansion row
            if (clickedRow &&
                clickedRow.closest('#rankingsTable') &&
                !clickedRow.classList.contains('expansion-row') &&
                clickedRow.dataset.rank) {{
                toggleRow(clickedRow);
            }}
        }});

        
        function handleMetricChange(select) {{
            if (select.value.includes('_mobile_free')) {{
                window.location.href = select.value;
            }} else {{
                // Show upgrade modal for locked metrics
                document.getElementById('upgradeModal').style.display = 'block';
                // Reset selection to current metric
                select.value = '{metric_info["slug"]}_mobile_free.html?v={version}';
            }}
        }}

        function closeUpgradeModal() {{
            document.getElementById('upgradeModal').style.display = 'none';
        }}

        window.onload = function() {{
            const tbody = document.querySelector('#rankingsTable tbody');

            // Store all rows (main and expansion) separately
            const allTrs = Array.from(tbody.querySelectorAll('tr'));

            allTrs.forEach(tr => {{
                if (tr.classList.contains('expansion-row')) {{
                    allExpansionRows.push(tr);
                }} else {{
                    allRows.push(tr);
                }}
            }});

            // Data is already sorted by current value descending from Python
            // Just set the initial state and update UI
            currentSort = 'current';
            sortAscending = false;

            // Update the Current column header to show it's sorted
            const headers = document.querySelectorAll('th');
            headers[2].textContent = 'Current ↓';

            // Apply initial filter (Top 50% is the default)
            filterTable();

            // Apply heat mapping to current column since it's the initial sort
            applyCurrentColumnColors();
        }};

        function toggleRow(row) {{
            const rank = row.dataset.rank;
            const expansionRow = document.getElementById('expand-' + rank);

            if (expandedRow && expandedRow !== expansionRow) {{
                expandedRow.classList.remove('visible');
                expandedRow.previousElementSibling.classList.remove('expanded');
            }}

            if (expansionRow.classList.contains('visible')) {{
                expansionRow.classList.remove('visible');
                row.classList.remove('expanded');
                expandedRow = null;
            }} else {{
                expansionRow.classList.add('visible');
                row.classList.add('expanded');
                expandedRow = expansionRow;
            }}
        }}

        function updateTimePeriod() {{
            currentTimePeriod = document.getElementById('timePeriod').value;
            const header = document.getElementById('changeHeader');

            // Update header text
            const periodLabels = {{
                '1month': '1 Mo',
                '3month': '3 Mo',
                '6month': '6 Mo',
                '1year': '1 Yr',
                '3year': '3 Yr'
            }};
            header.textContent = periodLabels[currentTimePeriod];

            // Update the change column values to show the new period
            updateChangeColumn();

            // Re-sort by the new time period to show best/worst performers
            currentSort = 'change';
            sortAscending = false;
            sortTable('change');
        }}

        function applyCurrentColumnColors() {{
            // Apply heat mapping to current column using same logic as desktop
            const tbody = document.querySelector('#rankingsTable tbody');
            const rows = tbody.querySelectorAll('tr:not(.expansion-row)');

            // Get all current values for range calculation
            const allValues = [];
            rows.forEach(row => {{
                const value = parseFloat(row.dataset.current);
                if (!isNaN(value)) {{
                    allValues.push(value);
                }}
            }});

            const max = Math.max(...allValues);
            const min = Math.min(...allValues);
            const range = max - min;

            // Apply colors based on value position in range
            rows.forEach(row => {{
                const currentTd = row.cells[2];
                const value = parseFloat(row.dataset.current);

                if (!isNaN(value)) {{
                    const percent = range > 0 ? ((value - min) / range) * 100 : 50;

                    // Use same gradient as desktop version
                    let bgColor;
                    if (percent <= 20) bgColor = '#DADFCE';  // Cream
                    else if (percent <= 40) bgColor = '#E8F4FF';  // Very light blue
                    else if (percent <= 60) bgColor = '#C6E4FF';  // Light blue
                    else if (percent <= 80) bgColor = '#8CCFFF';  // Medium blue
                    else bgColor = '#0BB4FF';  // Full blue

                    currentTd.style.backgroundColor = bgColor;
                    currentTd.style.color = (bgColor === '#0BB4FF' || bgColor === '#8CCFFF') ? '#F6F7F3' : '#3D3733';
                }}

                // Clear change column colors
                const changeTd = row.cells[3];
                changeTd.style.backgroundColor = '';
                changeTd.style.color = '#3D3733';
            }});
        }}

        function updateChangeColumn() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            const rows = tbody.querySelectorAll('tr:not(.expansion-row)');

            // Clear current column colors
            rows.forEach(row => {{
                const currentTd = row.cells[2];
                currentTd.style.backgroundColor = '';
                currentTd.style.color = '#3D3733';
            }});

            // Apply change column colors
            rows.forEach(row => {{
                const changeValue = parseFloat(row.dataset[currentTimePeriod]) || 0;
                const changeTd = row.cells[3];
                const changeText = formatChange(changeValue);
                const changeColor = getColorForChange(changeValue);
                const textColor = getTextColor(changeColor);

                changeTd.textContent = changeText;
                changeTd.style.backgroundColor = changeColor || '';
                changeTd.style.color = changeColor ? textColor : '#3D3733';
            }});
        }}

        function formatChange(value) {{
            if (!value || value === 0) return '—';
            const sign = value >= 0 ? '+' : '';
            return sign + value.toFixed(1) + '%';
        }}

        function getColorForChange(val) {{
            if (!val || val === 0) return '';
            if (val <= -5) return '#3D3733';
            if (val <= -2) return '#A09B95';
            if (val <= 0) return '#DADFCE';
            if (val <= 2) return '#C6E4FF';
            if (val <= 5) return '#8CCFFF';
            return '#0BB4FF';
        }}

        function getTextColor(bgColor) {{
            return (bgColor === '#3D3733' || bgColor === '#A09B95') ? '#F6F7F3' : '#3D3733';
        }}

        function sortTable(column) {{
            if (currentSort === column) {{
                sortAscending = !sortAscending;
            }} else {{
                currentSort = column;
                sortAscending = false;
            }}

            // Update header arrows
            document.querySelectorAll('th').forEach(th => {{
                th.textContent = th.textContent.replace(' ↑', '').replace(' ↓', '');
            }});

            const headerIndex = column === 'rank' ? 0 : column === 'metro' ? 1 : column === 'current' ? 2 : 3;
            const headers = document.querySelectorAll('th');
            if (headers[headerIndex]) {{
                headers[headerIndex].textContent += sortAscending ? ' ↑' : ' ↓';
            }}

            // Sort rows
            allRows.sort((a, b) => {{
                let aVal, bVal;

                if (column === 'rank') {{
                    aVal = parseInt(a.querySelector('.rank').textContent);
                    bVal = parseInt(b.querySelector('.rank').textContent);
                }} else if (column === 'metro') {{
                    aVal = a.dataset.metro;
                    bVal = b.dataset.metro;
                    return sortAscending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }} else if (column === 'current') {{
                    aVal = parseFloat(a.dataset.current) || 0;
                    bVal = parseFloat(b.dataset.current) || 0;
                }} else {{ // change column
                    aVal = parseFloat(a.dataset[currentTimePeriod]) || 0;
                    bVal = parseFloat(b.dataset[currentTimePeriod]) || 0;
                }}

                if (sortAscending) {{
                    return aVal - bVal;
                }} else {{
                    return bVal - aVal;
                }}
            }});

            filterTable();

            // Apply appropriate coloring based on current sort
            if (currentSort === 'current') {{
                applyCurrentColumnColors();
            }} else if (currentSort === 'change') {{
                updateChangeColumn();
            }}
        }}

        function searchTable() {{
            filterTable();
        }}

        function filterTable() {{
            const filter = parseFloat(document.getElementById('marketFilter').value);
            const searchTerm = document.getElementById('searchBox').value.toLowerCase();
            const tbody = document.querySelector('#rankingsTable tbody');

            // Reset expandedRow since we're rebuilding the table
            expandedRow = null;

            tbody.innerHTML = '';

            let rank = 1;
            allRows.forEach((row, index) => {{
                const percentile = parseFloat(row.dataset.percentile);
                const metroName = row.dataset.metro;
                const matchesSearch = !searchTerm || metroName.includes(searchTerm);

                if (percentile <= filter && matchesSearch) {{
                    // Clone and update the main row
                    const newRow = row.cloneNode(true);
                    newRow.querySelector('.rank').textContent = rank;
                    newRow.dataset.rank = rank;

                    // Just add cursor style - click handler is managed by event delegation
                    newRow.style.cursor = 'pointer';

                    // Clone and update the expansion row
                    const expansionRow = allExpansionRows[index];
                    const newExpansionRow = expansionRow.cloneNode(true);
                    newExpansionRow.id = 'expand-' + rank;

                    tbody.appendChild(newRow);
                    tbody.appendChild(newExpansionRow);
                    rank++;
                }}
            }});

            // Apply appropriate coloring based on current sort
            if (currentSort === 'current') {{
                applyCurrentColumnColors();
            }} else if (currentSort === 'change') {{
                updateChangeColumn();
            }}

            // If no results
            if (rank === 1) {{
                const emptyRow = document.createElement('tr');
                emptyRow.innerHTML = '<td colspan="4" style="text-align: center; padding: 20px;">No metros found matching your criteria</td>';
                tbody.appendChild(emptyRow);
            }}
        }}

        function openChart(metroUrl, metricSlug) {{
            // Free version: only allow charts for median_sale_price
            if (metricSlug === 'median_sale_price') {{
                const chartUrl = 'https://home-economics.us/wp-content/uploads/reports/live/mobile/' +
                               metroUrl + '/' + metroUrl + '_' + metricSlug + '_mobile.png';
                window.open(chartUrl, '_blank');
            }} else {{
                // Locked metric - show upgrade modal
                showUpgradeModal();
            }}
        }}
    </script>

    <!-- Upgrade Modal -->
    <div id="upgradeModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeUpgradeModal()">&times;</span>
            <h2>Upgrade to Premium</h2>
            <p>Access all 12 real estate metrics including:</p>
            <ul>
                <li>Active Listings</li>
                <li>Weeks of Supply</li>
                <li>Days on Market</li>
                <li>And much more!</li>
            </ul>
            <div class="modal-buttons">
                <a href="https://www.home-economics.us/subscribe" class="upgrade-button">Upgrade Now</a>
                <a href="https://www.home-economics.us/login" class="login-button">Already a Member? Log In</a>
            </div>
        </div>
    </div>

</body>
</html>"""

    return html

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-path', default='data/weekly_housing_market_data.parquet')
    parser.add_argument('--output-dir', default='rankings')
    args = parser.parse_args()

    print("Loading Redfin data for mobile free version...")
    df = pd.read_parquet(args.data_path)

    if 'DURATION' in df.columns:
        df = df[df['DURATION'] == '4 weeks'].copy()
        print(f"Using 4-week duration data: {len(df)} rows")

    metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
    print(f"Found {len(metros_df['REGION_NAME'].unique())} unique metros")

    metros_df['PERIOD_END'] = pd.to_datetime(metros_df['PERIOD_END'])
    latest_date = metros_df['PERIOD_END'].max()
    date_str = latest_date.strftime('%B %d, %Y')

    # Calculate market sizes
    print("Calculating market sizes...")
    market_sizes = {}
    for metro in metros_df['REGION_NAME'].unique():
        metro_data = metros_df[metros_df['REGION_NAME'] == metro]
        market_sizes[metro] = calculate_market_size(metro_data)

    sizes_df = pd.DataFrame(list(market_sizes.items()), columns=['metro', 'total_homes'])
    sizes_df = sizes_df.sort_values('total_homes', ascending=False)
    sizes_df['rank'] = range(1, len(sizes_df) + 1)
    sizes_df['percentile'] = sizes_df['rank'] / len(sizes_df) * 100

    # Time periods
    periods = {
        '1month': 1,
        '3month': 3,
        '6month': 6,
        '1year': 13,
        '3year': 39
    }

    output_path = Path(args.output_dir)
    output_path.mkdir(exist_ok=True)

    # Process each metric
    # Free version only processes median_sale_price
    for metric_key, metric_info in METRICS.items():
        if metric_key != 'MEDIAN_SALE_PRICE':
            continue
        if metric_key not in metros_df.columns:
            print(f"Skipping {metric_key} - not in data")
            continue

        print(f"Processing {metric_info['display']} for mobile free version...")

        rankings_data = []
        for metro in metros_df['REGION_NAME'].unique():
            metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')

            if len(metro_data) == 0:
                continue

            latest_data = metro_data.iloc[-1]

            # Handle OFF_MARKET_IN_TWO_WEEKS special case
            if metric_key == 'OFF_MARKET_IN_TWO_WEEKS':
                if pd.isna(latest_data[metric_key]) or pd.isna(latest_data.get('ADJUSTED_AVERAGE_NEW_LISTINGS')):
                    continue
                if latest_data['ADJUSTED_AVERAGE_NEW_LISTINGS'] == 0:
                    continue
                current_value = (latest_data[metric_key] / latest_data['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
            else:
                if pd.isna(latest_data[metric_key]):
                    continue
                current_value = latest_data[metric_key]

            # Calculate changes
            if metric_key == 'OFF_MARKET_IN_TWO_WEEKS':
                changes = {}
                latest = metro_data.iloc[-1]
                latest_date = latest['PERIOD_END']

                target_dates = {
                    '1month': latest_date - timedelta(days=30),
                    '3month': latest_date - timedelta(days=90),
                    '6month': latest_date - timedelta(days=180),
                    '1year': latest_date - timedelta(days=365),
                    '3year': latest_date - timedelta(days=365*3)
                }

                for period_name in periods.keys():
                    if period_name in target_dates:
                        target_date = target_dates[period_name]
                        past_data_df = metro_data[metro_data['PERIOD_END'] <= target_date]

                        if len(past_data_df) > 0:
                            past_data = past_data_df.iloc[-1]

                            if (pd.notna(past_data[metric_key]) and pd.notna(past_data.get('ADJUSTED_AVERAGE_NEW_LISTINGS')) and
                                pd.notna(latest[metric_key]) and pd.notna(latest.get('ADJUSTED_AVERAGE_NEW_LISTINGS')) and
                                past_data['ADJUSTED_AVERAGE_NEW_LISTINGS'] > 0 and latest['ADJUSTED_AVERAGE_NEW_LISTINGS'] > 0):

                                past_pct = (past_data[metric_key] / past_data['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
                                current_pct = (latest[metric_key] / latest['ADJUSTED_AVERAGE_NEW_LISTINGS']) * 100
                                changes[period_name] = current_pct - past_pct
                            else:
                                changes[period_name] = None
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

        # Sort by current value
        rankings_data.sort(key=lambda x: x['current_value'], reverse=True)

        # Generate and save mobile HTML
        html = generate_mobile_html_page(rankings_data, metric_key, metric_info, METRICS, date_str)
        output_file = output_path / f"{metric_info['slug']}_mobile_free.html"
        with open(output_file, 'w') as f:
            f.write(html)
        print(f"  Saved {output_file}")

    # Create mobile index redirect
    import time
    version = int(time.time())
    with open(output_path / 'index_mobile_free.html', 'w') as f:
        f.write(f'''<!DOCTYPE html>
<html><head>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta http-equiv="refresh" content="0; url=median_sale_price_mobile_free.html?v={version}">
</head>
<body>Redirecting...
    <!-- Upgrade Modal -->
    <div id="upgradeModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeUpgradeModal()">&times;</span>
            <h2>Upgrade to Premium</h2>
            <p>Access all 12 real estate metrics including:</p>
            <ul>
                <li>Active Listings</li>
                <li>Weeks of Supply</li>
                <li>Days on Market</li>
                <li>And much more!</li>
            </ul>
            <div class="modal-buttons">
                <a href="https://www.home-economics.us/subscribe" class="upgrade-button">Upgrade Now</a>
                <a href="https://www.home-economics.us/login" class="login-button">Already a Member? Log In</a>
            </div>
        </div>
    </div>

</body></html>''')

    print(f"\nGenerated mobile free version with median_sale_price only")
    print("Other metrics are locked with upgrade prompts")

if __name__ == '__main__':
    main()