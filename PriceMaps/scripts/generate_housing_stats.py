#!/usr/bin/env python3
"""
Generate housing market statistics for email updates
Creates markdown summary with top/bottom performers
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json

def load_data():
    """Load Zillow data and population data"""
    script_dir = Path(__file__).parent
    data_dir = script_dir.parent / 'data'

    # Load Zillow data
    df = pd.read_csv(data_dir / 'ZillowZip.csv')

    # Get date columns (all columns with YYYY-MM-DD format)
    date_cols = [col for col in df.columns if '-' in col and len(col) == 10]
    date_cols.sort()

    if len(date_cols) < 2:
        raise ValueError("Need at least 2 months of data")

    latest_date = date_cols[-1]
    prev_date = date_cols[-2]
    year_ago_date = None

    # Find date exactly 12 months ago
    latest_dt = pd.to_datetime(latest_date)
    for col in reversed(date_cols[:-1]):
        col_dt = pd.to_datetime(col)
        months_diff = (latest_dt.year - col_dt.year) * 12 + (latest_dt.month - col_dt.month)
        if months_diff == 12:
            year_ago_date = col
            break

    # Load population data
    pop_file = script_dir.parent / 'resources' / 'populations' / 'PopulationByZIP.csv'
    if pop_file.exists():
        try:
            pop_df = pd.read_csv(pop_file, encoding='utf-8')
        except UnicodeDecodeError:
            pop_df = pd.read_csv(pop_file, encoding='latin-1')

        # Handle different column names
        zip_col = 'ZIP census tabulation area' if 'ZIP census tabulation area' in pop_df.columns else 'ZIP'
        pop_col = 'Total population (2020 Census)' if 'Total population (2020 Census)' in pop_df.columns else 'Population'

        pop_df['ZIP'] = pop_df[zip_col].astype(str).str.zfill(5)
        pop_dict = dict(zip(pop_df['ZIP'], pop_df[pop_col]))
    else:
        pop_dict = {}

    return df, date_cols, latest_date, prev_date, year_ago_date, pop_dict

def calculate_national_stats(df, latest_date, prev_date, year_ago_date):
    """Calculate national-level statistics"""
    # Get US national data (RegionID 102001)
    us_data = df[df['RegionID'] == 102001].iloc[0] if len(df[df['RegionID'] == 102001]) > 0 else None

    if us_data is not None:
        median_price = us_data[latest_date]
        mom_change = ((us_data[latest_date] - us_data[prev_date]) / us_data[prev_date] * 100) if pd.notna(us_data[prev_date]) else None
        yoy_change = ((us_data[latest_date] - us_data[year_ago_date]) / us_data[year_ago_date] * 100) if year_ago_date and pd.notna(us_data[year_ago_date]) else None
    else:
        # Calculate from all ZIPs
        median_price = df[latest_date].median()
        mom_change = None
        yoy_change = None

    # Count ZIPs with increases/decreases
    if year_ago_date:
        df_with_yoy = df[(pd.notna(df[latest_date])) & (pd.notna(df[year_ago_date]))].copy()
        df_with_yoy['yoy_change'] = ((df_with_yoy[latest_date] - df_with_yoy[year_ago_date]) / df_with_yoy[year_ago_date] * 100)
        pct_increasing = (df_with_yoy['yoy_change'] > 0).sum() / len(df_with_yoy) * 100
        total_zips = len(df_with_yoy)
    else:
        pct_increasing = None
        total_zips = len(df[pd.notna(df[latest_date])])

    return {
        'median_price': median_price,
        'mom_change': mom_change,
        'yoy_change': yoy_change,
        'pct_increasing': pct_increasing,
        'total_zips': total_zips
    }

def get_rankings(df, latest_date, year_ago_date, pop_dict, min_pop=10000):
    """Get top/bottom rankings for various categories"""

    # Filter to ZIPs with population data >= min_pop
    df_ranked = df[pd.notna(df[latest_date])].copy()
    df_ranked['ZIP'] = df_ranked['RegionName'].astype(str).str.zfill(5)
    df_ranked['population'] = df_ranked['ZIP'].map(pop_dict)
    df_ranked = df_ranked[df_ranked['population'] >= min_pop].copy()

    # Calculate YoY change if available
    if year_ago_date:
        df_ranked = df_ranked[pd.notna(df_ranked[year_ago_date])].copy()
        df_ranked['yoy_change'] = ((df_ranked[latest_date] - df_ranked[year_ago_date]) / df_ranked[year_ago_date] * 100)

    # Get rankings
    rankings = {
        'highest_prices': df_ranked.nlargest(10, latest_date)[['ZIP', 'City', 'State', latest_date, 'population']].to_dict('records'),
        'lowest_prices': df_ranked.nsmallest(10, latest_date)[['ZIP', 'City', 'State', latest_date, 'population']].to_dict('records'),
    }

    if year_ago_date:
        rankings['accelerating'] = df_ranked.nlargest(10, 'yoy_change')[['ZIP', 'City', 'State', 'yoy_change', 'population']].to_dict('records')
        rankings['decelerating'] = df_ranked.nsmallest(10, 'yoy_change')[['ZIP', 'City', 'State', 'yoy_change', 'population']].to_dict('records')

    return rankings

def load_previous_rankings(output_dir):
    """Load previous month's rankings to identify new entries"""
    prev_file = output_dir / 'previous_rankings.json'
    if prev_file.exists():
        with open(prev_file, 'r') as f:
            return json.load(f)
    return None

def save_current_rankings(rankings, output_dir):
    """Save current rankings for next month's comparison"""
    prev_file = output_dir / 'previous_rankings.json'
    with open(prev_file, 'w') as f:
        json.dump({
            'highest_prices': [r['ZIP'] for r in rankings['highest_prices']],
            'lowest_prices': [r['ZIP'] for r in rankings['lowest_prices']],
            'accelerating': [r['ZIP'] for r in rankings.get('accelerating', [])],
            'decelerating': [r['ZIP'] for r in rankings.get('decelerating', [])]
        }, f, indent=2)

def format_narrative(stats, latest_date):
    """Generate narrative introduction"""
    latest_dt = pd.to_datetime(latest_date)
    month_name = latest_dt.strftime('%B')

    narrative = f"According to Zillow data compiled by Home Economics, "

    if stats['mom_change'] is not None:
        if abs(stats['mom_change']) < 0.1:
            narrative += f"home prices inched up by the slightest of margins ({stats['mom_change']:.2f}%) in {month_name}"
        elif stats['mom_change'] > 0:
            narrative += f"home prices rose {stats['mom_change']:.2f}% in {month_name}"
        else:
            narrative += f"home prices fell {abs(stats['mom_change']):.2f}% in {month_name}"
    else:
        narrative += f"the latest data through {month_name} shows"

    if stats['yoy_change'] is not None:
        narrative += f", bringing year-over-year appreciation to {stats['yoy_change']:.1f}%. "
    else:
        narrative += ". "

    if stats['pct_increasing'] is not None:
        if stats['pct_increasing'] > 50:
            fraction = "almost two-thirds" if stats['pct_increasing'] > 65 else "over half"
            narrative += f"Prices in {fraction} of the ZIP codes across the country are still higher than a year ago.\n\n"
        else:
            narrative += f"Just {stats['pct_increasing']:.0f}% of ZIP codes are seeing year-over-year increases.\n\n"

    narrative += f"The median home in the US now costs ${stats['median_price']/1000:.0f}K."

    return narrative

def format_rankings_text(rankings, prev_rankings):
    """Format rankings as plain text"""
    output = []

    # Highest Prices
    output.append("\n\nHighest Prices")
    for i, r in enumerate(rankings['highest_prices'], 1):
        new_marker = " (new)" if prev_rankings and r['ZIP'] not in prev_rankings.get('highest_prices', []) else ""
        city_state = f"{r['City']}, {r['State']}" if r['City'] != 'nan' else r['State']
        price = r[list(r.keys())[3]]  # The price column
        output.append(f"{r['ZIP']} {city_state} (${price/1000000:.2f}M, pop: {int(r['population']):,}){new_marker}")

    # Lowest Prices
    output.append("\n\nLowest Prices")
    for i, r in enumerate(rankings['lowest_prices'], 1):
        new_marker = " (new)" if prev_rankings and r['ZIP'] not in prev_rankings.get('lowest_prices', []) else ""
        city_state = f"{r['City']}, {r['State']}" if r['City'] != 'nan' else r['State']
        price = r[list(r.keys())[3]]  # The price column
        output.append(f"{r['ZIP']} {city_state} (${price/1000:.0f}K, pop: {int(r['population']):,}){new_marker}")

    # Accelerating Prices
    if 'accelerating' in rankings:
        output.append("\n\nAccelerating Prices (Y/Y)")
        for i, r in enumerate(rankings['accelerating'], 1):
            new_marker = " (new)" if prev_rankings and r['ZIP'] not in prev_rankings.get('accelerating', []) else ""
            city_state = f"{r['City']}, {r['State']}" if r['City'] != 'nan' else r['State']
            output.append(f"{r['ZIP']} {city_state} ({r['yoy_change']:+.1f}% y/y, pop: {int(r['population']):,}){new_marker}")

    # Decelerating Prices
    if 'decelerating' in rankings:
        output.append("\n\nDecelerating Prices (Y/Y)")
        for i, r in enumerate(rankings['decelerating'], 1):
            new_marker = " (new)" if prev_rankings and r['ZIP'] not in prev_rankings.get('decelerating', []) else ""
            city_state = f"{r['City']}, {r['State']}" if r['City'] != 'nan' else r['State']
            output.append(f"{r['ZIP']} {city_state} ({r['yoy_change']:+.1f}% y/y, pop: {int(r['population']):,}){new_marker}")

    return "\n".join(output)

def main():
    """Main execution"""
    print("📊 Generating housing market statistics...")

    # Load data
    df, date_cols, latest_date, prev_date, year_ago_date, pop_dict = load_data()
    print(f"📅 Latest date: {latest_date}")

    # Calculate national stats
    stats = calculate_national_stats(df, latest_date, prev_date, year_ago_date)
    print(f"💰 Median price: ${stats['median_price']:,.0f}")

    # Get rankings
    rankings = get_rankings(df, latest_date, year_ago_date, pop_dict)

    # Load previous rankings for comparison
    output_dir = Path(__file__).parent.parent / 'output'
    prev_rankings = load_previous_rankings(output_dir)

    # Format output
    narrative = format_narrative(stats, latest_date)
    rankings_text = format_rankings_text(rankings, prev_rankings)

    # Combine
    full_output = narrative + rankings_text
    full_output += f"\n\nData source: Zillow Home Value Index (ZHVI) as of {latest_date}"

    # Save to file
    output_file = output_dir / f"housing_stats_{latest_date.replace('-', '_')}.md"
    with open(output_file, 'w') as f:
        f.write(full_output)

    print(f"✅ Saved to: {output_file}")

    # Save current rankings for next time
    save_current_rankings(rankings, output_dir)

    # Print to stdout for GitHub Actions
    print("\n" + "="*80)
    print("HOUSING MARKET STATISTICS")
    print("="*80)
    print(full_output)
    print("="*80)

    return full_output

if __name__ == "__main__":
    main()
