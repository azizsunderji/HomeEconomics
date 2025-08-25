#!/usr/bin/env python3
"""
Social media chart generator using declarative ChartSpec
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timedelta
from chartspec import ChartSpec, get_social_media_spec
from style import apply_multi_panel_style, style_axis, add_titles, export_with_svg

def create_social_chart_with_spec(
    df: pd.DataFrame,
    metro: str,
    metric_config: dict,
    output_file: str,
    spec: ChartSpec = None
):
    """Generate a social media chart using ChartSpec"""
    
    if spec is None:
        spec = get_social_media_spec()
    
    # Extract metric details
    column = metric_config['column']
    name = metric_config['name']
    unit = metric_config.get('unit', '')
    decimals = metric_config.get('decimals', 0)
    is_percentage = metric_config.get('is_percentage', False)
    normalize_for_histogram = metric_config.get('normalize_for_histogram', None)
    normalized_unit_label = metric_config.get('normalized_unit_label', '')
    
    # Filter data for metro
    metro_data = df[df['REGION_NAME'] == metro].copy()
    if metro_data.empty:
        print(f"No data found for {metro}")
        return False
    
    # Sort by date and get recent data
    metro_data = metro_data.sort_values('PERIOD_END')
    recent_data = metro_data.tail(260)  # 5 years
    
    # Get current and historical values
    current_week = recent_data.iloc[-1]
    current_value = current_week[column]
    current_date = pd.to_datetime(current_week['PERIOD_END'])
    
    # Create figure with spec
    fig, gs = apply_multi_panel_style(spec)
    
    # Add titles using spec positions
    add_titles(fig, spec,
              name.upper(),
              metro.upper(),
              f"Data based on 4 week window captured {current_date.strftime('%B %d, %Y')}")
    
    # 1. Time series (full width, top)
    ax1 = fig.add_subplot(gs[0, :])
    style_axis(ax1, spec)
    
    ax1.fill_between(pd.to_datetime(recent_data['PERIOD_END']), recent_data[column], 
                     alpha=0.6, color=spec.brand_blue)
    ax1.plot(pd.to_datetime(recent_data['PERIOD_END']), recent_data[column], 
            color=spec.brand_blue, linewidth=1.5)
    
    ax1.set_title("Historical Trend - Weekly Data", fontsize=spec.label_size, pad=10)
    ax1.set_ylabel(name, fontsize=spec.label_size)
    
    # Add lookback lines
    for lookback_weeks in [13, 52]:
        if len(recent_data) > lookback_weeks:
            lookback_date = pd.to_datetime(recent_data.iloc[-lookback_weeks]['PERIOD_END'])
            ax1.axvline(lookback_date, color='gray', linestyle='--', alpha=0.3, linewidth=0.8)
            label = "3-month" if lookback_weeks == 13 else "1-year"
            ax1.text(lookback_date, ax1.get_ylim()[1]*0.95, label, 
                    rotation=0, ha='center', fontsize=8, alpha=0.5)
    
    ax1.tick_params(axis='x', rotation=0)
    
    # 2. Year comparison bar chart
    ax2 = fig.add_subplot(gs[1, 0])
    style_axis(ax2, spec)
    
    years = []
    values = []
    for year in range(2019, 2025):
        same_week_last_year = recent_data[
            (pd.to_datetime(recent_data['PERIOD_END']).dt.year == year) &
            (pd.to_datetime(recent_data['PERIOD_END']).dt.isocalendar().week == current_date.isocalendar().week)
        ]
        if not same_week_last_year.empty:
            years.append(year)
            values.append(same_week_last_year.iloc[0][column])
    
    if years:
        bars = ax2.bar(years, values, color=spec.brand_blue)
        
        # Set alpha for each bar
        for i, (year, bar) in enumerate(zip(years, bars)):
            if year < years[-1]:
                bar.set_alpha(0.6)
            else:
                bar.set_alpha(1.0)
        
        # Add value labels
        for bar, val in zip(bars, values):
            height = bar.get_height()
            label = f"{val:,.{decimals}f}{unit}" if not is_percentage else f"{val:.{decimals}f}%"
            ax2.text(bar.get_x() + bar.get_width()/2., height,
                    label, ha='center', va='bottom', fontsize=8)
    
    ax2.set_title(f"Historical Comparison: Same Week (August {current_date.day})", 
                 fontsize=spec.label_size, pad=10)
    ax2.set_xlabel("Year", fontsize=spec.label_size)
    ax2.set_ylabel(name, fontsize=spec.label_size)
    
    # 3. Current level histogram
    ax3 = fig.add_subplot(gs[1, 1])
    style_axis(ax3, spec)
    
    # National comparison
    all_metros = df[pd.to_datetime(df['PERIOD_END']) == current_date][column].dropna()
    
    if normalize_for_histogram == 'per_100_active' and 'ACTIVE_LISTINGS' in df.columns:
        active_col = df[pd.to_datetime(df['PERIOD_END']) == current_date]['ACTIVE_LISTINGS']
        normalized_values = (all_metros / active_col * 100).dropna()
        ax3.hist(normalized_values, bins=30, alpha=0.6, color=spec.brand_blue)
        
        current_normalized = (current_value / current_week['ACTIVE_LISTINGS'] * 100)
        ax3.axvline(current_normalized, color=spec.fg, linewidth=2)
        
        percentile = (normalized_values < current_normalized).mean() * 100
        ax3.text(0.95, 0.95, f"{metro.split(',')[0]}: {current_normalized:.1f}%\nOther metros\nMedian: {normalized_values.median():.1f}%",
                transform=ax3.transAxes, ha='right', va='top', fontsize=9)
    else:
        ax3.hist(all_metros, bins=30, alpha=0.6, color=spec.brand_blue)
        ax3.axvline(current_value, color=spec.fg, linewidth=2)
        
        percentile = (all_metros < current_value).mean() * 100
        label = f"{current_value:,.{decimals}f}{unit}" if not is_percentage else f"{current_value:.{decimals}f}%"
        ax3.text(0.95, 0.95, f"{metro.split(',')[0]}: {label}\nOther metros\nMedian: {all_metros.median():,.{decimals}f}",
                transform=ax3.transAxes, ha='right', va='top', fontsize=9)
    
    ax3.set_title(f"Current Level ({percentile:.0f}th percentile)", 
                 fontsize=spec.label_size, pad=10)
    ax3.set_xlabel(normalized_unit_label if normalize_for_histogram else name, 
                  fontsize=spec.label_size)
    
    # 4. Momentum chart
    ax4 = fig.add_subplot(gs[2, 0])
    style_axis(ax4, spec)
    
    if len(recent_data) >= 13:
        three_month_ago = recent_data.iloc[-13][column]
        momentum = ((current_value - three_month_ago) / three_month_ago * 100)
        
        x = np.array([0, 1, 2])
        y = np.array([0, momentum/2, momentum])
        
        ax4.fill_between(x, 0, y, where=(y >= 0), interpolate=True, 
                        color=spec.brand_blue, alpha=0.6)
        ax4.fill_between(x, 0, y, where=(y < 0), interpolate=True, 
                        color=spec.brand_red, alpha=0.6)
        ax4.plot(x, y, color=spec.brand_blue if momentum >= 0 else spec.brand_red, linewidth=2)
        
        ax4.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        ax4.set_xlim(-0.5, 2.5)
        
        # Add value annotation
        ax4.text(2, momentum, f"{momentum:+.1f}%", 
                ha='left', va='center', fontsize=spec.label_size, weight='bold')
    
    ax4.set_title(f"Momentum: 3-Mo", fontsize=spec.label_size, pad=10)
    ax4.set_xticks([])
    
    # 5. 3-month change distribution
    ax5 = fig.add_subplot(gs[2, 1])
    style_axis(ax5, spec)
    
    three_month_changes = []
    for metro_name in df['REGION_NAME'].unique():
        metro_df = df[df['REGION_NAME'] == metro_name].sort_values('PERIOD_END')
        if len(metro_df) >= 13:
            recent = metro_df.iloc[-1][column]
            three_mo_ago = metro_df.iloc[-13][column]
            if pd.notna(recent) and pd.notna(three_mo_ago) and three_mo_ago != 0:
                change = ((recent - three_mo_ago) / three_mo_ago * 100)
                three_month_changes.append(change)
    
    if three_month_changes and len(recent_data) >= 13:
        ax5.hist(three_month_changes, bins=30, alpha=0.6, color=spec.brand_blue)
        
        three_month_ago = recent_data.iloc[-13][column]
        current_change = ((current_value - three_month_ago) / three_month_ago * 100)
        ax5.axvline(current_change, color=spec.fg, linewidth=2)
        
        ax5.text(0.95, 0.95, f"{metro.split(',')[0]}: {current_change:+.1f}%\nOther metros\nMedian: {np.median(three_month_changes):+.1f}%",
                transform=ax5.transAxes, ha='right', va='top', fontsize=9)
    
    ax5.set_title(f"3-Month Change in {name}", fontsize=spec.label_size, pad=10)
    ax5.set_xlabel("% Change", fontsize=spec.label_size)
    
    # Export with both PNG and SVG
    plt.tight_layout()
    export_with_svg(fig, output_file.replace('.png', ''), spec)
    plt.close()
    
    return True

def test_with_spec():
    """Test the spec-based chart generator"""
    print("Loading data...")
    df = pd.read_parquet('/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/data/weekly_housing_market_data.parquet')
    
    # Test with custom spec
    spec = get_social_media_spec()
    
    # Adjust spacing for better layout
    spec.titles.main_y = 0.96
    spec.titles.metro_y = 0.925
    spec.titles.subtitle_y = 0.89
    spec.hspace = 0.55
    spec.wspace = 0.35
    
    metric_config = {
        'column': 'MEDIAN_SALE_PRICE',
        'name': 'Median Sale Price',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False
    }
    
    print("Generating chart with spec...")
    # Get a metro that exists in the data
    metro_df = df[df['REGION_TYPE'] == 'metro']
    if 'Denver, CO metro area' in metro_df['REGION_NAME'].values:
        metro = "Denver, CO metro area"
    else:
        metro = metro_df['REGION_NAME'].iloc[0]
        print(f"Using metro: {metro}")
    
    success = create_social_chart_with_spec(
        df, 
        metro,
        metric_config,
        "spec_based_chart.png",
        spec
    )
    
    if success:
        print("✓ Chart generated successfully!")
        print("✓ Spec saved to: spec_based_chart_spec.json")
        spec.to_json("spec_based_chart_spec.json")
    else:
        print("✗ Failed to generate chart")

if __name__ == "__main__":
    test_with_spec()