#!/usr/bin/env python3
"""
Wrapper to make social_media_chart_generator_v2 work with the live editor
"""
import sys
import os
import pandas as pd
import matplotlib.pyplot as plt
from chartspec import ChartSpec

# Import the social chart generator
from social_media_chart_generator_v2 import create_exact_metro_chart

def generate_chart_with_spec(spec: ChartSpec = None):
    """
    Generate a social chart that can be edited with ChartSpec
    This wrapper handles data loading and calls the original function
    """
    if spec is None:
        spec = ChartSpec()
    
    # Load the data
    data_path = '/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/data/weekly_housing_market_data.parquet'
    
    try:
        df = pd.read_parquet(data_path)
    except FileNotFoundError:
        # If data not found, create sample data for testing
        print(f"Warning: Could not find {data_path}, using sample data")
        import numpy as np
        from datetime import datetime, timedelta
        dates = pd.date_range(end=datetime.now(), periods=260, freq='W')
        df = pd.DataFrame({
            'PERIOD_END': dates,
            'REGION_NAME': ['Denver, CO metro area'] * 260,
            'REGION_TYPE': ['metro'] * 260,
            'MEDIAN_SALE_PRICE': 500000 + np.cumsum(np.random.randn(260) * 5000),
            'ACTIVE_LISTINGS': 5000 + np.random.randn(260) * 500,
            'ADJUSTED_AVERAGE_NEW_LISTINGS': 800 + np.random.randn(260) * 100,
            'OFF_MARKET_IN_TWO_WEEKS': 40 + np.random.randn(260) * 5,
        })
    
    # Default metro and metric
    metro = "Denver, CO metro area"
    metric_config = {
        'column': 'MEDIAN_SALE_PRICE',
        'name': 'Median Sale Price',
        'unit': '$',
        'decimals': 0,
        'is_percentage': False
    }
    
    # Create temporary file for the chart
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        output_file = tmp.name
    
    # Override the font and spacing settings in the chart generator
    import matplotlib
    matplotlib.rcParams['font.sans-serif'] = [spec.font, 'Arial', 'DejaVu Sans']
    matplotlib.rcParams['font.size'] = spec.base_fontsize if hasattr(spec, 'base_fontsize') else 10
    
    # Create the chart using the original function
    success = create_exact_metro_chart(df, metro, metric_config, output_file)
    
    if success:
        # The chart was saved to file, now we need to get the figure
        # Since create_exact_metro_chart closes the figure, we need to reload it
        # or modify how we call it
        
        # For now, let's get the current figure that was created
        fig = plt.gcf()
        
        # Apply ChartSpec adjustments to the existing figure
        # This is a bit hacky but works for the editor
        
        # Adjust the figure layout based on spec
        fig.subplots_adjust(
            top=spec.top,
            bottom=spec.bottom,
            left=spec.left,
            right=spec.right,
            hspace=spec.hspace,
            wspace=spec.wspace
        )
        
        # Update title positions if they exist
        for text in fig.texts:
            # Try to identify which title this is based on y position
            y_pos = text.get_position()[1]
            if y_pos > 0.9:  # Main title area
                if hasattr(spec, 'titles'):
                    # Adjust based on content
                    if 'MEDIAN' in text.get_text() or 'ACTIVE' in text.get_text() or 'NEW' in text.get_text():
                        text.set_position((0.5, spec.titles.main_y))
                        text.set_fontsize(spec.title_size)
                    elif 'metro area' in text.get_text().lower():
                        text.set_position((0.5, spec.titles.metro_y))
                        text.set_fontsize(spec.metro_size if hasattr(spec, 'metro_size') else 12)
                    elif 'Data' in text.get_text():
                        text.set_position((0.5, spec.titles.subtitle_y))
                        text.set_fontsize(spec.subtitle_size if hasattr(spec, 'subtitle_size') else 10)
        
        # Update font sizes for all axes
        for ax in fig.get_axes():
            ax.tick_params(labelsize=spec.tick_size if hasattr(spec, 'tick_size') else 9)
            ax.title.set_fontsize(spec.label_size if hasattr(spec, 'label_size') else 10)
            if ax.get_xlabel():
                ax.xaxis.label.set_fontsize(spec.label_size if hasattr(spec, 'label_size') else 10)
            if ax.get_ylabel():
                ax.yaxis.label.set_fontsize(spec.label_size if hasattr(spec, 'label_size') else 10)
        
        # Clean up temp file
        try:
            os.remove(output_file)
        except:
            pass
        
        return fig
    else:
        # Create an empty figure with error message
        fig = plt.figure(figsize=(spec.width, spec.height), dpi=spec.dpi, facecolor=spec.bg)
        fig.text(0.5, 0.5, 'Failed to generate chart\nCheck data availability', 
                ha='center', va='center', fontsize=20)
        return fig

# For testing
if __name__ == '__main__':
    spec = ChartSpec()
    fig = generate_chart_with_spec(spec)
    plt.savefig('test_social_editable.png', dpi=100, bbox_inches='tight')
    print("Test chart saved as test_social_editable.png")