#!/usr/bin/env python3
"""
Social Media Chart Generator V2
Creates square (1200x1200) charts optimized for X and LinkedIn
Includes BOTH histograms (levels and 3-month change) with better spacing
"""

from datetime import datetime, timedelta
import os
import numpy as np
import pandas as pd

# Set backend BEFORE importing pyplot
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from matplotlib import gridspec
from matplotlib.patches import Rectangle, Patch
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

# Color palette - EXACT from Denver
COLORS = {
    "blue": "#0BB4FF",
    "yellow": "#FEC439",
    "background": "#F6F7F3",
    "red": "#F4743B",
    "light_red": "#FBCAB5",
    "black": "#3D3733",
    "gray": "#808080",
    "green": "#67A275",
    "light_green": "#C6DCCB",
}

def setup_fonts():
    """Set up font sizes for social media format"""
    plt.rcParams["font.size"] = 9  # Even smaller base size for compact layout

def get_smart_y_limits(data_values, column_name=None):
    """Calculate smart y-axis limits for better variation visibility"""
    import numpy as np
    
    # Convert to numpy array if it's a list
    data_values = np.array(data_values)
    
    # Handle empty or invalid data
    if len(data_values) == 0 or np.all(np.isnan(data_values)):
        return 0, 1
    
    # Filter out NaN values
    data_values = data_values[~np.isnan(data_values)]
    if len(data_values) == 0:
        return 0, 1
    
    data_min = np.min(data_values)
    data_max = np.max(data_values)
    data_range = data_max - data_min
    data_mean = np.mean(data_values)
    
    # Metrics that should zoom in to show variation
    tight_range_metrics = [
        'AVERAGE_SALE_TO_LIST_RATIO',
        'MEDIAN_DAYS_TO_CLOSE',
        'MEDIAN_DAYS_ON_MARKET',
        'AGE_OF_INVENTORY'
    ]
    
    if column_name in tight_range_metrics:
        # Use tighter bounds to show variation
        if column_name == 'AVERAGE_SALE_TO_LIST_RATIO':
            # Sale to List Ratio needs special handling
            padding = min(data_range * 0.5, abs(data_mean) * 0.02)
            if padding == 0:
                padding = 0.01
            y_min = data_min - padding
            y_max = data_max + padding
        else:
            # For time-based metrics, don't force zero
            padding = data_range * 0.3 if data_range / data_mean < 0.2 else data_range * 0.2
            y_min = data_min - padding
            y_max = data_max + padding
            # But don't go below 0 for time metrics
            if column_name in ['MEDIAN_DAYS_TO_CLOSE', 'MEDIAN_DAYS_ON_MARKET', 'AGE_OF_INVENTORY']:
                y_min = max(0, y_min)
    else:
        # Default - standard padding
        y_min = 0 if data_min >= 0 and data_min < data_mean * 0.3 else data_min * 0.9
        y_max = data_max * 1.1
    
    return y_min, y_max

def get_bar_chart_y_limits(values, column_name=None):
    """Calculate y-axis limits for bar charts with headroom for labels"""
    import numpy as np
    
    # Convert to numpy array if it's a list
    values = np.array(values)
    
    # Handle empty or invalid data
    if len(values) == 0 or np.all(np.isnan(values)):
        return 0, 1
    
    # Filter out NaN values
    values = values[~np.isnan(values)]
    if len(values) == 0:
        return 0, 1
    
    data_min = np.min(values)
    data_max = np.max(values)
    
    # Always ensure 20% headroom for value labels
    y_max = data_max * 1.2
    
    # Special handling for Sale to List Ratio and time metrics
    if column_name == 'AVERAGE_SALE_TO_LIST_RATIO':
        y_min = data_min * 0.9  # Show variation
    elif column_name in ['MEDIAN_DAYS_TO_CLOSE', 'MEDIAN_DAYS_ON_MARKET']:
        y_min = data_min * 0.8  # Show variation but not misleading
    else:
        y_min = 0 if data_min >= 0 else data_min * 0.8
    
    # Ensure positive metrics don't go below 0
    positive_metrics = [
        'MEDIAN_DAYS_TO_CLOSE', 'MEDIAN_DAYS_ON_MARKET', 'AGE_OF_INVENTORY',
        'MEDIAN_SALE_PRICE', 'ACTIVE_LISTINGS', 'ADJUSTED_AVERAGE_NEW_LISTINGS',
        'ADJUSTED_AVERAGE_HOMES_SOLD', 'OFF_MARKET_IN_TWO_WEEKS', 'WEEKS_OF_SUPPLY'
    ]
    if column_name in positive_metrics:
        y_min = max(0, y_min)
    
    return y_min, y_max

def format_thousands(x, pos):
    """Format axis labels to use 'k' for thousands"""
    if abs(x) >= 1000:
        return f"{int(x/1000)}k"
    return f"{int(x)}"

def format_value(value, unit_label, decimals, is_percentage):
    """Format individual values for display"""
    if unit_label == "$":
        if value >= 1_000_000:
            return f"${value/1_000_000:.1f}M"
        elif value >= 1_000:
            return f"${value/1_000:.0f}K"
        else:
            return f"${value:.0f}"
    elif is_percentage:
        if value < 2:  # Stored as decimal
            return f"{value*100:.{decimals}f}%"
        else:
            return f"{value:.{decimals}f}%"
    elif unit_label == "%":
        if value < 2:  # Stored as decimal
            return f"{value*100:.{decimals}f}%"
        else:
            return f"{value:.{decimals}f}%"
    else:
        return f"{value:,.{decimals}f}"

def create_social_media_chart(df, metro_name, metric_config, output_filename):
    """
    Create a square social media chart for a metro and metric
    
    Args:
        df: DataFrame with all metro data
        metro_name: Full metro name (e.g., "Denver, CO metro area")
        metric_config: Dict with metric configuration
        output_filename: Path to save the chart
    
    Returns:
        bool: True if successful
    """
    setup_fonts()
    
    # Extract metric configuration
    metric_name = metric_config["name"]
    column_name = metric_config["column"]
    unit_label = metric_config.get("unit", "")
    decimals = metric_config.get("decimals", 0)
    is_percentage = metric_config.get("is_percentage", False)
    
    # Filter data for this metro
    metric_data = df[
        (df["REGION_NAME"] == metro_name) & (df["DURATION"] == "4 weeks")
    ].copy()
    
    if len(metric_data) == 0:
        print(f"No data found for {metro_name}")
        return False
    
    # Convert dates and sort
    metric_data["PERIOD_END"] = pd.to_datetime(metric_data["PERIOD_END"])
    metric_data = metric_data.sort_values("PERIOD_END")
    metric_data = metric_data.dropna(subset=[column_name])
    
    if len(metric_data) == 0:
        print(f"No valid data for {column_name} in {metro_name}")
        return False
    
    # Get latest date and value
    latest_date = metric_data["PERIOD_END"].max()
    latest_value = metric_data[metric_data["PERIOD_END"] == latest_date][column_name].values[0]
    
    # Format metro display name with state abbreviation
    metro_parts = metro_name.replace(" metro area", "").split(", ")
    if len(metro_parts) == 2:
        city_name, state_name = metro_parts
        # Map state names to abbreviations
        state_abbrevs = {
            "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
            "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
            "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
            "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
            "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
            "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
            "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
            "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
            "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
            "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
            "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
            "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
            "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC"
        }
        state_abbrev = state_abbrevs.get(state_name, state_name)
        metro_display = f"{city_name} Metro, {state_abbrev}"
    else:
        metro_display = metro_name.replace(" metro area", " Metro")
    
    # Create square figure (1200x1200 pixels at 100 DPI)
    fig = plt.figure(figsize=(12, 12), facecolor=COLORS["background"], dpi=100)
    
    # Create custom grid layout
    # Top row: history (left), ranking (right)
    # Middle row: momentum (left), histogram1 (right)
    # Bottom row: blank (left), histogram2 (right)
    gs = gridspec.GridSpec(
        3, 2,
        top=0.80,    # More breathing room at top
        bottom=0.10, # More breathing room at bottom
        left=0.12,
        right=0.94,
        hspace=0.25,  # Vertical spacing between rows
        wspace=0.25,  # Horizontal spacing between columns
        height_ratios=[1.2, 1, 0.8]  # Top row slightly taller
    )
    
    # Title section - moved down for more top margin
    fig.text(
        0.5, 0.90,
        metric_name.upper(),
        ha="center", va="top",
        fontsize=18, fontweight="bold",
        color=COLORS["black"]
    )
    
    # Metro name in blue
    fig.text(
        0.5, 0.87,
        metro_display.upper(),
        ha="center", va="top",
        fontsize=16, fontweight="bold",
        color=COLORS["blue"]
    )
    
    # Date info with additional details
    date_text = f'{latest_date.strftime("%b %d, %Y")} • {format_value(latest_value, unit_label, decimals, is_percentage)}'
    fig.text(
        0.5, 0.84,
        date_text,
        ha="center", va="top",
        fontsize=10,
        color=COLORS["gray"]
    )
    
    # Website attribution - bottom left
    fig.text(
        0.12, 0.05,
        "WWW.HOME-ECONOMICS.US",
        ha="left", va="bottom",
        fontsize=10, fontweight="bold",
        color=COLORS["black"],
        alpha=0.9
    )
    
    # ============================
    # 1. HISTORICAL TREND (Top-left)
    # ============================
    ax_history = fig.add_subplot(gs[0, 0])
    ax_history.set_facecolor(COLORS["background"])
    
    # Plot filled area
    ax_history.fill_between(
        metric_data["PERIOD_END"], 0, metric_data[column_name],
        color=COLORS["blue"], alpha=0.3
    )
    
    # Get current date info for comparison
    current_month = latest_date.month
    current_day = latest_date.day
    
    # Highlight the 3-month periods with hatching
    for year in range(latest_date.year, 2016, -1):
        if year == latest_date.year:
            year_end_date = latest_date
        else:
            year_all = metric_data[metric_data["PERIOD_END"].dt.year == year]
            if len(year_all) == 0:
                continue
            try:
                target_date = datetime(year, current_month, current_day)
            except Exception:
                target_date = datetime(year, current_month, 28)
            closest_idx = (year_all["PERIOD_END"] - target_date).abs().idxmin()
            year_end_date = year_all.loc[closest_idx, "PERIOD_END"]
        
        year_start_date = year_end_date - timedelta(days=90)
        period_data = metric_data[
            (metric_data["PERIOD_END"] >= year_start_date)
            & (metric_data["PERIOD_END"] <= year_end_date)
        ]
        
        if len(period_data) > 0:
            import matplotlib as mpl
            mpl.rcParams["hatch.linewidth"] = 0.3
            ax_history.fill_between(
                period_data["PERIOD_END"],
                0,
                period_data[column_name],
                color="none",
                edgecolor=COLORS["blue"],
                hatch="/////",
                alpha=1.0,
                linewidth=0,
            )
    
    # Add vertical lines and dots
    for year in range(latest_date.year, 2016, -1):
        if year == latest_date.year:
            year_end_date = latest_date
        else:
            year_all = metric_data[metric_data["PERIOD_END"].dt.year == year]
            if len(year_all) == 0:
                continue
            try:
                target_date = datetime(year, current_month, current_day)
            except Exception:
                target_date = datetime(year, current_month, 28)
            closest_idx = (year_all["PERIOD_END"] - target_date).abs().idxmin()
            year_end_date = year_all.loc[closest_idx, "PERIOD_END"]
        
        value_at_date = metric_data[metric_data["PERIOD_END"] == year_end_date][
            column_name
        ].iloc[0]
        ax_history.vlines(
            year_end_date, 0, value_at_date, 
            color=COLORS["black"], linewidth=1.5, zorder=10
        )
        ax_history.plot(
            year_end_date,
            value_at_date,
            "o",
            color=COLORS["black"],
            markersize=3,
            zorder=17,
        )
    
    # Plot line on top
    ax_history.plot(
        metric_data["PERIOD_END"], metric_data[column_name],
        color=COLORS["blue"], linewidth=1.5, zorder=15
    )
    
    # Title
    ax_history.text(
        0.0, 1.05,
        "Historical Trend",
        transform=ax_history.transAxes,
        fontsize=11, fontweight="bold",
        ha="left", va="top",
        color=COLORS["black"]
    )
    
    # Format axes
    ax_history.xaxis.set_major_locator(mdates.YearLocator())
    ax_history.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_history.tick_params(axis="both", labelsize=8)
    ax_history.grid(True, alpha=0.3, axis="y")
    
    # Apply smart y-axis limits
    y_min, y_max = get_smart_y_limits(metric_data[column_name].values, column_name)
    ax_history.set_ylim(y_min, y_max)
    
    # Add Y-axis formatter for thousands
    from matplotlib.ticker import FuncFormatter
    if unit_label == "$":
        ax_history.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"${x/1000:.0f}K" if x >= 1000 else f"${x:.0f}"
        ))
    elif unit_label == "" and column_name in ["ACTIVE_LISTINGS", "ADJUSTED_AVERAGE_NEW_LISTINGS", 
                                                "ADJUSTED_AVERAGE_HOMES_SOLD", "AVERAGE_PENDING_SALES_LISTING_UPDATES", 
                                                "OFF_MARKET_IN_TWO_WEEKS"]:
        ax_history.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"{x/1000:.0f}K" if x >= 1000 else f"{x:.0f}"
        ))
    elif is_percentage or unit_label == "%":
        ax_history.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"{x*100:.0f}%" if x < 2 else f"{x:.0f}%"
        ))
    
    # Y-axis label
    ax_history.set_ylabel(
        metric_name.title(),
        fontsize=10,
        color=COLORS["black"],
        labelpad=5
    )
    
    # Remove spines
    for spine in ax_history.spines.values():
        spine.set_visible(False)
    
    # Add legend for historical trend
    from matplotlib.patches import Rectangle
    from matplotlib.lines import Line2D
    hatch_patch = Rectangle((0, 0), 1, 1, facecolor='none', edgecolor=COLORS["blue"], 
                            hatch='/////', linewidth=0)
    line_with_dot = Line2D([0], [0], color=COLORS["black"], linewidth=1.5, 
                          marker='o', markersize=3, markerfacecolor=COLORS["black"])
    
    ax_history.legend([hatch_patch, line_with_dot], 
                     ['3-month', 'Current'],
                     loc='upper right', fontsize=7,
                     frameon=True, fancybox=False, shadow=False,
                     framealpha=1.0, facecolor=COLORS["background"], 
                     edgecolor='none',
                     bbox_to_anchor=(1, 1.05))
    
    # ============================
    # 2. HISTORICAL RANKING (Top-right)
    # ============================
    ax_ranking = fig.add_subplot(gs[0, 1])
    ax_ranking.set_facecolor(COLORS["background"])
    
    # Get same week data for recent years
    current_week = latest_date.isocalendar()[1]
    years = []
    values = []
    start_year = max(2019, latest_date.year - 5)
    
    for year in range(start_year, latest_date.year + 1):
        year_data = metric_data[metric_data["PERIOD_END"].dt.year == year]
        if len(year_data) > 0:
            week_data = year_data[year_data["PERIOD_END"].dt.isocalendar().week == current_week]
            if len(week_data) > 0:
                value = week_data.iloc[0][column_name]
            else:
                # Find closest date
                target_date = datetime(year, latest_date.month, latest_date.day)
                closest_idx = (year_data["PERIOD_END"] - target_date).abs().idxmin()
                value = year_data.loc[closest_idx, column_name]
            years.append(str(year))
            values.append(value)
    
    # Sort by values
    sorted_data = sorted(zip(years, values), key=lambda x: x[1], reverse=True)
    sorted_years = [x[0] for x in sorted_data]
    sorted_values = [x[1] for x in sorted_data]
    
    # Create bars
    x_pos = np.arange(len(sorted_years))
    bar_colors = [COLORS["black"] if year == str(latest_date.year) else COLORS["blue"] 
                  for year in sorted_years]
    
    bars = ax_ranking.bar(x_pos, sorted_values, color=bar_colors, alpha=1.0)
    
    # Add value labels with proper formatting
    for bar, val in zip(bars, sorted_values):
        # Enhanced formatting logic from mobile charts
        if column_name == "WEEKS_OF_SUPPLY":
            label = f"{int(val)}"
        elif column_name == "AVERAGE_SALE_TO_LIST_RATIO":
            label = f"{val*100:.1f}%"
        elif unit_label == "$":
            if val >= 1_000_000:
                label = f"${val/1_000_000:.1f}M"
            elif val >= 1000:
                label = f"${int(val/1000)}K"
            else:
                label = f"${int(val)}"
        elif column_name == "PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS":
            label = f"{val*100:.{decimals}f}%"
        elif column_name == "OFF_MARKET_IN_TWO_WEEKS":
            label = f"{int(val):,}"
        elif unit_label == "%":
            label = f"{val*100:.{decimals}f}%" if val < 2 else f"{val:.{decimals}f}%"
        elif val > 5000 and unit_label != "$" and not is_percentage:
            label = f"{int(val/1000)}k" if val >= 1000 else f"{int(val)}"
        else:
            label = format_value(val, unit_label, decimals, is_percentage)
        
        ax_ranking.text(
            bar.get_x() + bar.get_width()/2,
            bar.get_height() + max(sorted_values) * 0.02,
            label,
            ha="center", va="bottom",
            fontsize=9,
            color=COLORS["black"]
        )
    
    # Title
    ax_ranking.text(
        0.0, 1.05,
        "Same Week Comparison",
        transform=ax_ranking.transAxes,
        fontsize=11, fontweight="bold",
        ha="left", va="top",
        color=COLORS["black"]
    )
    
    # Format axes
    ax_ranking.set_xticks(x_pos)
    ax_ranking.set_xticklabels(sorted_years, fontsize=8)
    ax_ranking.tick_params(axis="y", labelsize=8)
    ax_ranking.grid(True, alpha=0.3, axis="y")
    
    # Apply smart y-axis limits for bar chart
    y_min, y_max = get_bar_chart_y_limits(sorted_values, column_name)
    ax_ranking.set_ylim(y_min, y_max)
    
    # Add Y-axis formatter for bar chart
    if unit_label == "$":
        ax_ranking.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"${x/1000:.0f}K" if x >= 1000 else f"${x:.0f}"
        ))
    elif unit_label == "" and column_name in ["ACTIVE_LISTINGS", "ADJUSTED_AVERAGE_NEW_LISTINGS", 
                                                "ADJUSTED_AVERAGE_HOMES_SOLD", "AVERAGE_PENDING_SALES_LISTING_UPDATES", 
                                                "OFF_MARKET_IN_TWO_WEEKS"]:
        ax_ranking.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"{x/1000:.0f}K" if x >= 1000 else f"{x:.0f}"
        ))
    elif is_percentage or unit_label == "%":
        ax_ranking.yaxis.set_major_formatter(FuncFormatter(
            lambda x, p: f"{x*100:.0f}%" if x < 2 else f"{x:.0f}%"
        ))
    
    # Y-axis label
    ax_ranking.set_ylabel(
        metric_name.title(),
        fontsize=10,
        color=COLORS["black"],
        labelpad=5
    )
    
    # Remove spines
    for spine in ax_ranking.spines.values():
        spine.set_visible(False)
    
    # ============================
    # 3. MOMENTUM TRIANGLE (Middle-left)
    # ============================
    ax_momentum = fig.add_subplot(gs[1, 0])
    ax_momentum.set_facecolor(COLORS["background"])
    
    # Calculate 3-month change
    three_months_ago = latest_date - timedelta(days=90)
    past_data = metric_data[metric_data["PERIOD_END"] <= three_months_ago]
    
    if len(past_data) > 0:
        past_value = past_data.iloc[-1][column_name]
        change = latest_value - past_value
    else:
        change = 0
    
    # Calculate historical average change
    historical_changes = []
    for year in range(start_year, latest_date.year):
        year_data = metric_data[
            (metric_data["PERIOD_END"].dt.year == year) &
            (metric_data["PERIOD_END"].dt.month == latest_date.month)
        ]
        if len(year_data) > 0:
            year_latest = year_data.iloc[-1]
            year_latest_date = year_latest["PERIOD_END"]
            year_latest_value = year_latest[column_name]
            
            year_three_months_ago = year_latest_date - timedelta(days=90)
            year_past_data = metric_data[
                metric_data["PERIOD_END"] <= year_three_months_ago
            ]
            
            if len(year_past_data) > 0:
                year_past_value = year_past_data.iloc[-1][column_name]
                year_change = year_latest_value - year_past_value
                historical_changes.append(year_change)
    
    avg_historical_change = np.mean(historical_changes) if historical_changes else 0
    
    # Create triangles - BIGGER
    triangle_width = 1.2
    triangle_current = mpatches.Polygon(
        [(0, 0), (triangle_width, change), (triangle_width, 0)],
        closed=True, fill=False, edgecolor=COLORS["black"],
        linewidth=1.5, hatch="///", alpha=1.0
    )
    
    triangle_historical = mpatches.Polygon(
        [(0, 0), (triangle_width, avg_historical_change), (triangle_width, 0)],
        closed=True, fill=True, facecolor=COLORS["blue"],
        edgecolor=COLORS["blue"], linewidth=1.5, alpha=0.8
    )
    
    ax_momentum.add_patch(triangle_historical)
    ax_momentum.add_patch(triangle_current)
    
    # Title
    ax_momentum.text(
        0.0, 1.05,
        "3-Month Momentum",
        transform=ax_momentum.transAxes,
        fontsize=11, fontweight="bold",
        ha="left", va="top",
        color=COLORS["black"]
    )
    
    # Add value labels with smart overlap prevention
    if unit_label == "$":
        if abs(change) >= 1000:
            current_label = f"{'+' if change > 0 else '-'}${abs(change)/1000:.0f}K"
        else:
            current_label = f"{'+' if change > 0 else '-'}${abs(change):.0f}"
        if abs(avg_historical_change) >= 1000:
            historical_label = f"{'+' if avg_historical_change > 0 else '-'}${abs(avg_historical_change)/1000:.0f}K"
        else:
            historical_label = f"{'+' if avg_historical_change > 0 else '-'}${abs(avg_historical_change):.0f}"
    elif is_percentage or unit_label == "%":
        # Check if stored as decimal (0.1) or percentage (10)
        if abs(change) < 2:  # Likely stored as decimal
            current_label = f"{change*100:+.1f}%"
            historical_label = f"{avg_historical_change*100:+.1f}%"
        else:
            current_label = f"{change:+.1f}%"
            historical_label = f"{avg_historical_change:+.1f}%"
    else:
        current_label = f"{change:+.{decimals}f}"
        if unit_label:
            current_label += f" {unit_label}"
        historical_label = f"{avg_historical_change:+.{decimals}f}"
        if unit_label:
            historical_label += f" {unit_label}"
    
    # Calculate text height for overlap prevention
    y_max = max(abs(change), abs(avg_historical_change)) * 1.3 if max(abs(change), abs(avg_historical_change)) > 0 else 1
    text_height_estimate = y_max * 0.15  # Estimate text box height
    value_gap = abs(change - avg_historical_change)
    
    # Smart positioning to prevent overlap
    if value_gap < text_height_estimate * 0.6:
        # Values are very close - offset horizontally
        ax_momentum.text(
            triangle_width + 0.05, change,
            current_label,
            fontsize=10, va="center",
            color=COLORS["black"], fontweight="bold"
        )
        ax_momentum.text(
            triangle_width + 0.3, avg_historical_change,
            historical_label,
            fontsize=9, va="center",
            color=COLORS["blue"]
        )
    elif value_gap < text_height_estimate * 1.5:
        # Values are moderately close - use vertical alignment
        if change > avg_historical_change:
            ax_momentum.text(
                triangle_width + 0.05, change,
                current_label,
                fontsize=10, va="bottom",
                color=COLORS["black"], fontweight="bold"
            )
            ax_momentum.text(
                triangle_width + 0.05, avg_historical_change,
                historical_label,
                fontsize=9, va="top",
                color=COLORS["blue"]
            )
        else:
            ax_momentum.text(
                triangle_width + 0.05, change,
                current_label,
                fontsize=10, va="top",
                color=COLORS["black"], fontweight="bold"
            )
            ax_momentum.text(
                triangle_width + 0.05, avg_historical_change,
                historical_label,
                fontsize=9, va="bottom",
                color=COLORS["blue"]
            )
    else:
        # Values are far apart - center alignment
        ax_momentum.text(
            triangle_width + 0.05, change,
            current_label,
            fontsize=10, va="center",
            color=COLORS["black"], fontweight="bold"
        )
        ax_momentum.text(
            triangle_width + 0.05, avg_historical_change,
            historical_label,
            fontsize=9, va="center",
            color=COLORS["blue"]
        )
    
    # Legend
    legend_elements = [
        Patch(facecolor="none", edgecolor=COLORS["black"], hatch="///", label="Current"),
        Patch(facecolor=COLORS["blue"], edgecolor=COLORS["blue"], alpha=0.8, label="Historical Avg")
    ]
    ax_momentum.legend(
        handles=legend_elements,
        loc="upper right",
        fontsize=9,
        frameon=True,
        framealpha=0.8,
        facecolor=COLORS["background"],
        edgecolor="none"
    )
    
    # Set limits - adjust for bigger triangle
    max_abs_change = max(abs(change), abs(avg_historical_change))
    y_max = max_abs_change * 1.3 if max_abs_change > 0 else 1
    ax_momentum.set_xlim(-0.1, 1.6)
    ax_momentum.set_ylim(-y_max, y_max * 1.3)
    
    # Remove spines and ticks
    for spine in ax_momentum.spines.values():
        spine.set_visible(False)
    ax_momentum.set_xticks([])
    ax_momentum.set_yticks([])
    
    # ============================
    # 4. NATIONAL COMPARISON - BOTH HISTOGRAMS (Right column, middle and bottom)
    # ============================
    
    # Get all metro data for current date
    metro_data_all = df[
        (df["REGION_TYPE_ID"] == -2) &
        (df["DURATION"] == "4 weeks") &
        (df["PERIOD_END"] == latest_date) &
        (df["REGION_NAME"] != "All Redfin Metros")
    ].copy()
    
    current_data = metro_data_all.dropna(subset=[column_name]).copy()
    
    # Filter out invalid values for certain metrics
    if column_name == "MEDIAN_DAYS_TO_CLOSE":
        current_data = current_data[current_data[column_name] >= 1].copy()
    
    # Filter out very small markets (< 5 homes sold per week)
    MIN_HOMES_SOLD = 5
    if "ADJUSTED_AVERAGE_HOMES_SOLD" in metro_data_all.columns:
        active_markets = metro_data_all[metro_data_all["ADJUSTED_AVERAGE_HOMES_SOLD"] >= MIN_HOMES_SOLD]["REGION_NAME"]
        current_data = current_data[current_data["REGION_NAME"].isin(active_markets)]
    
    if len(current_data) > 10:  # Only show histograms if enough data
        
        # ============================
        # 4A. CURRENT LEVEL HISTOGRAM (Middle-right)
        # ============================
        ax_hist1 = fig.add_subplot(gs[1, 1])
        ax_hist1.set_facecolor(COLORS["background"])
        
        values_for_hist = current_data[column_name].values
        
        # Remove outliers using IQR method
        q1 = np.percentile(values_for_hist, 25)
        q3 = np.percentile(values_for_hist, 75)
        iqr = q3 - q1
        
        # Use 5th and 95th percentile as initial bounds
        lower_percentile_bound = np.percentile(values_for_hist, 5)
        upper_percentile_bound = np.percentile(values_for_hist, 95)
        
        # Calculate reasonable bounds
        median_val_hist = np.median(values_for_hist)
        lower_bound = max(lower_percentile_bound, median_val_hist - 3 * iqr)
        upper_bound = min(upper_percentile_bound, median_val_hist + 3 * iqr)
        
        # Only slightly expand if target is outside but close
        if latest_value < lower_bound and latest_value > lower_percentile_bound * 0.5:
            lower_bound = latest_value - iqr * 0.2
        if latest_value > upper_bound and latest_value < upper_percentile_bound * 1.5:
            upper_bound = latest_value + iqr * 0.2
        
        # Filter values
        values_filtered = values_for_hist[
            (values_for_hist >= lower_bound) & (values_for_hist <= upper_bound)
        ]
        
        # Create histogram with explicit range
        n_bins = min(30, len(np.unique(values_filtered)))
        counts, bins, patches = ax_hist1.hist(
            values_filtered,
            bins=n_bins,
            range=(lower_bound, upper_bound),
            color=COLORS["blue"],
            alpha=0.3,
            edgecolor=COLORS["blue"],
            linewidth=0.5
        )
        
        # Highlight target metro
        target_bin_idx = np.digitize(latest_value, bins) - 1
        if 0 <= target_bin_idx < len(patches):
            patches[target_bin_idx].set_facecolor(COLORS["black"])
            patches[target_bin_idx].set_alpha(1.0)
        
        # Calculate percentile (using original unfiltered values)
        percentile = (values_for_hist < latest_value).sum() / len(values_for_hist) * 100
        
        # Main title for both histograms (only on first one)
        ax_hist1.text(
            0.0, 1.15,
            "National Comparison",
            transform=ax_hist1.transAxes,
            fontsize=11, fontweight="bold",
            ha="left", va="top",
            color=COLORS["black"]
        )
        
        # Subtitle for current level
        ax_hist1.text(
            0.0, 1.05,
            f"Current Level ({percentile:.0f}th %ile)",
            transform=ax_hist1.transAxes,
            fontsize=9, fontweight="normal",
            ha="left", va="top",
            color=COLORS["gray"]
        )
        
        # Add median line (using filtered values for display)
        median_val_display = np.median(values_filtered)
        ax_hist1.axvline(median_val_display, color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7)
        
        # Use full data median for legend
        median_val = np.median(values_for_hist)
        
        # Legend
        target_label = format_value(latest_value, unit_label, decimals, is_percentage)
        median_label = format_value(median_val, unit_label, decimals, is_percentage)
        
        # Shorten metro display for legend
        metro_short = metro_parts[0] if len(metro_parts) > 0 else metro_display.split(",")[0]
        
        legend_elements = [
            Rectangle((0, 0), 1, 1, facecolor=COLORS["black"], alpha=1.0, 
                     label=f"{metro_short}: {target_label}"),
            Line2D([0], [0], color=COLORS["gray"], linestyle=":", linewidth=1, 
                  alpha=0.7, label=f"Median: {median_label}")
        ]
        ax_hist1.legend(
            handles=legend_elements,
            loc="upper right",
            fontsize=7,
            frameon=True,
            framealpha=0.8,
            facecolor=COLORS["background"],
            edgecolor="none",
            bbox_to_anchor=(1.0, 0.98)
        )
        
        # Format axes
        ax_hist1.set_xlabel(metric_name.title(), fontsize=8, labelpad=2)
        ax_hist1.set_ylabel("Number of Metros", fontsize=8, labelpad=3)
        ax_hist1.tick_params(axis="both", labelsize=7)
        ax_hist1.grid(True, alpha=0.3, axis="y")
        
        # Add X-axis formatter for histogram
        if unit_label == "$":
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"${int(x/1000)}K" if x >= 1000 else f"${int(x)}"
            ))
        elif column_name == "PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS":
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"{x*100:.0f}%"
            ))
        elif column_name == "OFF_MARKET_IN_TWO_WEEKS":
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"{int(x):,}"
            ))
        elif column_name == "AVERAGE_SALE_TO_LIST_RATIO":
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"{x*100:.0f}%"
            ))
        elif unit_label == "%":
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"{x*100:.0f}%" if x < 2 else f"{x:.0f}%"
            ))
        elif column_name in ["ADJUSTED_AVERAGE_HOMES_SOLD", "ADJUSTED_AVERAGE_NEW_LISTINGS", "ACTIVE_LISTINGS"]:
            ax_hist1.xaxis.set_major_formatter(FuncFormatter(
                lambda x, p: f"{int(x/1000)}K" if x >= 1000 else f"{int(x)}"
            ))
        
        # Remove spines
        for spine in ax_hist1.spines.values():
            spine.set_visible(False)
        
        # ============================
        # 4B. 3-MONTH CHANGE HISTOGRAM (Bottom-right)
        # ============================
        ax_hist2 = fig.add_subplot(gs[2, 1])
        ax_hist2.set_facecolor(COLORS["background"])
        
        # Calculate 3-month changes for all metros
        three_months_ago = latest_date - timedelta(days=90)
        past_data_all = df[
            (df["REGION_TYPE_ID"] == -2) &
            (df["DURATION"] == "4 weeks") &
            (df["PERIOD_END"] <= three_months_ago) &
            (df["PERIOD_END"] >= three_months_ago - timedelta(days=30)) &
            (df["REGION_NAME"] != "All Redfin Metros")
        ].copy()
        
        past_data_all = past_data_all.sort_values(["REGION_NAME", "PERIOD_END"]).groupby("REGION_NAME").last()
        
        change_df = current_data.merge(
            past_data_all[[column_name]],
            left_on="REGION_NAME",
            right_index=True,
            suffixes=("", "_past"),
            how="inner"
        )
        
        # Calculate changes
        change_df["change_3m"] = change_df[column_name] - change_df[f"{column_name}_past"]
        
        target_row = change_df[change_df["REGION_NAME"] == metro_name]
        if len(target_row) > 0:
            target_change = target_row.iloc[0]["change_3m"]
            
            change_values = change_df["change_3m"].values
            change_values = change_values[~np.isnan(change_values)]
            
            if len(change_values) > 0:
                # Remove outliers for change histogram
                q1_change = np.percentile(change_values, 25)
                q3_change = np.percentile(change_values, 75)
                iqr_change = q3_change - q1_change
                
                lower_percentile_change = np.percentile(change_values, 5)
                upper_percentile_change = np.percentile(change_values, 95)
                
                median_change = np.median(change_values)
                lower_bound_change = max(lower_percentile_change, median_change - 3 * iqr_change)
                upper_bound_change = min(upper_percentile_change, median_change + 3 * iqr_change)
                
                # Expand if target is outside
                if target_change < lower_bound_change and target_change > lower_percentile_change - iqr_change:
                    lower_bound_change = target_change - iqr_change * 0.2
                if target_change > upper_bound_change and target_change < upper_percentile_change + iqr_change:
                    upper_bound_change = target_change + iqr_change * 0.2
                
                change_values_filtered = change_values[
                    (change_values >= lower_bound_change) & (change_values <= upper_bound_change)
                ]
                
                # Create histogram
                n_bins = min(30, len(np.unique(change_values_filtered)))
                counts, bins, patches = ax_hist2.hist(
                    change_values_filtered,
                    bins=n_bins,
                    range=(lower_bound_change, upper_bound_change),
                    color=COLORS["blue"],
                    alpha=0.3,
                    edgecolor=COLORS["blue"],
                    linewidth=0.5
                )
                
                # Highlight target metro
                target_change_bin_idx = np.digitize(target_change, bins) - 1
                if 0 <= target_change_bin_idx < len(patches):
                    patches[target_change_bin_idx].set_facecolor(COLORS["black"])
                    patches[target_change_bin_idx].set_alpha(1.0)
                
                # Calculate percentile for change
                percentile_change = (change_values < target_change).sum() / len(change_values) * 100
                
                # Subtitle for 3-month change
                ax_hist2.text(
                    0.0, 1.05,
                    f"3-Month Change ({percentile_change:.0f}th %ile)",
                    transform=ax_hist2.transAxes,
                    fontsize=9, fontweight="normal",
                    ha="left", va="top",
                    color=COLORS["gray"]
                )
                
                # Add median line
                median_change_display = np.median(change_values_filtered)
                ax_hist2.axvline(median_change_display, color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7)
                
                # Legend
                target_change_label = format_value(target_change, unit_label, decimals, is_percentage)
                median_change_label = format_value(median_change, unit_label, decimals, is_percentage)
                
                legend_elements = [
                    Rectangle((0, 0), 1, 1, facecolor=COLORS["black"], alpha=1.0, 
                             label=f"{metro_short}: {target_change_label}"),
                    Line2D([0], [0], color=COLORS["gray"], linestyle=":", linewidth=1, 
                          alpha=0.7, label=f"Median: {median_change_label}")
                ]
                ax_hist2.legend(
                    handles=legend_elements,
                    loc="upper right",
                    fontsize=7,
                    frameon=True,
                    framealpha=0.8,
                    facecolor=COLORS["background"],
                    edgecolor="none",
                    bbox_to_anchor=(1.0, 0.98)
                )
                
                # Format axes
                ax_hist2.set_xlabel("3-Month Change", fontsize=8, labelpad=2)
                ax_hist2.set_ylabel("Number of Metros", fontsize=8, labelpad=3)
                ax_hist2.tick_params(axis="both", labelsize=7)
                ax_hist2.grid(True, alpha=0.3, axis="y")
                
                # X-axis formatter for change histogram
                if unit_label == "$":
                    ax_hist2.xaxis.set_major_formatter(FuncFormatter(
                        lambda x, p: f"${int(x/1000)}K" if abs(x) >= 1000 else f"${int(x)}"
                    ))
                elif column_name in ["PERCENT_ACTIVE_LISTINGS_WITH_PRICE_DROPS", "AVERAGE_SALE_TO_LIST_RATIO"]:
                    ax_hist2.xaxis.set_major_formatter(FuncFormatter(
                        lambda x, p: f"{x*100:.0f}%"
                    ))
                elif unit_label == "%":
                    ax_hist2.xaxis.set_major_formatter(FuncFormatter(
                        lambda x, p: f"{x*100:.0f}%" if abs(x) < 2 else f"{x:.0f}%"
                    ))
                elif column_name in ["ADJUSTED_AVERAGE_HOMES_SOLD", "ADJUSTED_AVERAGE_NEW_LISTINGS", "ACTIVE_LISTINGS"]:
                    ax_hist2.xaxis.set_major_formatter(FuncFormatter(
                        lambda x, p: f"{int(x/1000)}K" if abs(x) >= 1000 else f"{int(x)}"
                    ))
                
                # Remove spines
                for spine in ax_hist2.spines.values():
                    spine.set_visible(False)
    
    # Save figure (PNG only)
    plt.savefig(
        output_filename,
        dpi=100,
        bbox_inches="tight",
        facecolor=COLORS["background"],
        pad_inches=0.1
    )
    
    plt.close()
    
    return True