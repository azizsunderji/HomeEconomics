#!/usr/bin/env python3
"""
Generate weekly metro rankings HTML pages - FREE VERSION
- Only median_sale_price is accessible
- Other metrics are locked with upgrade prompts
- All functionality remains for median_sale_price
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
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
        'slug': 'median_sale_price',
        'accessible': True  # Free version can access this
    },
    'ACTIVE_LISTINGS': {
        'display': 'Active Listings',
        'format': 'number',
        'slug': 'active_listings',
        'accessible': False  # Locked in free version
    },
    'WEEKS_OF_SUPPLY': {
        'display': 'Weeks of Supply',
        'format': 'decimal1',
        'slug': 'weeks_supply',
        'accessible': False
    },
    'ADJUSTED_AVERAGE_HOMES_SOLD': {
        'display': 'Homes Sold',
        'format': 'number',
        'slug': 'homes_sold',
        'accessible': False
    },
    'ADJUSTED_AVERAGE_NEW_LISTINGS': {
        'display': 'New Listings',
        'format': 'number',
        'slug': 'new_listings',
        'accessible': False
    },
    'MEDIAN_DAYS_ON_MARKET': {
        'display': 'Days on Market',
        'format': 'number',
        'slug': 'median_days_on_market',
        'accessible': False
    },
    'AVERAGE_PENDING_SALES_LISTING_UPDATES': {
        'display': 'Pending Sales',
        'format': 'number',
        'slug': 'pending_sales',
        'accessible': False
    },
    'OFF_MARKET_IN_TWO_WEEKS': {
        'display': 'Off Market in 2 Weeks',
        'format': 'percent',
        'slug': 'off_market_in_2_weeks',
        'is_calculated': True,
        'accessible': False
    },
    'MEDIAN_DAYS_TO_CLOSE': {
        'display': 'Days to Close',
        'format': 'number',
        'slug': 'median_days_to_close',
        'accessible': False
    },
    'AVERAGE_SALE_TO_LIST_RATIO': {
        'display': 'Sale to List Ratio',
        'format': 'percent',
        'slug': 'sale_to_list_ratio',
        'multiplier': 100,
        'accessible': False
    },
    'PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS': {
        'display': 'Price Drops',
        'format': 'percent',
        'slug': 'pct_listings_w__price_drops',
        'multiplier': 100,
        'accessible': False
    },
    'AGE_OF_INVENTORY': {
        'display': 'Age of Inventory',
        'format': 'number',
        'slug': 'age_of_inventory',
        'accessible': False
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
    """Calculate percentage changes for different time periods using date-based approach."""
    from datetime import timedelta

    changes = {}

    # Use only 4-week duration data for consistency
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

    # Define target dates for each period (matching MobileCharts approach)
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
            # Find the closest data point at or before the target date
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
    # Remove "metro area" suffix if present
    clean_name = metro_name.replace(' metro area', '')
    # Convert to lowercase and replace spaces/commas with underscores
    url_name = clean_name.lower().replace(', ', '_').replace(' ', '_')
    # Remove any remaining special characters
    url_name = ''.join(c if c.isalnum() or c == '_' else '' for c in url_name)
    return url_name

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

def generate_metric_summary(rankings_data, metric_key, metric_info, segment_name="All Markets", sizes_df=None):
    """Generate insightful summary text for a metric."""
    # Find national-level data (All Redfin Metros)
    national_data = None
    for metro in rankings_data:
        if metro['metro_name'] == 'All Redfin Metros':
            national_data = metro
            break

    # Group metros by region
    regional_data = {}
    state_data = {}

    for metro in rankings_data:
        if metro['metro_name'] == 'All Redfin Metros':
            continue  # Skip national aggregate

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
            changes_1m = [m['changes'].get('1month') for m in metros if m['changes'].get('1month') is not None]
            changes_3m = [m['changes'].get('3month') for m in metros if m['changes'].get('3month') is not None]
            changes_1y = [m['changes'].get('1year') for m in metros if m['changes'].get('1year') is not None]

            if changes_3m:
                median_val = np.median(changes_3m)
                regional_trends[region] = {
                    'median_3m': median_val,
                    'median_1y': np.median(changes_1y) if changes_1y else None,
                    'count': len(metros)
                }

    # Build summary text based on metric type
    metric_name = metric_info['display'].lower()

    # Add national context if available
    if national_data and metric_key == 'MEDIAN_SALE_PRICE':
        price = national_data['current_value']
        if price >= 1000000:
            price_str = f"${price/1000000:.1f} million"
        else:
            price_str = f"${int(price/1000)}K"
        summary_parts.append(f"Nationally, the median sale price stands at {price_str}.")

    # Opening - describe current levels for median sale price
    if metric_key == 'MEDIAN_SALE_PRICE':
        # Add state to metro names and format prices better
        top_metros_with_state = []
        for m in top_5[:3]:
            name_parts = m['metro_name'].split(',')
            if len(name_parts) > 1:
                city = name_parts[0]
                state = name_parts[1].strip()
                top_metros_with_state.append(f"{city}, {state}")
            else:
                top_metros_with_state.append(m['metro_name'])

        top_metros = ', '.join(top_metros_with_state)

        # Format price as millions
        price = top_5[0]['current_value']
        if price >= 1000000:
            price_str = f"${price/1000000:.1f} million"
        else:
            price_str = f"${int(price/1000)}K"

        # Add segment context
        segment_context = f" among {segment_name.lower()}" if segment_name != "All Markets" else ""
        summary_parts.append(f"Home prices{segment_context} are currently highest in {top_metros}, with values exceeding {price_str}.")

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
        # Find first non-Other region
        for region_text, change in trending_regions:
            if region_text != 'Other':
                summary_parts.append(f"The {region_text} is showing strong growth with a median {change:.1f}% increase over the past 3 months.")
                break

    if declining_regions:
        declining_regions.sort(key=lambda x: x[1])
        # Find first non-Other region
        for region_text, change_val in declining_regions:
            if region_text != 'Other':
                change = abs(change_val)
                summary_parts.append(f"The {region_text} has seen declines, with a median {change:.1f}% decrease over 3 months.")
                break

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

def generate_html_page_free(rankings_data, metric_key, metric_info, all_metrics, date_str, sizes_df=None):
    """Generate HTML page for free version with locked metrics."""

    # Generate summaries for different market segments (only for median_sale_price)
    summaries = {}
    if metric_key == 'MEDIAN_SALE_PRICE':
        segments = [
            ('10', 'Large Markets (Top 10%)'),
            ('25', 'Major Markets (Top 25%)'),
            ('50', 'Mid-Size Markets (Top 50%)'),
            ('100', 'All Markets')
        ]

        for percentile, name in segments:
            if percentile == '100':
                segment_data = rankings_data
            else:
                segment_data = [r for r in rankings_data if r['market_percentile'] <= float(percentile)]

            if segment_data:
                summaries[percentile] = generate_metric_summary(segment_data, metric_key, metric_info, name, sizes_df)
            else:
                summaries[percentile] = f"No data available for {name}."

    # Generate version string based on current timestamp for cache-busting
    import time
    version = int(time.time())

    # Build metric navigation buttons with locked state for non-accessible metrics
    metric_buttons = []
    metric_options = []
    for m_key, m_info in all_metrics.items():
        is_active = 'active' if m_key == metric_key else ''
        is_locked = '' if m_info.get('accessible', False) else 'locked'

        if m_info.get('accessible', False):
            # Accessible metric - normal link
            metric_buttons.append(
                f'<a href="{m_info["slug"]}_free.html?v={version}" class="metric-btn {is_active}">'
                f'{m_info["display"]}</a>'
            )
        else:
            # Locked metric - not a link, just a button with subtle lock icon
            metric_buttons.append(
                f'<button class="metric-btn {is_locked}" onclick="showUpgradePrompt()">'
                f'<span class="lock-icon">🔒</span> {m_info["display"]}'
                f'</button>'
            )

        # Mobile dropdown options
        selected = 'selected' if m_key == metric_key else ''
        disabled = '' if m_info.get('accessible', False) else 'disabled'
        lock_text = '' if m_info.get('accessible', False) else ' 🔒'
        metric_options.append(
            f'<option value="{m_info["slug"]}_free.html?v={version}" {selected} {disabled}>{m_info["display"]}{lock_text}</option>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>{metric_info['display']} Rankings | Home Economics (Free Version)</title>
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
            height: 100vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            scrollbar-width: none;
            -ms-overflow-style: none;
        }}

        body::-webkit-scrollbar {{
            display: none;
        }}

        /* Upgrade banner at the very top */
        .upgrade-banner {{
            background: linear-gradient(90deg, #0BB4FF 0%, #0995D6 100%);
            color: white;
            padding: 12px 20px;
            text-align: center;
            font-size: 14px;
            font-weight: 500;
            border-bottom: 2px solid #0885C6;
            flex-shrink: 0;
        }}

        .upgrade-banner a {{
            color: white;
            text-decoration: underline;
            font-weight: 600;
        }}

        .upgrade-banner a:hover {{
            text-decoration: none;
        }}

        .fixed-header {{
            position: sticky;
            top: 0;
            background: white;
            z-index: 100;
            padding: 20px;
            padding-bottom: 0;
            border-bottom: 1px solid #DADFCE;
            flex-shrink: 0;
        }}

        h1 {{
            font-size: 20px;
            font-weight: bold;
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

        .metric-dropdown {{
            display: none;
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
            position: relative;
            cursor: pointer;
            font-family: inherit;
        }}

        .metric-btn:hover:not(.locked) {{
            background: #DADFCE;
        }}

        .metric-btn.active {{
            background: #0BB4FF;
            border-color: #0BB4FF;
            color: #F6F7F3;
        }}

        /* Locked metric styling */
        .metric-btn.locked {{
            opacity: 0.6;
            cursor: not-allowed;
            border-color: #E0E0E0;
            background: #FAFAFA;
        }}

        .metric-btn.locked:hover {{
            background: #F5F5F5;
            border-color: #0BB4FF;
        }}

        .lock-icon {{
            display: inline-block;
            font-size: 10px;
            margin-right: 3px;
            opacity: 0.5;
            filter: grayscale(100%);
            vertical-align: middle;
        }}

        .metric-btn.locked:hover .lock-icon {{
            opacity: 0.7;
        }}

        /* Upgrade prompt modal */
        .upgrade-modal {{
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            z-index: 10000;
            max-width: 400px;
            text-align: center;
        }}

        .upgrade-modal.show {{
            display: block;
        }}

        .upgrade-modal h3 {{
            font-size: 18px;
            margin-bottom: 15px;
            color: #3D3733;
        }}

        .upgrade-modal p {{
            font-size: 14px;
            line-height: 1.6;
            margin-bottom: 20px;
            color: #6B635C;
        }}

        .upgrade-modal-buttons {{
            display: flex;
            gap: 10px;
            justify-content: center;
        }}

        .upgrade-modal-buttons button {{
            padding: 10px 20px;
            font-size: 14px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
            transition: all 0.2s;
        }}

        .upgrade-modal-buttons .upgrade-cta {{
            background: #0BB4FF;
            color: white;
            font-weight: 600;
        }}

        .upgrade-modal-buttons .upgrade-cta:hover {{
            background: #0995D6;
        }}

        .upgrade-modal-buttons .login-cta {{
            background: white;
            color: #0BB4FF;
            border: 2px solid #0BB4FF;
            font-weight: 600;
        }}

        .upgrade-modal-buttons .login-cta:hover {{
            background: #F0F0EC;
        }}

        .upgrade-modal-buttons .cancel {{
            background: #F0F0EC;
            color: #3D3733;
        }}

        .upgrade-modal-buttons .cancel:hover {{
            background: #DADFCE;
        }}

        .modal-backdrop {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 9999;
        }}

        .modal-backdrop.show {{
            display: block;
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
            margin: 15px 15px 15px 0;
            overflow: hidden;
        }}

        .summary-toggle {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 15px;
            cursor: pointer;
            user-select: none;
            background: linear-gradient(to right, transparent, rgba(11, 180, 255, 0.05));
            transition: all 0.2s;
            border-left: 3px solid #0BB4FF;
        }}

        .summary-toggle:hover {{
            background: linear-gradient(to right, rgba(11, 180, 255, 0.05), rgba(11, 180, 255, 0.1));
            border-left-width: 4px;
        }}

        .summary-toggle:hover .summary-toggle-text::after {{
            content: " (Click to expand)";
            font-size: 12px;
            color: #0BB4FF;
            font-weight: normal;
        }}

        .summary-toggle-text {{
            font-size: 14px;
            color: #3D3733;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
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

        .table-container {{
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 0 20px 20px 20px;
            max-width: 100%;
            border-right: none !important;
            box-shadow: none !important;
        }}

        .table-container::-webkit-scrollbar {{
            width: 12px;
            display: block !important;
        }}

        .table-container::-webkit-scrollbar-track {{
            background: #F6F7F3;
            border-radius: 6px;
        }}

        .table-container::-webkit-scrollbar-thumb {{
            background: #0BB4FF;
            border-radius: 6px;
            min-height: 50px;
        }}

        .table-container::-webkit-scrollbar-thumb:hover {{
            background: #0AA0E8;
        }}

        .table-container {{
            -ms-overflow-style: auto;
            scrollbar-width: thin;
            scrollbar-color: #0BB4FF #F6F7F3;
        }}

        table {{
            width: calc(100% - 10px);
            border-collapse: collapse;
            margin-right: 10px;
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
            cursor: pointer;
            transition: all 0.2s;
            position: relative;
        }}

        td.metro:hover {{
            color: #0BB4FF;
            text-decoration: underline;
        }}

        .click-hint {{
            display: inline-block;
            margin-left: 10px;
            font-size: 11px;
            color: #0BB4FF;
            font-weight: normal;
            opacity: 0;
            animation: fadeInOut 3s ease-in-out;
            animation-delay: 1s;
            pointer-events: none;
        }}

        @keyframes fadeInOut {{
            0% {{ opacity: 0; }}
            20% {{ opacity: 1; }}
            80% {{ opacity: 1; }}
            100% {{ opacity: 0; }}
        }}

        tbody tr:first-child .click-hint {{
            animation: pulse 2s ease-in-out infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 0.5; }}
            50% {{ opacity: 1; }}
        }}

        tr:hover {{
            background: rgba(218, 223, 206, 0.2);
        }}

        /* Chart Panel Styles */
        .chart-panel {{
            position: fixed;
            top: 0;
            right: -450px;
            width: 450px;
            height: 100vh;
            max-height: 100%;
            background: #F6F7F3;
            border-left: 1px solid #DADFCE;
            transition: right 0.3s ease;
            z-index: 9999;
            display: flex;
            flex-direction: column;
        }}

        .chart-panel.open {{
            right: 0;
        }}

        .chart-panel-header {{
            display: none;
        }}

        .chart-panel-title {{
            display: none;
        }}

        .chart-panel-close {{
            position: absolute;
            top: 15px;
            right: 15px;
            z-index: 10;
            background: white;
            border: 1px solid #DADFCE;
            border-radius: 50%;
            font-size: 18px;
            cursor: pointer;
            color: #6B635C;
            padding: 0;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}

        .chart-panel-close:hover {{
            background: #0BB4FF;
            color: white;
            border-color: #0BB4FF;
        }}

        .chart-panel-content {{
            flex: 1;
            overflow-y: scroll !important;
            overflow-x: hidden;
            padding: 50px 20px 40px 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            background: #F6F7F3;
            min-height: 101%;
            scrollbar-width: thin;
            scrollbar-color: #0BB4FF #F6F7F3;
        }}

        .chart-panel-content::-webkit-scrollbar {{
            width: 12px;
            display: block !important;
        }}

        .chart-panel-content::-webkit-scrollbar-track {{
            background: #F6F7F3;
            border-radius: 6px;
            margin: 10px 0;
            border: 1px solid #DADFCE;
        }}

        .chart-panel-content::-webkit-scrollbar-thumb {{
            background: #0BB4FF;
            border-radius: 6px;
            border: 1px solid #F6F7F3;
            min-height: 30px;
        }}

        .chart-panel-content::-webkit-scrollbar-thumb:hover {{
            background: #0995D6;
        }}

        .chart-loading {{
            display: none;
            text-align: center;
            padding: 40px;
            color: #6B635C;
        }}

        .chart-loading.active {{
            display: block;
        }}

        .chart-image {{
            width: 100%;
            max-width: 420px;
            height: auto;
            display: none;
        }}

        .chart-image.loaded {{
            display: block;
        }}

        .chart-error {{
            display: none;
            text-align: center;
            padding: 40px;
            color: #F4743B;
        }}

        .chart-error.active {{
            display: block;
        }}

        .chart-link {{
            margin-top: 10px;
            margin-bottom: 5px;
            text-align: center;
            font-size: 12px;
        }}

        .chart-link a {{
            color: #0BB4FF;
            text-decoration: none;
        }}

        .chart-link a:hover {{
            text-decoration: underline;
        }}

        .scroll-indicator {{
            position: absolute;
            bottom: 15px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 13px;
            font-weight: 600;
            color: white;
            background: #0BB4FF;
            padding: 8px 16px;
            border-radius: 20px;
            display: none;
            pointer-events: none;
            animation: bounce 1.5s infinite;
            box-shadow: 0 4px 12px rgba(11, 180, 255, 0.4);
            z-index: 10;
        }}

        .scroll-indicator.visible {{
            display: block;
        }}

        @keyframes bounce {{
            0%, 100% {{
                transform: translateX(-50%) translateY(0);
                box-shadow: 0 4px 12px rgba(11, 180, 255, 0.4);
            }}
            50% {{
                transform: translateX(-50%) translateY(-8px);
                box-shadow: 0 8px 20px rgba(11, 180, 255, 0.6);
            }}
        }}

        .table-container {{
            transition: margin-right 0.3s ease;
        }}

        .table-container.panel-open {{
            margin-right: 450px;
        }}

        body.in-iframe {{
            overflow-y: auto !important;
            overflow-x: hidden !important;
        }}

        body.in-iframe .table-container {{
            overflow-y: auto !important;
            overflow-x: hidden !important;
        }}

        body.in-iframe .chart-panel {{
            position: fixed;
            top: 140px !important;
            height: calc(100vh - 150px) !important;
            width: 420px !important;
            right: -420px !important;
            border-top-left-radius: 8px;
            border-left: 1px solid #DADFCE !important;
        }}

        body.in-iframe .chart-panel.open {{
            right: 0 !important;
        }}

        body.in-iframe .table-container.panel-open {{
            margin-right: 430px;
        }}

        .footer {{
            text-align: center;
            padding: 15px 20px;
            font-size: 11px;
            color: #6B635C;
            border-top: 1px solid #DADFCE;
            background: white;
            flex-shrink: 0;
        }}

        .arrow {{
            font-size: 10px;
            margin-left: 2px;
        }}

        @media (max-width: 768px) {{
            * {{
                -webkit-overflow-scrolling: touch !important;
            }}

            html, body {{
                margin: 0;
                padding: 0;
                overflow: auto;
                width: 100%;
                height: 100%;
            }}

            body {{
                font-size: 11px;
                display: block;
            }}

            .upgrade-banner {{
                font-size: 12px;
                padding: 8px 10px;
            }}

            th {{ font-size: 10px; }}
            td {{ font-size: 11px; padding: 4px 6px; }}

            .fixed-header {{
                position: sticky;
                top: 0;
                background: white;
                z-index: 100;
                padding: 10px;
            }}

            h1 {{
                font-size: 16px;
                margin-bottom: 8px;
            }}

            .summary-box {{
                display: none !important;
            }}

            #searchBox {{
                width: 100% !important;
                padding: 10px 14px !important;
                font-size: 14px !important;
                border: 1px solid #DADFCE !important;
                border-radius: 4px !important;
                margin-bottom: 10px !important;
                margin-right: 0 !important;
                display: block !important;
            }}

            .filter label {{
                display: none !important;
            }}

            .controls {{
                margin-bottom: 12px;
                display: flex;
                flex-direction: column;
                gap: 10px;
                align-items: stretch;
            }}

            .filter {{
                display: flex !important;
                flex-direction: column !important;
                gap: 10px !important;
            }}

            .metrics {{
                display: none !important;
            }}

            .metric-dropdown {{
                display: block !important;
                width: auto;
                min-width: 200px;
                max-width: 280px;
                padding: 10px 40px 10px 14px;
                font-size: 13px;
                border: 1px solid #DADFCE;
                background: white;
                border-radius: 4px;
                font-family: inherit;
                color: #3D3733;
                appearance: none;
                background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
                background-repeat: no-repeat;
                background-position: right 10px center;
                background-size: 20px;
            }}

            #marketFilter {{
                font-size: 13px;
                padding: 10px 40px 10px 14px;
                width: auto;
                min-width: 200px;
                max-width: 280px;
                border-radius: 4px;
                appearance: none;
                background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
                background-repeat: no-repeat;
                background-position: right 10px center;
                background-size: 20px;
            }}

            .fixed-header {{
                flex-shrink: 0;
            }}

            .mobile-wrapper {{
                position: relative;
                width: 100%;
                height: 100vh;
                overflow: hidden;
            }}

            .table-container {{
                width: 100%;
                max-height: calc(100vh - 240px);
                overflow-y: auto;
                -webkit-overflow-scrolling: touch;
                position: relative;
                flex: 1;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                position: relative;
            }}

            th:nth-child(4), td:nth-child(4),
            th:nth-child(5), td:nth-child(5),
            th:nth-child(6), td:nth-child(6),
            th:nth-child(8), td:nth-child(8),
            th:nth-child(9), td:nth-child(9) {{
                display: none !important;
            }}

            th, td {{
                padding: 8px 6px;
                border-bottom: 1px solid #e0e0e0;
                white-space: nowrap;
            }}

            th:first-child, td:first-child {{
                width: 25px;
                text-align: center;
                font-weight: bold;
                padding: 6px 2px;
            }}

            th:nth-child(2), td:nth-child(2) {{
                text-align: left;
                padding: 6px 8px;
                white-space: normal;
                word-break: break-word;
            }}

            th:nth-child(3), td:nth-child(3) {{
                width: 80px;
                text-align: right;
                padding: 6px 4px;
            }}

            th:nth-child(7), td:nth-child(7) {{
                width: 60px;
                text-align: right;
                padding: 6px 8px;
                font-weight: bold;
            }}

            table {{
                position: relative;
            }}

            thead {{
                position: -webkit-sticky !important;
                position: sticky !important;
                top: -1px !important;
                z-index: 500 !important;
            }}

            thead th {{
                position: -webkit-sticky !important;
                position: sticky !important;
                top: -1px !important;
                background: white !important;
                z-index: 501 !important;
                border-bottom: 3px solid #0BB4FF !important;
                padding: 12px 6px !important;
            }}

            .chart-panel, .modal-overlay {{
                display: none !important;
            }}

            td.metro {{
                color: #0BB4FF !important;
                text-decoration: underline !important;
                cursor: pointer !important;
            }}
        }}
    </style>
</head>
<body>
    <div class="upgrade-banner">
        Viewing limited version • Unlock all 12 metrics by <a href="https://www.home-economics.us/subscribe" target="_blank">upgrading</a>
    </div>

    <div class="fixed-header">
        <h1>{metric_info['display'].upper()}</h1>

        <div class="controls">
            <div class="metrics">
                {''.join(metric_buttons)}
            </div>

            <select class="metric-dropdown" id="metricDropdown" onchange="handleMetricChange(this)">
                {''.join(metric_options)}
            </select>

            <div class="filter">
                <input type="text" id="searchBox" placeholder="Search metros..." onkeyup="searchTable()" style="padding: 4px 8px; border: 1px solid #DADFCE; background: white; font-family: inherit; font-size: 12px; margin-right: 10px;">
                <label>Show:</label>
                <select id="marketFilter" onchange="filterTable()">
                    <option value="10" selected>Large Markets (Top 10%)</option>
                    <option value="25">Major Markets (Top 25%)</option>
                    <option value="50">Mid-Size Markets (Top 50%)</option>
                    <option value="100">All Markets</option>
                </select>
            </div>
        </div>
        """

    # Only show summary for median_sale_price
    if metric_key == 'MEDIAN_SALE_PRICE':
        html += f"""
        <div class="summary-box">
            <div class="summary-toggle" onclick="toggleSummary()">
                <span class="summary-toggle-text">Market Analysis Summary</span>
                <span class="summary-arrow" id="summaryArrow">▼</span>
            </div>
            <div class="summary-content" id="summaryContent">
                <p class="summary-text" id="summaryText">{summaries.get('10', 'No summary available.')}</p>
            </div>
        </div>
        """

    html += f"""
    </div>

    <div class="table-container">
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
            <tr id="medianRow" style="background: #F0F0EC; font-size: 11px; color: #6B635C;">
                <td></td>
                <td>Median</td>
                <td class="number" id="median-current">—</td>
                <td class="number" id="median-month1">—</td>
                <td class="number" id="median-month3">—</td>
                <td class="number" id="median-month6">—</td>
                <td class="number" id="median-year1">—</td>
                <td class="number" id="median-year3">—</td>
            </tr>
        </thead>
        <tbody>
    """

    # Add all data rows
    for i, row in enumerate(rankings_data, 1):
        html += f'''            <tr data-percentile="{row['market_percentile']:.1f}" '''
        html += f'data-metro="{row["metro_name"].lower()}" '
        html += f'data-current="{row["current_value"]}" '

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
        metro_url = format_metro_for_url(row["metro_name"])
        html += f'                <td class="metro" data-metro-url="{metro_url}" onclick="showChart(this)">{row["metro_name"]}</td>\n'

        multiplier = metric_info.get('multiplier', 1)
        current_val = format_value(row['current_value'], metric_info['format'], multiplier)
        html += f'                <td class="number current-value">{current_val}</td>\n'

        for period_key, period_name in [('1month', 'month1'), ('3month', 'month3'),
                                        ('6month', 'month6'), ('1year', 'year1'), ('3year', 'year3')]:
            change_val = row['changes'].get(period_key)
            change_text = format_change(change_val)
            html += f'                <td class="number change-{period_name}">{change_text}</td>\n'

        html += '            </tr>\n'

    html += f"""        </tbody>
    </table>
    </div>

    <div class="footer">
        <strong>Data:</strong> Redfin weekly housing market data | <strong>Updated:</strong> {date_str} | <strong>Version:</strong> Free<br>
        <a href="https://www.home-economics.us">Home Economics</a> | <a href="https://www.home-economics.us/subscribe" target="_blank">Upgrade to Premium</a><br>
        <small style="color: #999;">Version: {version} | Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</small>
    </div>

    <div class="modal-backdrop" id="modalBackdrop" onclick="closeUpgradeModal()"></div>

    <div class="upgrade-modal" id="upgradeModal">
        <h3>Premium Feature</h3>
        <p>Access all 12 housing market metrics including inventory levels, days on market, price drops, and more. Get the complete picture of real estate markets across the US.</p>
        <div class="upgrade-modal-buttons">
            <button class="upgrade-cta" onclick="window.open('https://www.home-economics.us/subscribe', '_blank')">Upgrade Now</button>
            <button class="login-cta" onclick="window.open('https://www.home-economics.us/login', '_blank')">Already a Member?<br>Log In</button>
            <button class="cancel" onclick="closeUpgradeModal()">Maybe Later</button>
        </div>
    </div>

    <div class="modal-overlay" id="modalOverlay" onclick="closeChartPanel()"></div>

    <div class="chart-panel" id="chartPanel">
        <button class="chart-panel-close" onclick="closeChartPanel()">×</button>
        <div class="chart-panel-content">
            <div class="chart-loading" id="chartLoading">Loading chart...</div>
            <img class="chart-image" id="chartImage" alt="Metro chart">
            <div class="chart-error" id="chartError">Chart not available for this metro</div>
            <div class="chart-link" id="chartLink"></div>
            <div class="scroll-indicator" id="scrollIndicator">Scroll down for full chart</div>
        </div>
    </div>

    <script>
        // Global state
        let allRows = [];
        let currentSort = 'current';
        let sortAscending = false;

        // Store summaries for different segments
        const marketSummaries = {json.dumps(summaries) if metric_key == 'MEDIAN_SALE_PRICE' else '{}'};

        // Initialize on page load
        window.onload = function() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            allRows = Array.from(tbody.querySelectorAll('tr'));

            // Initial sort by current value with coloring
            sortTableWithInitialColoring('current');

            // Calculate initial medians
            calculateMedians();

            // Detect if in iframe
            if (window.self !== window.top) {{
                document.body.classList.add('in-iframe');
            }}
        }};

        // Handle metric dropdown change
        function handleMetricChange(select) {{
            if (select.value.includes('_free')) {{
                window.location.href = select.value;
            }} else {{
                showUpgradePrompt();
                // Reset dropdown to current selection
                select.value = 'median_sale_price_free.html?v={version}';
            }}
        }}

        // Show upgrade prompt
        function showUpgradePrompt() {{
            document.getElementById('modalBackdrop').classList.add('show');
            document.getElementById('upgradeModal').classList.add('show');
        }}

        // Close upgrade modal
        function closeUpgradeModal() {{
            document.getElementById('modalBackdrop').classList.remove('show');
            document.getElementById('upgradeModal').classList.remove('show');
        }}

        // Special function for initial load to apply coloring
        function sortTableWithInitialColoring(column) {{
            currentSort = column;
            sortAscending = false;

            const arrow = document.getElementById('arrow-' + column);
            if (arrow) {{
                arrow.textContent = '↓';
            }}

            allRows.sort((a, b) => {{
                const aStr = a.dataset[column];
                const bStr = b.dataset[column];

                if (aStr === 'null' && bStr === 'null') return 0;
                if (aStr === 'null') return 1;
                if (bStr === 'null') return -1;

                const aVal = parseFloat(aStr);
                const bVal = parseFloat(bStr);

                return bVal - aVal;
            }});

            allRows.forEach(row => {{
                const td = row.querySelector('.current-value');
                if (td) {{
                    const value = parseFloat(row.dataset.current);
                    if (!isNaN(value)) {{
                        const allValues = allRows.map(r => parseFloat(r.dataset.current)).filter(v => !isNaN(v));
                        const max = Math.max(...allValues);
                        const min = Math.min(...allValues);
                        const range = max - min;
                        const percent = range > 0 ? ((value - min) / range) * 100 : 50;

                        let bgColor;
                        if (percent <= 20) bgColor = '#DADFCE';
                        else if (percent <= 40) bgColor = '#E8F4FF';
                        else if (percent <= 60) bgColor = '#C6E4FF';
                        else if (percent <= 80) bgColor = '#8CCFFF';
                        else bgColor = '#0BB4FF';

                        td.style.backgroundColor = bgColor;
                        td.style.color = (bgColor === '#0BB4FF' || bgColor === '#8CCFFF') ? '#F6F7F3' : '#3D3733';
                    }}
                }}
            }});

            const tbody = document.querySelector('#rankingsTable tbody');
            tbody.innerHTML = '';
            allRows.forEach((row, index) => {{
                const rankCell = row.querySelector('.rank');
                if (rankCell) rankCell.textContent = index + 1;
                tbody.appendChild(row);
            }});

            filterTable();
        }}

        // Simple, bulletproof sorting function
        function sortTable(column) {{
            if (currentSort === column) {{
                sortAscending = !sortAscending;
            }} else {{
                sortAscending = false;
                currentSort = column;
            }}

            document.querySelectorAll('.arrow').forEach(arrow => {{
                arrow.textContent = '';
            }});

            const arrow = document.getElementById('arrow-' + column);
            if (arrow) {{
                arrow.textContent = sortAscending ? '↑' : '↓';
            }}

            document.querySelectorAll('th').forEach(th => {{
                th.classList.remove('sorted');
            }});
            event.target.classList.add('sorted');

            allRows.sort((a, b) => {{
                let aVal, bVal;

                if (column === 'metro') {{
                    aVal = a.dataset.metro;
                    bVal = b.dataset.metro;
                    return sortAscending ?
                        aVal.localeCompare(bVal) :
                        bVal.localeCompare(aVal);
                }} else {{
                    const aStr = a.dataset[column];
                    const bStr = b.dataset[column];

                    if (aStr === 'null' && bStr === 'null') return 0;
                    if (aStr === 'null') return sortAscending ? -1 : 1;
                    if (bStr === 'null') return sortAscending ? 1 : -1;

                    const aVal = parseFloat(aStr);
                    const bVal = parseFloat(bStr);

                    return sortAscending ? aVal - bVal : bVal - aVal;
                }}
            }});

            document.querySelectorAll('td').forEach(td => {{
                if (td.className.includes('change-') || td.className.includes('number')) {{
                    td.style.backgroundColor = '';
                    td.style.color = '';
                }}
            }});

            if (column === 'current') {{
                allRows.forEach(row => {{
                    const td = row.querySelector('.current-value');
                    if (td) {{
                        const value = parseFloat(row.dataset.current);
                        if (!isNaN(value)) {{
                            const allValues = allRows.map(r => parseFloat(r.dataset.current)).filter(v => !isNaN(v));
                            const max = Math.max(...allValues);
                            const min = Math.min(...allValues);
                            const range = max - min;
                            const percent = range > 0 ? ((value - min) / range) * 100 : 50;

                            let bgColor;
                            if (percent <= 20) bgColor = '#DADFCE';
                            else if (percent <= 40) bgColor = '#E8F4FF';
                            else if (percent <= 60) bgColor = '#C6E4FF';
                            else if (percent <= 80) bgColor = '#8CCFFF';
                            else bgColor = '#0BB4FF';

                            td.style.backgroundColor = bgColor;
                            td.style.color = (bgColor === '#0BB4FF' || bgColor === '#8CCFFF') ? '#F6F7F3' : '#3D3733';
                        }}
                    }}
                }});
            }} else if (column !== 'metro') {{
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

            filterTable();
        }}

        function getColorForChange(val) {{
            if (val <= -5) return '#3D3733';
            if (val <= -2) return '#A09B95';
            if (val <= 0) return '#DADFCE';
            if (val <= 2) return '#C6E4FF';
            if (val <= 5) return '#8CCFFF';
            return '#0BB4FF';
        }}

        function getTextColor(bgColor) {{
            return (bgColor === '#3D3733' || bgColor === '#6B635C') ? '#F6F7F3' : '#3D3733';
        }}

        function calculateMedians() {{
            const tbody = document.querySelector('#rankingsTable tbody');
            const visibleRows = Array.from(tbody.querySelectorAll('tr'));

            if (visibleRows.length === 0) return;

            const columns = ['current', 'month1', 'month3', 'month6', 'year1', 'year3'];

            columns.forEach(col => {{
                const values = visibleRows
                    .map(row => {{
                        const val = row.dataset[col];
                        return val !== 'null' ? parseFloat(val) : null;
                    }})
                    .filter(v => v !== null && !isNaN(v))
                    .sort((a, b) => a - b);

                if (values.length > 0) {{
                    const median = values.length % 2 === 0
                        ? (values[values.length / 2 - 1] + values[values.length / 2]) / 2
                        : values[Math.floor(values.length / 2)];

                    let formatted;
                    if (col === 'current') {{
                        const metricType = '{metric_info.get("format", "number")}';
                        const multiplier = {metric_info.get("multiplier", 1)};
                        const displayVal = median * multiplier;

                        if (metricType === 'currency') {{
                            if (displayVal >= 1000000) {{
                                formatted = '$' + (displayVal / 1000000).toFixed(1) + 'M';
                            }} else if (displayVal >= 1000) {{
                                formatted = '$' + Math.round(displayVal / 1000) + 'K';
                            }} else {{
                                formatted = '$' + Math.round(displayVal);
                            }}
                        }} else if (metricType === 'percent') {{
                            formatted = displayVal.toFixed(1) + '%';
                        }} else {{
                            formatted = displayVal.toFixed(1);
                        }}
                    }} else {{
                        formatted = (median >= 0 ? '+' : '') + median.toFixed(1) + '%';
                    }}

                    document.getElementById('median-' + col).textContent = formatted;
                }} else {{
                    document.getElementById('median-' + col).textContent = '—';
                }}
            }});
        }}

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

            calculateMedians();

            // Update summary based on selected filter (only for median_sale_price)
            if (marketSummaries && Object.keys(marketSummaries).length > 0) {{
                const summaryText = marketSummaries[filter] || marketSummaries['100'];
                const summaryEl = document.getElementById('summaryText');
                if (summaryEl) {{
                    summaryEl.textContent = summaryText;
                }}
            }}
        }}

        function searchTable() {{
            filterTable();
        }}

        function toggleSummary() {{
            const content = document.getElementById('summaryContent');
            const arrow = document.getElementById('summaryArrow');

            if (content && arrow) {{
                if (content.classList.contains('expanded')) {{
                    content.classList.remove('expanded');
                    arrow.style.transform = 'rotate(0deg)';
                }} else {{
                    content.classList.add('expanded');
                    arrow.style.transform = 'rotate(180deg)';
                }}
            }}
        }}

        let currentChartMetro = null;

        function showChart(element) {{
            const metroName = element.textContent;
            const metroUrl = element.getAttribute('data-metro-url');
            const currentMetric = '{metric_info['slug']}';

            if (window.innerWidth <= 768) {{
                const chartUrl = `https://home-economics.us/wp-content/uploads/reports/live/mobile/${{metroUrl}}/${{metroUrl}}_${{currentMetric}}_mobile.png`;
                window.open(chartUrl, '_blank');
                return;
            }}

            document.getElementById('chartLoading').classList.add('active');
            document.getElementById('chartImage').classList.remove('loaded');
            document.getElementById('chartError').classList.remove('active');

            document.getElementById('chartPanel').classList.add('open');
            document.getElementById('modalOverlay').classList.add('open');
            document.querySelector('.table-container').classList.add('panel-open');

            const chartUrl = `https://home-economics.us/wp-content/uploads/reports/live/mobile/${{metroUrl}}/${{metroUrl}}_${{currentMetric}}_mobile.png`;

            const img = document.getElementById('chartImage');
            img.onload = function() {{
                document.getElementById('chartLoading').classList.remove('active');
                img.classList.add('loaded');

                const linkDiv = document.getElementById('chartLink');
                linkDiv.innerHTML = `<a href="${{chartUrl}}" target="_blank">Open chart in new tab ↗</a>`;

                setTimeout(() => {{
                    const content = document.querySelector('.chart-panel-content');
                    const hasScroll = content.scrollHeight > content.clientHeight;
                    const indicator = document.getElementById('scrollIndicator');
                    if (hasScroll) {{
                        indicator.classList.add('visible');
                        content.onscroll = function() {{
                            if (content.scrollTop > 50) {{
                                indicator.classList.remove('visible');
                            }} else {{
                                indicator.classList.add('visible');
                            }}
                        }};
                    }} else {{
                        indicator.classList.remove('visible');
                    }}
                }}, 100);
            }};

            img.onerror = function() {{
                document.getElementById('chartLoading').classList.remove('active');
                document.getElementById('chartError').classList.add('active');
                document.getElementById('chartLink').innerHTML = '';
            }};

            img.src = chartUrl;
            currentChartMetro = metroUrl;
        }}

        function closeChartPanel() {{
            document.getElementById('chartPanel').classList.remove('open');
            document.getElementById('modalOverlay').classList.remove('open');
            document.querySelector('.table-container').classList.remove('panel-open');
            document.getElementById('scrollIndicator').classList.remove('visible');
            currentChartMetro = null;
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                closeChartPanel();
                closeUpgradeModal();
            }}
        }});
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

    # Time periods
    periods = {
        '1month': 1,
        '3month': 3,
        '6month': 6,
        '1year': 13,
        '3year': 39
    }

    # Create output directory
    output_path = Path(args.output_dir)
    output_path.mkdir(exist_ok=True)

    # For free version, only process MEDIAN_SALE_PRICE
    metric_key = 'MEDIAN_SALE_PRICE'
    metric_info = METRICS[metric_key]

    print(f"Processing {metric_info['display']} for free version...")

    rankings_data = []
    for metro in metros_df['REGION_NAME'].unique():
        metro_data = metros_df[metros_df['REGION_NAME'] == metro].sort_values('PERIOD_END')

        if len(metro_data) == 0:
            continue

        latest_data = metro_data.iloc[-1]

        if pd.isna(latest_data[metric_key]):
            continue

        current_value = latest_data[metric_key]

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
            'current_value': current_value,
            'changes': changes,
            'market_percentile': market_percentile
        })

    # Sort by current value initially
    rankings_data.sort(key=lambda x: x['current_value'], reverse=True)

    # Generate and save HTML for free version
    html = generate_html_page_free(rankings_data, metric_key, metric_info, METRICS, date_str, sizes_df)
    output_file = output_path / f"{metric_info['slug']}_free.html"
    with open(output_file, 'w') as f:
        f.write(html)
    print(f"  Saved {output_file}")

    # Create index_free.html redirect
    import time
    version = int(time.time())
    with open(output_path / 'index_free.html', 'w') as f:
        f.write(f'''<!DOCTYPE html>
<html><head>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta http-equiv="refresh" content="0; url=median_sale_price_free.html?v={version}">
</head>
<body>Redirecting...</body></html>''')

    print(f"\nGenerated free version with median_sale_price only")
    print("Other metrics are locked with upgrade prompts")

if __name__ == '__main__':
    main()