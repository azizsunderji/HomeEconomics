#!/usr/bin/env python3
"""
Example of a chart file compatible with the live editor
Shows how to structure your chart code to work with the Flask server
"""
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from chartspec import ChartSpec, TitlePositions

def generate_chart_with_spec(spec: ChartSpec = None):
    """
    Main function that the editor will call
    Must accept a ChartSpec parameter and return a matplotlib figure
    """
    if spec is None:
        # Default spec if none provided
        spec = ChartSpec()
    
    # Create figure with spec dimensions
    fig = plt.figure(figsize=(spec.width, spec.height), dpi=spec.dpi, facecolor=spec.bg)
    
    # Set font
    plt.rcParams['font.sans-serif'] = [spec.font, 'Arial', 'DejaVu Sans']
    plt.rcParams['font.size'] = spec.base_fontsize if hasattr(spec, 'base_fontsize') else 10
    
    # Create grid layout
    gs = gridspec.GridSpec(
        3, 2,
        height_ratios=[1.2, 0.9, 0.9],
        width_ratios=[1, 1],
        top=spec.top,
        bottom=spec.bottom,
        left=spec.left,
        right=spec.right,
        hspace=spec.hspace,
        wspace=spec.wspace
    )
    
    # Add titles
    fig.text(0.5, spec.titles.main_y if hasattr(spec, 'titles') else 0.96, 
             'MEDIAN SALE PRICE', 
             fontsize=spec.title_size, weight='bold', 
             ha='center', va='top', color=spec.fg)
    
    fig.text(0.5, spec.titles.metro_y if hasattr(spec, 'titles') else 0.92, 
             'DENVER, CO METRO AREA', 
             fontsize=spec.metro_size if hasattr(spec, 'metro_size') else 12,
             ha='center', va='top', color=spec.brand_blue)
    
    fig.text(0.5, spec.titles.subtitle_y if hasattr(spec, 'titles') else 0.88,
             f'Data as of {datetime.now().strftime("%B %d, %Y")}',
             fontsize=spec.subtitle_size if hasattr(spec, 'subtitle_size') else 10,
             ha='center', va='top', color=spec.fg, alpha=0.7)
    
    # Create sample data
    dates = pd.date_range(end=datetime.now(), periods=260, freq='W')
    values = 500000 + np.cumsum(np.random.randn(260) * 5000)
    
    # 1. Time series (full width top)
    ax1 = fig.add_subplot(gs[0, :])
    ax1.fill_between(dates, values, alpha=0.6, color=spec.brand_blue)
    ax1.plot(dates, values, color=spec.brand_blue, linewidth=1.5)
    ax1.set_title('Historical Trend - Weekly Data', fontsize=spec.label_size)
    ax1.set_facecolor(spec.bg)
    ax1.grid(True, alpha=0.3)
    for spine in ax1.spines.values():
        spine.set_visible(False)
    
    # 2. Bar chart
    ax2 = fig.add_subplot(gs[1, 0])
    years = [2019, 2020, 2021, 2022, 2023, 2024]
    year_values = [480000, 490000, 520000, 540000, 530000, 525000]
    bars = ax2.bar(years, year_values, color=spec.brand_blue)
    for i, bar in enumerate(bars[:-1]):
        bar.set_alpha(0.6)
    ax2.set_title('Year Comparison', fontsize=spec.label_size)
    ax2.set_facecolor(spec.bg)
    ax2.grid(True, axis='y', alpha=0.3)
    for spine in ax2.spines.values():
        spine.set_visible(False)
    
    # 3. Histogram
    ax3 = fig.add_subplot(gs[1, 1])
    hist_data = np.random.normal(500000, 50000, 100)
    ax3.hist(hist_data, bins=20, color=spec.brand_blue, alpha=0.6)
    ax3.axvline(525000, color=spec.fg, linewidth=2)
    ax3.set_title('Current Level Distribution', fontsize=spec.label_size)
    ax3.set_facecolor(spec.bg)
    ax3.grid(True, axis='y', alpha=0.3)
    for spine in ax3.spines.values():
        spine.set_visible(False)
    
    # 4. Momentum
    ax4 = fig.add_subplot(gs[2, 0])
    x = np.array([0, 1, 2])
    y = np.array([0, 1.5, 2.8])
    ax4.fill_between(x, 0, y, color=spec.brand_blue, alpha=0.6)
    ax4.plot(x, y, color=spec.brand_blue, linewidth=2)
    ax4.set_title('Momentum: 3-Mo', fontsize=spec.label_size)
    ax4.set_xlim(-0.5, 2.5)
    ax4.set_xticks([])
    ax4.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax4.text(2, 2.8, '+2.8%', ha='left', va='center', fontsize=spec.label_size, weight='bold')
    ax4.set_facecolor(spec.bg)
    for spine in ax4.spines.values():
        spine.set_visible(False)
    
    # 5. 3-month change
    ax5 = fig.add_subplot(gs[2, 1])
    change_data = np.random.normal(2, 3, 100)
    ax5.hist(change_data, bins=20, color=spec.brand_blue, alpha=0.6)
    ax5.axvline(2.8, color=spec.fg, linewidth=2)
    ax5.set_title('3-Month Change Distribution', fontsize=spec.label_size)
    ax5.set_xlabel('% Change', fontsize=spec.label_size)
    ax5.set_facecolor(spec.bg)
    ax5.grid(True, axis='y', alpha=0.3)
    for spine in ax5.spines.values():
        spine.set_visible(False)
    
    # Set tick sizes for all axes
    for ax in [ax1, ax2, ax3, ax4, ax5]:
        ax.tick_params(labelsize=spec.tick_size if hasattr(spec, 'tick_size') else 9)
    
    plt.tight_layout()
    return fig

# Also provide a simple entry point for testing
if __name__ == '__main__':
    spec = ChartSpec()
    fig = generate_chart_with_spec(spec)
    plt.savefig('example_chart.png', dpi=100, facecolor=spec.bg, bbox_inches='tight')
    print("Chart saved as example_chart.png")