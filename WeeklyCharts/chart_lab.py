#!/usr/bin/env python3
"""
Streamlit Chart Lab - Visual tuning console for chart specifications
"""
import json
import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from chartspec import ChartSpec, LegendSpec, TitlePositions, get_social_media_spec
from style import apply_multi_panel_style, style_axis, add_titles, export_with_svg

st.set_page_config(layout="wide", page_title="Chart Lab")
st.title("📊 Chart Lab - Visual Tuning Console")

# Initialize session state
if 'spec' not in st.session_state:
    st.session_state.spec = get_social_media_spec()

# Sidebar controls
st.sidebar.header("Chart Dimensions")
col1, col2 = st.sidebar.columns(2)
with col1:
    width = st.number_input("Width (in)", 4.0, 20.0, st.session_state.spec.width, 0.5)
    dpi = st.number_input("DPI", 50, 300, st.session_state.spec.dpi, 10)
with col2:
    height = st.number_input("Height (in)", 4.0, 20.0, st.session_state.spec.height, 0.5)
    
st.sidebar.header("Margins (0-1 scale)")
col1, col2 = st.sidebar.columns(2)
with col1:
    left = st.slider("Left", 0.01, 0.3, st.session_state.spec.left, 0.01)
    top = st.slider("Top", 0.6, 0.99, st.session_state.spec.top, 0.01)
with col2:
    right = st.slider("Right", 0.7, 0.99, st.session_state.spec.right, 0.01)
    bottom = st.slider("Bottom", 0.01, 0.4, st.session_state.spec.bottom, 0.01)

st.sidebar.header("Title Positions (Y)")
main_y = st.sidebar.slider("Main Title Y", 0.85, 0.99, st.session_state.spec.titles.main_y, 0.005)
metro_y = st.sidebar.slider("Metro Title Y", 0.80, 0.98, st.session_state.spec.titles.metro_y, 0.005)
subtitle_y = st.sidebar.slider("Subtitle Y", 0.75, 0.95, st.session_state.spec.titles.subtitle_y, 0.005)

st.sidebar.header("Font Sizes")
col1, col2 = st.sidebar.columns(2)
with col1:
    title_size = st.number_input("Title", 8, 30, st.session_state.spec.title_size, 1)
    metro_size = st.number_input("Metro", 8, 25, st.session_state.spec.metro_size, 1)
    subtitle_size = st.number_input("Subtitle", 6, 20, st.session_state.spec.subtitle_size, 1)
with col2:
    label_size = st.number_input("Labels", 6, 18, st.session_state.spec.label_size, 1)
    tick_size = st.number_input("Ticks", 6, 16, st.session_state.spec.tick_size, 1)
    base_fs = st.number_input("Base", 6, 16, st.session_state.spec.base_fontsize, 1)

st.sidebar.header("Grid Layout (Multi-panel)")
col1, col2 = st.sidebar.columns(2)
with col1:
    hspace = st.slider("V-Space", 0.1, 0.8, st.session_state.spec.hspace, 0.05)
with col2:
    wspace = st.slider("H-Space", 0.1, 0.6, st.session_state.spec.wspace, 0.05)

# Update spec
spec = ChartSpec(
    width=width, height=height, dpi=dpi,
    left=left, right=right, top=top, bottom=bottom,
    title_size=title_size, metro_size=metro_size, subtitle_size=subtitle_size,
    label_size=label_size, tick_size=tick_size, base_fontsize=base_fs,
    hspace=hspace, wspace=wspace,
    titles=TitlePositions(main_y=main_y, metro_y=metro_y, subtitle_y=subtitle_y)
)
st.session_state.spec = spec

# Main area - Chart preview
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Chart Preview")
    
    # Load sample data
    @st.cache_data
    def load_data():
        try:
            df = pd.read_parquet('/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/data/weekly_housing_market_data.parquet')
            return df
        except:
            # Generate sample data if real data not available
            dates = pd.date_range(end=datetime.now(), periods=52*5, freq='W')
            return pd.DataFrame({
                'week_end_date': dates,
                'metro_region_type_name': ['Denver, CO metro area'] * len(dates),
                'MEDIAN_SALE_PRICE': np.random.normal(500000, 50000, len(dates)),
                'ACTIVE_LISTINGS': np.random.normal(5000, 500, len(dates)),
                'NEW_LISTINGS': np.random.normal(800, 100, len(dates)),
                'HOMES_SOLD': np.random.normal(600, 50, len(dates)),
                'OFF_MARKET_IN_TWO_WEEKS': np.random.normal(40, 5, len(dates)),
                'MONTHS_OF_SUPPLY': np.random.normal(2.5, 0.5, len(dates))
            })
    
    df = load_data()
    
    # Create the chart
    fig, gs = apply_multi_panel_style(spec)
    
    # Add titles
    add_titles(fig, spec, 
              "MEDIAN SALE PRICE",
              "DENVER, CO METRO AREA",
              f"Data based on 4 week window captured {datetime.now().strftime('%B %d, %Y')}")
    
    # Sample data for demo
    metro_data = df[df['metro_region_type_name'] == df['metro_region_type_name'].iloc[0]].tail(260)
    
    # Plot 1: Time series
    ax1 = fig.add_subplot(gs[0, :])
    style_axis(ax1, spec)
    if len(metro_data) > 0:
        ax1.fill_between(metro_data['week_end_date'], 
                        metro_data.get('MEDIAN_SALE_PRICE', metro_data.iloc[:, 2]),
                        alpha=0.6, color=spec.brand_blue)
        ax1.plot(metro_data['week_end_date'],
                metro_data.get('MEDIAN_SALE_PRICE', metro_data.iloc[:, 2]),
                color=spec.brand_blue, linewidth=1.5)
    ax1.set_title("Historical Trend - Weekly Data", fontsize=spec.label_size, pad=10)
    
    # Plot 2: Bar chart
    ax2 = fig.add_subplot(gs[1, 0])
    style_axis(ax2, spec)
    years = [2019, 2020, 2021, 2022, 2023, 2024]
    values = [400000, 420000, 480000, 520000, 510000, 500000]
    bars = ax2.bar(years[:5], values[:5], color=spec.brand_blue, alpha=0.8)
    ax2.bar(years[5:], values[5:], color=spec.brand_blue)
    ax2.set_title("Historical Comparison", fontsize=spec.label_size, pad=10)
    
    # Plot 3: Histogram
    ax3 = fig.add_subplot(gs[1, 1])
    style_axis(ax3, spec)
    ax3.hist(np.random.normal(0, 1, 100), bins=20, color=spec.brand_blue, alpha=0.6)
    ax3.set_title("Current Level", fontsize=spec.label_size, pad=10)
    
    # Plot 4: Momentum chart
    ax4 = fig.add_subplot(gs[2, 0])
    style_axis(ax4, spec)
    ax4.fill_between([0, 1, 2], [0, 50, 30], color=spec.brand_blue, alpha=0.4)
    ax4.set_title("Momentum: 3-Mo", fontsize=spec.label_size, pad=10)
    
    # Plot 5: Another histogram
    ax5 = fig.add_subplot(gs[2, 1])
    style_axis(ax5, spec)
    ax5.hist(np.random.normal(5, 2, 100), bins=15, color=spec.brand_blue, alpha=0.6)
    ax5.set_title("3-Month Change", fontsize=spec.label_size, pad=10)
    
    # Display the chart
    st.pyplot(fig)
    plt.close()
    
    # Pixel dimensions display
    st.info(f"📐 Output: {int(width*dpi)} × {int(height*dpi)} pixels at {dpi} DPI")

with col2:
    st.subheader("Export Options")
    
    # Show current spec as JSON
    st.text_area("Current Spec (JSON)", 
                 spec.to_json(), 
                 height=400)
    
    # Export buttons
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📥 Download Spec"):
            st.download_button(
                "Download chartspec.json",
                spec.to_json(),
                "chartspec.json",
                "application/json"
            )
    
    with col2:
        if st.button("📋 Copy to Clipboard"):
            st.code(spec.to_json(), language='json')
    
    # Load preset buttons
    st.subheader("Presets")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📱 Mobile"):
            from chartspec import get_mobile_spec
            st.session_state.spec = get_mobile_spec()
            st.rerun()
    with col2:
        if st.button("📷 Social Media"):
            st.session_state.spec = get_social_media_spec()
            st.rerun()
    
    # Import spec
    st.subheader("Import Spec")
    uploaded_file = st.file_uploader("Upload chartspec.json", type="json")
    if uploaded_file is not None:
        spec_data = json.load(uploaded_file)
        st.session_state.spec = ChartSpec.from_json(json_str=json.dumps(spec_data))
        st.success("Spec loaded!")
        st.rerun()

# Instructions
with st.expander("📖 How to Use"):
    st.markdown("""
    1. **Adjust parameters** using the sidebar controls
    2. **Preview changes** in real-time on the chart
    3. **Export the spec** as JSON when satisfied
    4. **Use the spec** in your chart generation code:
    
    ```python
    from chartspec import ChartSpec
    from style import apply_multi_panel_style, style_axis, add_titles
    
    # Load your tuned spec
    spec = ChartSpec.from_json('chartspec.json')
    
    # Create figure with spec
    fig, gs = apply_multi_panel_style(spec)
    add_titles(fig, spec, "YOUR TITLE", "YOUR METRO", "YOUR DATE")
    
    # Add your plots...
    ```
    
    **Tips:**
    - Title Y positions control vertical spacing of the 3-line header
    - Margins affect the overall chart area within the figure
    - H-Space and V-Space control gaps between panels
    - Export to JSON to save and reuse your perfect layout
    """)

# Debug info
with st.expander("🔧 Debug Info"):
    st.write("Figure margins:", f"L:{spec.left:.2f} R:{spec.right:.2f} T:{spec.top:.2f} B:{spec.bottom:.2f}")
    st.write("Title positions:", f"Main:{spec.titles.main_y:.3f} Metro:{spec.titles.metro_y:.3f} Sub:{spec.titles.subtitle_y:.3f}")
    st.write("Panel spacing:", f"H:{spec.hspace:.2f} V:{spec.wspace:.2f}")