#!/usr/bin/env python3
"""
Create an interactive year-over-year home price map with search and local view features
"""

import pandas as pd
import numpy as np
import geopandas as gpd
import json
from datetime import datetime

print("🏠 Creating Year-over-Year Home Price Map with Search...")

# Load Zillow housing data
df = pd.read_csv('data/ZillowZip.csv')

# Get date columns
date_columns = [col for col in df.columns if '-' in col]
if not date_columns:
    print("❌ Error: No date columns found in data")
    exit(1)

# Get latest date
latest_date = date_columns[-1]
print(f"📅 Latest date: {latest_date}")
date_obj = datetime.strptime(latest_date, "%Y-%m-%d")

# Helper function to find closest date N months ago
def find_date_n_months_ago(target_months):
    from dateutil.relativedelta import relativedelta
    target_date = date_obj - relativedelta(months=target_months)

    # Find closest available date
    best_match = None
    min_diff = float('inf')

    for col in date_columns:
        try:
            col_date = datetime.strptime(col, "%Y-%m-%d")
            diff = abs((col_date - target_date).days)
            if diff < min_diff:
                min_diff = diff
                best_match = col
        except:
            continue

    return best_match

# Find dates for all time horizons
print("\n📅 Finding dates for all time horizons...")
date_3m = find_date_n_months_ago(3)
date_6m = find_date_n_months_ago(6)
date_1y = find_date_n_months_ago(12)
date_3y = find_date_n_months_ago(36)
date_5y = find_date_n_months_ago(60)
date_10y = find_date_n_months_ago(120)
date_15y = find_date_n_months_ago(180)

print(f"   3-month ago:  {date_3m}")
print(f"   6-month ago:  {date_6m}")
print(f"   1-year ago:   {date_1y}")
print(f"   3-year ago:   {date_3y}")
print(f"   5-year ago:   {date_5y}")
print(f"   10-year ago:  {date_10y}")
print(f"   15-year ago:  {date_15y}")

# Collect all required date columns
required_dates = [latest_date, date_3m, date_6m, date_1y, date_3y, date_5y, date_10y, date_15y]
required_dates = [d for d in required_dates if d is not None]

# Calculate price appreciation for all horizons
df_analysis = df[['RegionName', 'State', 'City'] + required_dates].copy()
# Only require latest_date and at least 1y data (we'll handle missing data per-horizon)
df_analysis = df_analysis.dropna(subset=[latest_date, date_1y])

# Calculate changes for each horizon (as integers), handling missing data gracefully
if date_3m:
    mask = df_analysis[date_3m].notna()
    df_analysis.loc[mask, 'change_3m'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_3m]) / df_analysis.loc[mask, date_3m] * 100).round(0).astype(int)

if date_6m:
    mask = df_analysis[date_6m].notna()
    df_analysis.loc[mask, 'change_6m'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_6m]) / df_analysis.loc[mask, date_6m] * 100).round(0).astype(int)

if date_1y:
    mask = df_analysis[date_1y].notna()
    df_analysis.loc[mask, 'change_1y'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_1y]) / df_analysis.loc[mask, date_1y] * 100).round(0).astype(int)

if date_3y:
    mask = df_analysis[date_3y].notna()
    df_analysis.loc[mask, 'change_3y'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_3y]) / df_analysis.loc[mask, date_3y] * 100).round(0).astype(int)

if date_5y:
    mask = df_analysis[date_5y].notna()
    df_analysis.loc[mask, 'change_5y'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_5y]) / df_analysis.loc[mask, date_5y] * 100).round(0).astype(int)

if date_10y:
    mask = df_analysis[date_10y].notna()
    df_analysis.loc[mask, 'change_10y'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_10y]) / df_analysis.loc[mask, date_10y] * 100).round(0).astype(int)

if date_15y:
    mask = df_analysis[date_15y].notna()
    df_analysis.loc[mask, 'change_15y'] = ((df_analysis.loc[mask, latest_date] - df_analysis.loc[mask, date_15y]) / df_analysis.loc[mask, date_15y] * 100).round(0).astype(int)

df_analysis['ZCTA5CE20'] = df_analysis['RegionName'].astype(str).str.zfill(5)

# Filter out extreme outliers (keep reasonable range) - only filter where data exists
change_columns = [col for col in df_analysis.columns if col.startswith('change_')]
for col in change_columns:
    # Only apply filter where values exist (not NaN)
    mask = df_analysis[col].notna()
    outliers = mask & ((df_analysis[col] < -50) | (df_analysis[col] > 200))
    df_analysis = df_analysis[~outliers]

print(f"\n📊 Calculated price changes for {len(df_analysis):,} ZIP codes across all horizons")

# Load population data
try:
    pop_df = pd.read_csv('resources/populations/PopulationByZIP.csv', encoding='latin1', on_bad_lines='skip')
    if len(pop_df.columns) >= 3:
        pop_df.columns = ['zcta', 'name', 'population']
    else:
        pop_df.columns = ['zcta', 'population']
    pop_df['zcta'] = pop_df['zcta'].astype(str).str.zfill(5)
    pop_df['population'] = pd.to_numeric(pop_df['population'], errors='coerce').fillna(1000)
except Exception as e:
    print(f"⚠️  Warning: Could not load population data: {e}")
    print("Using default population values")
    pop_df = pd.DataFrame({'zcta': [], 'population': []})

# Load geometry for centroids
print("\n📍 Loading ZIP code geometries...")
gdf = gpd.read_file('resources/shapefiles/cb_2020_us_zcta520_500k.shp')
gdf['ZCTA5CE20'] = gdf['ZCTA5CE20'].astype(str).str.zfill(5)

# Calculate centroids
gdf['centroid'] = gdf.geometry.centroid
gdf['lat'] = gdf.centroid.y
gdf['lon'] = gdf.centroid.x

# Merge all data
merge_cols = ['ZCTA5CE20', 'City', 'State', latest_date] + change_columns
gdf_merged = gdf.merge(df_analysis[merge_cols], on='ZCTA5CE20', how='inner')
gdf_merged = gdf_merged.merge(pop_df[['zcta', 'name', 'population']],
                              left_on='ZCTA5CE20', right_on='zcta', how='left')

# Fill missing names
gdf_merged['name'] = gdf_merged['name'].fillna(gdf_merged['City'])
gdf_merged['name'] = gdf_merged['name'].fillna('Unknown')
gdf_merged['population'] = gdf_merged['population'].fillna(1000)

# Create city-state name
gdf_merged['city_state'] = gdf_merged.apply(
    lambda x: f"{x['City']}, {x['State']}" if pd.notna(x['City']) else x['name'], 
    axis=1
)

print(f"✅ Merged data for {len(gdf_merged):,} ZIP codes")

# Calculate population-based radius
conditions = [
    gdf_merged['population'] < 5000,
    gdf_merged['population'] < 20000,
    gdf_merged['population'] < 50000,
    gdf_merged['population'] < 100000,
    gdf_merged['population'] < 500000,
    gdf_merged['population'] >= 500000
]

choices = [3.0, 4.0, 6.0, 10.0, 16.0, 25.0]
gdf_merged['radius'] = np.select(conditions, choices, default=1.0)

# Calculate quintiles for all time horizons (embedded: 3m, 6m, 1y)
print(f"\n📊 Calculating quintiles for all time horizons...")
quintiles = {}

# Short-term horizons (embedded in HTML)
if 'change_3m' in gdf_merged.columns:
    quintiles['3m'] = np.nanpercentile(gdf_merged['change_3m'].values, [20, 40, 60, 80])
    print(f"   3-month:  {quintiles['3m'][0]:.0f}% | {quintiles['3m'][1]:.0f}% | {quintiles['3m'][2]:.0f}% | {quintiles['3m'][3]:.0f}%")

if 'change_6m' in gdf_merged.columns:
    quintiles['6m'] = np.nanpercentile(gdf_merged['change_6m'].values, [20, 40, 60, 80])
    print(f"   6-month:  {quintiles['6m'][0]:.0f}% | {quintiles['6m'][1]:.0f}% | {quintiles['6m'][2]:.0f}% | {quintiles['6m'][3]:.0f}%")

if 'change_1y' in gdf_merged.columns:
    quintiles['1y'] = np.nanpercentile(gdf_merged['change_1y'].values, [20, 40, 60, 80])
    print(f"   1-year:   {quintiles['1y'][0]:.0f}% | {quintiles['1y'][1]:.0f}% | {quintiles['1y'][2]:.0f}% | {quintiles['1y'][3]:.0f}%")

# Long-term horizons (lazy-loaded)
if 'change_3y' in gdf_merged.columns:
    quintiles['3y'] = np.nanpercentile(gdf_merged['change_3y'].values, [20, 40, 60, 80])
    print(f"   3-year:   {quintiles['3y'][0]:.0f}% | {quintiles['3y'][1]:.0f}% | {quintiles['3y'][2]:.0f}% | {quintiles['3y'][3]:.0f}%")

if 'change_5y' in gdf_merged.columns:
    quintiles['5y'] = np.nanpercentile(gdf_merged['change_5y'].values, [20, 40, 60, 80])
    print(f"   5-year:   {quintiles['5y'][0]:.0f}% | {quintiles['5y'][1]:.0f}% | {quintiles['5y'][2]:.0f}% | {quintiles['5y'][3]:.0f}%")

if 'change_10y' in gdf_merged.columns:
    quintiles['10y'] = np.nanpercentile(gdf_merged['change_10y'].values, [20, 40, 60, 80])
    print(f"   10-year:  {quintiles['10y'][0]:.0f}% | {quintiles['10y'][1]:.0f}% | {quintiles['10y'][2]:.0f}% | {quintiles['10y'][3]:.0f}%")

if 'change_15y' in gdf_merged.columns:
    quintiles['15y'] = np.nanpercentile(gdf_merged['change_15y'].values, [20, 40, 60, 80])
    print(f"   15-year:  {quintiles['15y'][0]:.0f}% | {quintiles['15y'][1]:.0f}% | {quintiles['15y'][2]:.0f}% | {quintiles['15y'][3]:.0f}%")

# Calculate quintiles for current price levels
current_prices = gdf_merged[latest_date].values
quintiles_price = np.percentile(current_prices, [20, 40, 60, 80])
print(f"\n📊 Current price quintiles:")
print(f"   20th percentile: ${quintiles_price[0]:,.0f}")
print(f"   40th percentile: ${quintiles_price[1]:,.0f}")
print(f"   60th percentile: ${quintiles_price[2]:,.0f}")
print(f"   80th percentile: ${quintiles_price[3]:,.0f}")

# Create zip data structures
# Short-term data (embedded in HTML)
zip_data = []
# Long-term data (lazy-loaded JSON)
long_term_data = {}

for _, row in gdf_merged.iterrows():
    # Clean up name
    name = str(row['city_state'])
    if name.startswith('zip code '):
        name = name.replace('zip code ', 'ZIP ')
    name = name.replace(', United States', '')

    zip_code = row['ZCTA5CE20']

    # Embedded data: location, current price, short-term changes (3M, 6M, 1Y)
    zip_obj = {
        'z': zip_code,
        'lat': round(row['lat'], 3),
        'lon': round(row['lon'], 3),
        'price': int(row[latest_date]),
        'r': round(row['radius'], 1),
        'pop': int(row['population']),
        'n': name
    }

    # Add short-term changes if available
    if 'change_3m' in row and pd.notna(row['change_3m']):
        zip_obj['p3m'] = int(row['change_3m'])
    if 'change_6m' in row and pd.notna(row['change_6m']):
        zip_obj['p6m'] = int(row['change_6m'])
    if 'change_1y' in row and pd.notna(row['change_1y']):
        zip_obj['p1y'] = int(row['change_1y'])

    zip_data.append(zip_obj)

    # Long-term data (separate file) - include whatever data exists for this ZIP
    long_term_obj = {}
    if 'change_3y' in row and pd.notna(row['change_3y']):
        long_term_obj['p3y'] = int(row['change_3y'])
    if 'change_5y' in row and pd.notna(row['change_5y']):
        long_term_obj['p5y'] = int(row['change_5y'])
    if 'change_10y' in row and pd.notna(row['change_10y']):
        long_term_obj['p10y'] = int(row['change_10y'])
    if 'change_15y' in row and pd.notna(row['change_15y']):
        long_term_obj['p15y'] = int(row['change_15y'])

    if long_term_obj:
        long_term_data[zip_code] = long_term_obj

# Sort by population (largest first) for better layering
zip_data = sorted(zip_data, key=lambda x: x['pop'], reverse=True)

print(f"\n📦 Generated data for {len(zip_data):,} ZIP codes")
print(f"   Embedded data: 3M, 6M, 1Y changes + current price")
print(f"   Long-term data: {len(long_term_data):,} ZIPs with 3Y, 5Y, 10Y, 15Y changes")

# Create HTML with all features
html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>US Home Price Changes - Year over Year</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Oracle:wght@400;500;600&display=swap');
body {{margin:0; padding:0; font-family:'Oracle',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}}
#map {{position:absolute; top:0; bottom:0; width:100%;}}

/* Control panel - integrated with legend */
.control-panel {{
    position:fixed;
    bottom:20px;
    left:20px;
    background:rgba(255,255,255,.95);
    padding:0 0 5px 0;
    z-index:1000;
    border:1px solid #e0e0e0;
    border-radius:4px;
    width:300px;
    font-family:'Oracle',sans-serif;
    font-size:11px;
}}

/* Search/controls section at top of panel */
.search-container {{
    padding:14px;
    border-bottom:1px solid #f0f0f0;
}}
.search-wrapper {{
    position:relative;
    display:flex;
    background:white;
    border:1px solid #ddd;
    border-radius:4px;
    margin:15px 20px 5px 20px;
}}
.search-box {{
    flex:1;
    padding:8px 10px;
    border:none;
    border-radius:4px 0 0 4px;
    font-size:11px;
    font-family:'Oracle',sans-serif;
    outline:none;
}}
.search-box:focus {{
    outline:none;
}}
.search-button {{
    padding:8px 14px;
    background:#0BB4FF;
    color:white;
    border:none;
    border-radius:0 4px 4px 0;
    cursor:pointer;
    font-size:10px;
    font-family:'Oracle',sans-serif;
    font-weight:600;
    transition:background 0.2s;
}}
.search-button:hover {{
    background:#0099dd;
}}

/* Search suggestions */
.search-suggestions {{
    position:absolute;
    top:100%;
    left:0;
    right:0;
    background:rgba(255,255,255,1);
    border:1px solid #ddd;
    border-top:none;
    max-height:200px;
    overflow-y:auto;
    display:none;
    border-radius:0 0 2px 2px;
    box-shadow:0 4px 12px rgba(0,0,0,0.15);
    z-index:10;
}}
.search-suggestions.active {{
    display:block;
}}
.suggestion-item {{
    padding:6px 8px;
    cursor:pointer;
    font-size:10px;
    font-family:'Oracle',sans-serif;
    border-bottom:1px solid #f0f0f0;
    background:white;
}}
.suggestion-item:hover,
.suggestion-item.selected {{
    background:#f5f5f5;
}}
.suggestion-item strong {{
    color:#0bb4ff;
}}

/* Compact toggles inside legend */
.toggle-row {{
    display:flex;
    gap:8px;
    margin:5px 10px 10px 10px;
}}
.compact-toggle {{
    flex:1;
    display:flex;
    background:#f0f0f0;
    border-radius:4px;
    padding:2px;
    cursor:pointer;
}}
.compact-toggle.disabled {{
    opacity:0.4;
    cursor:not-allowed;
}}
.toggle-opt {{
    flex:1;
    text-align:center;
    padding:6px 8px;
    font-size:9px;
    font-weight:600;
    letter-spacing:0.3px;
    color:#999;
    border-radius:3px;
    transition:all 0.2s ease;
}}
.toggle-opt.active {{
    background:#0BB4FF;
    color:white;
}}

/* Draw boundary button */
.draw-boundary-button {{
    padding:8px;
    background:white;
    border:1px solid #ddd;
    border-radius:4px;
    cursor:pointer;
    font-size:9px;
    font-family:'Oracle',sans-serif;
    font-weight:600;
    letter-spacing:0.3px;
    transition:all 0.2s;
    text-align:center;
    margin:0 10px 10px 10px;
}}
.draw-boundary-button:hover {{
    background:#f8f8f8;
    border-color:#999;
}}
.draw-boundary-button.active {{
    background:#67A275;
    color:white;
    border-color:#67A275;
}}
.draw-boundary-button.drawing {{
    background:#67A275;
    color:white;
    border-color:#67A275;
}}

/* Legend section inside control panel */
.legend {{
    padding:14px;
    font-size:10px;
    line-height:1.6;
    color:#000;
    font-family:'Oracle',sans-serif;
    position:relative;
}}
.legend.local-mode {{
    /* No special styling needed */
}}
.gradient-bar {{
    height:10px;
    background:linear-gradient(to right,
        #000000 0%, #000000 20%,
        #999999 20%, #999999 40%,
        #dadfce 40%, #dadfce 60%,
        #99ccff 60%, #99ccff 80%,
        #0bb4ff 80%, #0bb4ff 100%);
    margin:10px 10px 5px 10px;
    border:1px solid #ddd;
}}
.labels {{
    font-size:9px;
    position:relative;
    margin:2px 10px 0 10px;
    height:15px;
}}
.info-line {{
    font-size:10px;
    color:#666;
    margin:2px 10px;
}}
.zip-count {{
    font-size:9px;
    color:#999;
    margin:4px 10px 0 10px;
}}
.note {{
    font-size:9px;
    color:#999;
    margin:8px 10px 0 10px;
    line-height:1.3;
}}

/* Tooltip */
.custom-tooltip {{
    position:absolute;
    background:#000;
    color:#fff;
    padding:6px 10px;
    font-size:11px;
    pointer-events:none;
    z-index:9999;
    display:none;
    max-width:180px;
    line-height:1.3;
    border-radius:3px;
}}

/* Citation */
.citation {{
    position:fixed;
    bottom:10px;
    right:10px;
    background:rgba(255,255,255,0.9);
    padding:4px 8px;
    font-size:9px;
    font-variant:small-caps;
    letter-spacing:0.5px;
    z-index:999;
    border:1px solid #e0e0e0;
    border-radius:2px;
}}
.citation a {{
    color:#666;
    text-decoration:none;
    transition: color 0.2s ease;
}}
.citation a:hover {{
    color: #0bb4ff;
}}

/* Local mode glow effect for markers - but not in boundary mode */
.leaflet-pane.local-mode {{
    filter: drop-shadow(0 0 3px rgba(11, 180, 255, 0.3));
}}
.leaflet-pane.local-mode.boundary-active {{
    filter: none;
}}
</style>
</head>
<body>
<div id="map"></div>
<div class="custom-tooltip" id="tooltip"></div>
<div id="loadingOverlay" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:10000; align-items:center; justify-content:center;">
    <div style="background:white; padding:30px; border-radius:10px; box-shadow:0 4px 20px rgba(0,0,0,0.3); text-align:center;">
        <div style="width:50px; height:50px; border:4px solid #f3f3f3; border-top:4px solid #0bb4ff; border-radius:50%; animation:spin 1s linear infinite; margin:0 auto 15px;"></div>
        <div style="color:#3D3733; font-size:16px; font-weight:500;">Loading data...</div>
    </div>
</div>
<style>
@keyframes spin {{
    0% {{ transform: rotate(0deg); }}
    100% {{ transform: rotate(360deg); }}
}}
</style>
<div class="control-panel">
<div class="search-wrapper">
    <input type="text" class="search-box" id="searchBox" placeholder="Search ZIP or place name..." onkeyup="handleSearch(event)">
    <button class="search-button" onclick="performSearch()">Search</button>
    <div class="search-suggestions" id="suggestions"></div>
</div>
<div class="legend" id="legend">
<div class="toggle-row">
    <div class="compact-toggle" onclick="toggleDataMode()">
        <div class="toggle-opt" id="toggleLevels">LEVELS</div>
        <div class="toggle-opt active" id="toggleChanges">CHANGES</div>
    </div>
    <div class="compact-toggle" onclick="toggleView()">
        <div class="toggle-opt active" id="toggleGlobal">GLOBAL</div>
        <div class="toggle-opt" id="toggleLocal">LOCAL</div>
    </div>
</div>
<div class="toggle-row" id="horizonRow" style="opacity:1; pointer-events:auto;">
    <div class="compact-toggle" onclick="selectHorizon('3m')">
        <div class="toggle-opt" id="toggle3m">3M</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('6m')">
        <div class="toggle-opt" id="toggle6m">6M</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('1y')">
        <div class="toggle-opt active" id="toggle1y">1Y</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('long')">
        <div class="toggle-opt" id="toggleLong">MORE ▼</div>
    </div>
</div>
<div class="toggle-row" id="longHorizonRow" style="display:none;">
    <div class="compact-toggle" onclick="selectHorizon('3y')">
        <div class="toggle-opt" id="toggle3y">3Y</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('5y')">
        <div class="toggle-opt" id="toggle5y">5Y</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('10y')">
        <div class="toggle-opt" id="toggle10y">10Y</div>
    </div>
    <div class="compact-toggle" onclick="selectHorizon('15y')">
        <div class="toggle-opt" id="toggle15y">15Y</div>
    </div>
</div>
<div class="toggle-row">
    <div class="compact-toggle disabled" id="visualToggle" onclick="toggleVisualization()">
        <div class="toggle-opt active" id="toggleBubbles">BUBBLES</div>
        <div class="toggle-opt" id="toggleBoundaries">BOUNDARIES</div>
    </div>
</div>
<div id="boundaryHint" style="font-size:10px; color:#999; margin-top:2px; margin-bottom:8px; text-align:center;">Zoom in to enable boundary view</div>
<button class="draw-boundary-button" id="drawBoundaryBtn" onclick="toggleDrawMode()">
    <span id="drawBtnText">DRAW BOUNDARY</span>
</button>
<div class="gradient-bar"></div>
<div class="labels" style="font-size:8px; position:relative; margin-top:-2px;">
<span style="position:absolute; left:0;" id="q0">{quintiles.get('1y', [0,0,0,0])[0]:.0f}%</span>
<span style="position:absolute; left:20%;" id="q1">{quintiles.get('1y', [0,0,0,0])[1]:.0f}%</span>
<span style="position:absolute; left:40%;" id="q2">{quintiles.get('1y', [0,0,0,0])[2]:.0f}%</span>
<span style="position:absolute; left:60%;" id="q3">{quintiles.get('1y', [0,0,0,0])[3]:.0f}%</span>
<span style="position:absolute; right:0;" id="q4">+{int(max(15, quintiles.get('1y', [0,0,0,0])[3]+5))}%</span>
</div>
<div class="info-line" id="priceInfo">Year-over-Year Change</div>
<div class="info-line">Dates: {date_1y} to {latest_date}</div>
<div class="info-line">{len(zip_data):,} ZIP codes</div>
<div class="zip-count" id="zipCount"></div>
<div class="zip-count" id="popRange" style="display:none;"></div>
<div class="note" id="sizeNote">
Bubble size reflects population<br>
Zoom in for details
</div>
</div>
</div>
<div class="citation">
<a href="https://www.home-economics.us" target="_blank">www.home-economics.us</a>
</div>
<script>
// ZIP data
const zipData = {json.dumps(zip_data, separators=(',', ':'))};

// Global quintiles for all time horizons
const globalQuintiles = {{
    '3m': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('3m', [0,0,0,0])])},
    '6m': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('6m', [0,0,0,0])])},
    '1y': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('1y', [0,0,0,0])])},
    '3y': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('3y', [0,0,0,0])])},
    '5y': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('5y', [0,0,0,0])])},
    '10y': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('10y', [0,0,0,0])])},
    '15y': {json.dumps([int(q) if not np.isnan(q) else 0 for q in quintiles.get('15y', [0,0,0,0])])},
    'price': {json.dumps([int(quintiles_price[0]), int(quintiles_price[1]), int(quintiles_price[2]), int(quintiles_price[3])])}
}};

// Time horizon definitions
const horizons = {{
    '3m': {{label: '3 Months', field: 'p3m', embedded: true}},
    '6m': {{label: '6 Months', field: 'p6m', embedded: true}},
    '1y': {{label: '1 Year', field: 'p1y', embedded: true}},
    '3y': {{label: '3 Years', field: 'p3y', embedded: false}},
    '5y': {{label: '5 Years', field: 'p5y', embedded: false}},
    '10y': {{label: '10 Years', field: 'p10y', embedded: false}},
    '15y': {{label: '15 Years', field: 'p15y', embedded: false}}
}};

// State variables
let isLocalMode = false;
let dataMode = '1y'; // 'price' or a horizon key ('1y', '3y', etc)
let timeHorizon = '1y'; // Current time horizon when in change mode
let currentQuintiles = globalQuintiles['1y'];
let updateTimeout = null;
let markersLayer = null;
let selectedSuggestionIndex = -1;
let currentSuggestions = [];
let currentMinPop = null;
let currentMaxPop = null;

// Long-term data loading
let longTermData = null;
let longTermLoading = false;

// Drawing functionality
let drawnBoundary = null;
let drawControl = null;
let drawnItems = null;
let isDrawingMode = false;

// Boundary view functionality
let isBoundaryView = false;
let boundaryLayer = null;
let geometriesUltra = null;
let geometriesMedium = null;
let geometriesDetail = null;
let currentGeometryTier = null;
let geometriesLoading = false;

// Create search index for fast lookups
const searchIndex = zipData.map(z => ({{
    zip: z.z,
    name: z.n.toLowerCase(),
    nameOriginal: z.n,
    lat: z.lat,
    lon: z.lon,
    zipData: z,  // Store reference to full ZIP data
    pop: z.pop
}}));

// Initialize map
const map = L.map('map', {{
    center: [39.8283, -98.5795],
    zoom: 4,
    renderer: L.svg(),
    maxZoom: 18,
    attributionControl: false
}});

// Add base tiles (no labels)
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution: '',
    opacity: 0.9
}}).addTo(map);

// Create panes for layering
map.createPane('zipBoundaries');
map.getPane('zipBoundaries').style.zIndex = 450;
map.createPane('stateBoundaries');
map.getPane('stateBoundaries').style.zIndex = 500;
map.createPane('markerPane');
map.getPane('markerPane').style.zIndex = 600;

// Initialize drawing controls
drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

drawControl = new L.Control.Draw({{
    draw: {{
        polygon: {{
            shapeOptions: {{
                color: '#67A275',
                weight: 3,
                fillColor: '#C6DCCB',
                fillOpacity: 0.15
            }}
        }},
        rectangle: {{
            shapeOptions: {{
                color: '#67A275',
                weight: 3,
                fillColor: '#C6DCCB',
                fillOpacity: 0.15
            }}
        }},
        circle: false,
        circlemarker: false,
        marker: false,
        polyline: false
    }},
    edit: {{
        featureGroup: drawnItems,
        remove: true
    }}
}});

// Color function with dynamic quintiles
function getColor(value) {{
    if (value <= currentQuintiles[0]) return '#000000';
    if (value <= currentQuintiles[1]) return '#999999';
    if (value <= currentQuintiles[2]) return '#dadfce';
    if (value <= currentQuintiles[3]) return '#99ccff';
    return '#0bb4ff';
}}

// Calculate quintiles for a set of values
function calculateQuintiles(values) {{
    const sorted = [...values].sort((a, b) => a - b);

    // For small samples (< 5), use equal-width buckets from min to max
    if (sorted.length < 5) {{
        const min = sorted[0];
        const max = sorted[sorted.length - 1];
        const range = max - min;
        return [
            min + range * 0.2,
            min + range * 0.4,
            min + range * 0.6,
            min + range * 0.8
        ];
    }}

    return [
        sorted[Math.floor(sorted.length * 0.2)],
        sorted[Math.floor(sorted.length * 0.4)],
        sorted[Math.floor(sorted.length * 0.6)],
        sorted[Math.floor(sorted.length * 0.8)]
    ];
}}

// Get visible ZIPs
function getVisibleZips() {{
    const bounds = map.getBounds();
    return zipData.filter(z => bounds.contains([z.lat, z.lon]));
}}

// Update local quintiles
function updateLocalQuintiles() {{
    if (!isLocalMode) return;

    const visibleZips = getVisibleZips();
    const globalQ = dataMode === 'price' ? globalQuintiles['price'] : globalQuintiles[timeHorizon];

    if (visibleZips.length < 2) {{
        currentQuintiles = globalQ;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQ, visibleZips.length, null, null, false);
    }} else {{
        const values = visibleZips.map(z => getZipValue(z)).filter(v => v !== undefined);
        const populations = visibleZips.map(z => z.pop);
        currentQuintiles = calculateQuintiles(values);
        currentMinPop = Math.min(...populations);
        currentMaxPop = Math.max(...populations);
        const isSmallSample = visibleZips.length < 5;
        updateLegend(currentQuintiles, visibleZips.length, currentMinPop, currentMaxPop, isSmallSample);
    }}

    // Only update markers if not in boundary view mode
    if (!isBoundaryView) {{
        updateMarkers();
    }}
}}

// Update legend
function updateLegend(quintiles, zipCount, minPop, maxPop, isSmallSample = false) {{
    // Format labels based on data mode
    if (dataMode === 'price') {{
        // Price mode - show dollar amounts
        const fmt = (v) => '$' + (v >= 1000000 ? (v/1000000).toFixed(1) + 'M' : (v/1000).toFixed(0) + 'K');
        document.getElementById('q0').textContent = fmt(quintiles[0]);
        document.getElementById('q1').textContent = fmt(quintiles[1]);
        document.getElementById('q2').textContent = fmt(quintiles[2]);
        document.getElementById('q3').textContent = fmt(quintiles[3]);
        document.getElementById('q4').textContent = fmt(Math.max(quintiles[3] * 1.2, quintiles[3] + 100000));
    }} else {{
        // Change mode - show percentages
        document.getElementById('q0').textContent = quintiles[0].toFixed(0) + '%';
        document.getElementById('q1').textContent = quintiles[1].toFixed(0) + '%';
        document.getElementById('q2').textContent = quintiles[2].toFixed(0) + '%';
        document.getElementById('q3').textContent = quintiles[3].toFixed(0) + '%';
        document.getElementById('q4').textContent = '+' + Math.max(15, Math.ceil(quintiles[3] + 5)) + '%';
    }}

    if (isLocalMode) {{
        let zipCountText = `Analyzing ${{zipCount}} ZIP code${{zipCount === 1 ? '' : 's'}} in view`;
        if (isSmallSample && zipCount >= 2) {{
            zipCountText += ` <span style="color:#999; font-size:8px;">(small sample)</span>`;
        }}
        document.getElementById('zipCount').innerHTML = zipCountText;

        const popRangeEl = document.getElementById('popRange');
        if (minPop && maxPop) {{
            popRangeEl.textContent = `Population range: ${{minPop.toLocaleString()}} - ${{maxPop.toLocaleString()}}`;
            popRangeEl.style.display = 'block';
        }}
        document.getElementById('sizeNote').innerHTML = 'Bubble size reflects relative population<br>Zoom in for details';
    }} else {{
        document.getElementById('zipCount').innerHTML = '';
        document.getElementById('popRange').style.display = 'none';
        document.getElementById('sizeNote').innerHTML = 'Bubble size reflects population<br>Zoom in for details';
    }}
}}

// Toggle between global and local view
function toggleView() {{
    // Don't allow switching to GLOBAL if boundary is drawn
    if (drawnBoundary && isLocalMode) {{
        return;
    }}

    isLocalMode = !isLocalMode;

    const toggleGlobal = document.getElementById('toggleGlobal');
    const toggleLocal = document.getElementById('toggleLocal');

    if (isLocalMode) {{
        toggleGlobal.classList.remove('active');
        toggleLocal.classList.add('active');
    }} else {{
        toggleGlobal.classList.add('active');
        toggleLocal.classList.remove('active');
    }}

    const legend = document.getElementById('legend');
    legend.classList.toggle('local-mode', isLocalMode);

    const markerPane = map.getPane('markerPane');
    if (markerPane) {{
        markerPane.classList.toggle('local-mode', isLocalMode);
    }}

    if (isLocalMode) {{
        if (isBoundaryView) {{
            // Refresh boundary view with local quintiles
            loadAndShowBoundaries();
        }} else {{
            updateLocalQuintiles();
        }}
    }} else {{
        const globalQ = dataMode === 'price' ? globalQuintiles['price'] : globalQuintiles[timeHorizon];
        currentQuintiles = globalQ;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQ, zipData.length, null, null);
        if (isBoundaryView) {{
            // Refresh boundary view with global quintiles
            loadAndShowBoundaries();
        }} else {{
            updateMarkers();
        }}
    }}
}}

// Get value for a ZIP at current time horizon
function getZipValue(zip) {{
    if (dataMode === 'price') {{
        return zip.price;
    }}

    // Get change value for current horizon
    const field = horizons[timeHorizon].field;
    let value = zip[field];

    // If not embedded, check long-term data
    if (value === undefined && longTermData && longTermData[zip.z]) {{
        value = longTermData[zip.z][field];
    }}

    return value;
}}

// Show/hide loading indicator
function showLoading() {{
    const overlay = document.getElementById('loadingOverlay');
    overlay.style.display = 'flex';
}}

function hideLoading() {{
    const overlay = document.getElementById('loadingOverlay');
    overlay.style.display = 'none';
}}

// Load long-term data if needed
async function ensureLongTermData() {{
    if (longTermData || longTermLoading) return;

    longTermLoading = true;
    try {{
        const response = await fetch('long_term_changes.json');
        longTermData = await response.json();
        console.log(`Loaded long-term data for ${{Object.keys(longTermData).length}} ZIPs`);
    }} catch (error) {{
        console.error('Failed to load long-term data:', error);
        longTermData = {{}};
    }} finally {{
        longTermLoading = false;
    }}
}}

// Select time horizon
async function selectHorizon(horizon) {{
    if (horizon === 'long') {{
        // Toggle long-term horizons visibility
        const longRow = document.getElementById('longHorizonRow');
        const isVisible = longRow.style.display !== 'none';
        longRow.style.display = isVisible ? 'none' : 'flex';
        return;
    }}

    // Show loading indicator
    showLoading();

    try {{
        // Load long-term data if needed
        if (!horizons[horizon].embedded) {{
            await ensureLongTermData();
        }}

        timeHorizon = horizon;

        // Update all horizon toggles
        ['3m', '6m', '1y', '3y', '5y', '10y', '15y'].forEach(h => {{
            const el = document.getElementById(`toggle${{h}}`);
            if (el) {{
                el.classList.toggle('active', h === horizon);
            }}
        }});

        // Update currentQuintiles and refresh view
        if (isLocalMode) {{
            if (drawnBoundary) {{
                updateBoundaryQuintiles();
                if (isBoundaryView) {{
                    loadAndShowBoundaries();
                }}
            }} else {{
                updateLocalQuintiles();
                if (isBoundaryView) {{
                    loadAndShowBoundaries();
                }}
            }}
        }} else {{
            currentQuintiles = globalQuintiles[horizon];
            updateLegend(currentQuintiles, zipData.length, null, null);
            if (isBoundaryView) {{
                loadAndShowBoundaries();
            }} else {{
                updateMarkers();
            }}
        }}

        // Update info label
        const horizonLabel = horizons[horizon].label;
        document.getElementById('priceInfo').textContent = horizonLabel + ' Change';
    }} finally {{
        // Always hide loading indicator
        hideLoading();
    }}
}}

// Toggle between data modes (Price Levels vs Changes)
function toggleDataMode() {{
    const wasPrice = (dataMode === 'price');
    dataMode = wasPrice ? timeHorizon : 'price';

    const toggleLevels = document.getElementById('toggleLevels');
    const toggleChanges = document.getElementById('toggleChanges');
    const horizonRow = document.getElementById('horizonRow');

    if (dataMode === 'price') {{
        toggleLevels.classList.add('active');
        toggleChanges.classList.remove('active');
        // Disable horizon selectors (keep visible but grayed out)
        horizonRow.style.opacity = '0.3';
        horizonRow.style.pointerEvents = 'none';
        // Hide the long-term row if it was visible
        const longRow = document.getElementById('longHorizonRow');
        if (longRow.style.display !== 'none') {{
            longRow.style.display = 'none';
        }}
    }} else {{
        toggleLevels.classList.remove('active');
        toggleChanges.classList.add('active');
        // Enable horizon selectors
        horizonRow.style.opacity = '1';
        horizonRow.style.pointerEvents = 'auto';
    }}

    // Update price info label
    const priceInfo = document.getElementById('priceInfo');
    if (dataMode === 'price') {{
        priceInfo.textContent = 'Current Price Level';
    }} else {{
        priceInfo.textContent = horizons[timeHorizon].label + ' Change';
    }}

    // Recalculate based on current mode
    if (isLocalMode) {{
        if (drawnBoundary) {{
            updateBoundaryQuintiles();
            if (isBoundaryView) {{
                loadAndShowBoundaries();
            }}
        }} else {{
            updateLocalQuintiles();
            if (isBoundaryView) {{
                loadAndShowBoundaries();
            }}
        }}
    }} else {{
        currentQuintiles = dataMode === 'price' ? globalQuintiles['price'] : globalQuintiles[timeHorizon];
        updateLegend(currentQuintiles, zipData.length, null, null);
        if (isBoundaryView) {{
            loadAndShowBoundaries();
        }} else {{
            updateMarkers();
        }}
    }}
}}

// Toggle drawing mode
function toggleDrawMode() {{
    const drawBtn = document.getElementById('drawBoundaryBtn');
    const drawBtnText = document.getElementById('drawBtnText');

    // If boundary already drawn, clear it
    if (drawnBoundary) {{
        clearDrawnBoundary();
        return;
    }}

    // Toggle drawing mode
    isDrawingMode = !isDrawingMode;

    if (isDrawingMode) {{
        map.addControl(drawControl);
        drawBtn.classList.add('active');
        drawBtnText.textContent = 'Cancel Drawing';

        // Automatically start polygon drawing mode
        new L.Draw.Polygon(map, drawControl.options.draw.polygon).enable();
    }} else {{
        map.removeControl(drawControl);
        drawBtn.classList.remove('active');
        drawBtnText.textContent = 'Draw Boundary';
    }}
}}

// Clear drawn boundary
function clearDrawnBoundary() {{
    if (drawnBoundary) {{
        drawnItems.removeLayer(drawnBoundary);
        drawnBoundary = null;

        const drawBtn = document.getElementById('drawBoundaryBtn');
        const drawBtnText = document.getElementById('drawBtnText');
        drawBtn.classList.remove('drawing');
        drawBtnText.textContent = 'Draw Boundary';

        // Restore drop-shadow effect when boundary is cleared
        const markerPane = map.getPane('markerPane');
        if (markerPane) {{
            markerPane.classList.remove('boundary-active');
        }}

        // Re-enable GLOBAL toggle
        const toggleGlobal = document.getElementById('toggleGlobal');
        toggleGlobal.style.opacity = '';
        toggleGlobal.style.pointerEvents = '';

        // Return to normal local view if active
        if (isLocalMode) {{
            if (isBoundaryView) {{
                // If in boundary view, refresh boundaries without drawn boundary filter
                loadAndShowBoundaries();
            }} else {{
                // If in bubble view, update markers
                updateLocalQuintiles();
            }}
        }}
    }}
}}

// Toggle between bubbles and boundaries visualization
async function toggleVisualization() {{
    const visualToggle = document.getElementById('visualToggle');

    // Don't allow toggle if disabled (zoom < 6)
    if (visualToggle.classList.contains('disabled')) {{
        return;
    }}

    isBoundaryView = !isBoundaryView;

    const toggleBubbles = document.getElementById('toggleBubbles');
    const toggleBoundaries = document.getElementById('toggleBoundaries');

    if (isBoundaryView) {{
        toggleBubbles.classList.remove('active');
        toggleBoundaries.classList.add('active');

        // Load and show boundaries
        await loadAndShowBoundaries();
    }} else {{
        toggleBubbles.classList.add('active');
        toggleBoundaries.classList.remove('active');

        // Hide boundaries
        if (boundaryLayer) {{
            map.removeLayer(boundaryLayer);
            boundaryLayer = null;
        }}

        // Show markers
        updateMarkers();
    }}
}}

// Load boundary geometries if not already loaded
async function loadGeometries(tier) {{
    if (geometriesLoading) return;

    if (tier === 'ultra' && !geometriesUltra) {{
        geometriesLoading = true;
        try {{
            const response = await fetch('zip_geometries_ultra.json');
            geometriesUltra = await response.json();
            console.log('Loaded ultra-simplified geometries');
        }} catch (error) {{
            console.error('Failed to load ultra geometries:', error);
        }} finally {{
            geometriesLoading = false;
        }}
    }} else if (tier === 'medium' && !geometriesMedium) {{
        geometriesLoading = true;
        try {{
            const response = await fetch('zip_geometries_medium.json');
            geometriesMedium = await response.json();
            console.log('Loaded medium-simplified geometries');
        }} catch (error) {{
            console.error('Failed to load medium geometries:', error);
        }} finally {{
            geometriesLoading = false;
        }}
    }} else if (tier === 'detail' && !geometriesDetail) {{
        geometriesLoading = true;
        try {{
            const response = await fetch('zip_geometries_detail.json');
            geometriesDetail = await response.json();
            console.log('Loaded high-detail geometries');
        }} catch (error) {{
            console.error('Failed to load detail geometries:', error);
        }} finally {{
            geometriesLoading = false;
        }}
    }}
}}

// Load and display boundaries at appropriate detail level
async function loadAndShowBoundaries() {{
    const zoom = map.getZoom();

    // Determine which tier to use based on zoom level
    let tier;
    if (zoom >= 12) {{
        tier = 'detail';
    }} else if (zoom >= 9) {{
        tier = 'medium';
    }} else {{
        tier = 'ultra';
    }}

    // Load geometries if needed
    await loadGeometries(tier);

    // Get the appropriate geometry data
    let geometries;
    if (tier === 'detail') {{
        geometries = geometriesDetail;
    }} else if (tier === 'medium') {{
        geometries = geometriesMedium;
    }} else {{
        geometries = geometriesUltra;
    }}

    if (!geometries) {{
        console.error('Geometries not loaded');
        return;
    }}

    // Remove existing boundary layer if present
    if (boundaryLayer) {{
        map.removeLayer(boundaryLayer);
    }}

    // Create a map of ZIP codes to their data
    const zipMap = new Map(zipData.map(z => [z.z, z]));

    // Filter geometries based on drawn boundary or viewport
    const bounds = map.getBounds();
    let visibleGeometries;

    if (drawnBoundary && isLocalMode) {{
        // If boundary is drawn, only show ZIPs within the boundary
        const boundaryZips = getZipsInBoundary();
        const boundaryZipCodes = new Set(boundaryZips.map(z => z.z));

        visibleGeometries = geometries.features.filter(feature => {{
            const zipCode = feature.properties.zip;
            const zipInfo = zipMap.get(zipCode);

            if (!zipInfo) return false;

            // Filter out ZIPs without data for current horizon
            const value = getZipValue(zipInfo);
            if (value === undefined || value === null) return false;

            // Check if ZIP is in drawn boundary AND in viewport
            return boundaryZipCodes.has(zipCode) && bounds.contains([zipInfo.lat, zipInfo.lon]);
        }});
    }} else {{
        // Normal mode: filter by viewport only
        visibleGeometries = geometries.features.filter(feature => {{
            const zipCode = feature.properties.zip;
            const zipInfo = zipMap.get(zipCode);

            if (!zipInfo) return false;

            // Filter out ZIPs without data for current horizon
            const value = getZipValue(zipInfo);
            if (value === undefined || value === null) return false;

            // Check if ZIP centroid is in viewport
            return bounds.contains([zipInfo.lat, zipInfo.lon]);
        }});
    }}

    console.log(`Displaying ${{visibleGeometries.length}} boundaries (tier: ${{tier}})`);

    // If in local mode, calculate quintiles for visible ZIPs
    if (isLocalMode) {{
        const visibleZips = visibleGeometries.map(f => zipMap.get(f.properties.zip)).filter(z => z);
        const visibleValues = visibleZips.map(z => getZipValue(z)).filter(v => v !== undefined);

        if (visibleValues.length > 0) {{
            currentQuintiles = calculateQuintiles(visibleValues);

            // Update legend with local quintiles
            const minPop = Math.min(...visibleZips.map(z => z.pop));
            const maxPop = Math.max(...visibleZips.map(z => z.pop));
            currentMinPop = minPop;
            currentMaxPop = maxPop;

            updateLegend(currentQuintiles, visibleZips.length, minPop, maxPop);
        }}
    }}

    // Create GeoJSON layer with styling
    boundaryLayer = L.geoJSON({{
        type: 'FeatureCollection',
        features: visibleGeometries
    }}, {{
        style: function(feature) {{
            const zipCode = feature.properties.zip;
            const zipInfo = zipMap.get(zipCode);

            if (!zipInfo) {{
                return {{
                    fillColor: '#cccccc',
                    weight: 0.5,
                    opacity: 0.5,
                    color: '#ffffff',
                    fillOpacity: 0.6
                }};
            }}

            // Get value based on current data mode
            const value = getZipValue(zipInfo);
            const color = getColor(value);

            // At lower zoom levels, hide the white outlines for cleaner look
            const zoom = map.getZoom();
            const showOutline = zoom >= 8;

            return {{
                fillColor: color,
                weight: showOutline ? 1 : 0,
                opacity: showOutline ? 0.7 : 0,
                color: '#ffffff',
                fillOpacity: 0.7
            }};
        }},
        onEachFeature: function(feature, layer) {{
            const zipCode = feature.properties.zip;
            const zipInfo = zipMap.get(zipCode);

            if (zipInfo) {{
                layer.on({{
                    mouseover: function(e) {{
                        layer.setStyle({{
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 0.9
                        }});

                        const value = getZipValue(zipInfo);
                        let valueText;
                        if (value === undefined || value === null) {{
                            valueText = 'No data';
                        }} else if (dataMode === 'price') {{
                            valueText = `$${{Math.round(value).toLocaleString()}}`;
                        }} else {{
                            valueText = value >= 0 ? `+${{value}}%` : `${{value}}%`;
                        }}

                        const label = dataMode === 'price' ? 'Price' : horizons[timeHorizon].label;

                        const tooltip = document.getElementById('tooltip');
                        tooltip.innerHTML = `
                            <strong>${{zipCode}}</strong><br>
                            ${{zipInfo.n}}<br>
                            ${{label}}: ${{valueText}}<br>
                            Pop: ${{zipInfo.pop.toLocaleString()}}
                        `;
                        tooltip.style.display = 'block';
                        tooltip.style.left = (e.originalEvent.pageX + 10) + 'px';
                        tooltip.style.top = (e.originalEvent.pageY + 10) + 'px';
                    }},
                    mouseout: function() {{
                        layer.setStyle({{
                            weight: 1,
                            opacity: 0.7,
                            fillOpacity: 0.7
                        }});

                        document.getElementById('tooltip').style.display = 'none';
                    }},
                    mousemove: function(e) {{
                        const tooltip = document.getElementById('tooltip');
                        tooltip.style.left = (e.originalEvent.pageX + 10) + 'px';
                        tooltip.style.top = (e.originalEvent.pageY + 10) + 'px';
                    }}
                }});
            }}
        }},
        pane: 'zipBoundaries'
    }});

    boundaryLayer.addTo(map);
    currentGeometryTier = tier;

    // Hide markers when showing boundaries
    if (markersLayer) {{
        map.removeLayer(markersLayer);
    }}
}}

// Update boundary view when zoom or data changes
async function updateBoundaryView() {{
    if (!isBoundaryView) return;

    const zoom = map.getZoom();
    const newTier = zoom >= 9 ? 'medium' : 'ultra';

    // Reload boundaries if tier changed, layer missing, or viewport changed (pan)
    await loadAndShowBoundaries();
}}

// Get ZIPs within drawn boundary
function getZipsInBoundary() {{
    if (!drawnBoundary) return null;

    const layer = drawnBoundary;
    const filtered = zipData.filter(zip => {{
        const point = L.latLng(zip.lat, zip.lon);

        // For rectangles, just check bounds
        if (layer instanceof L.Rectangle) {{
            return layer.getBounds().contains(point);
        }}

        // For polygons, check bounds first, then precise check
        if (layer instanceof L.Polygon) {{
            if (!layer.getBounds().contains(point)) return false;
            return isPointInPolygon(point, layer);
        }}

        return false;
    }});

    console.log(`Found ${{filtered.length}} ZIPs in boundary`);
    return filtered;
}}

// Check if point is inside polygon
function isPointInPolygon(point, polygon) {{
    const latlngs = polygon.getLatLngs()[0];
    let inside = false;

    for (let i = 0, j = latlngs.length - 1; i < latlngs.length; j = i++) {{
        const xi = latlngs[i].lat, yi = latlngs[i].lng;
        const xj = latlngs[j].lat, yj = latlngs[j].lng;

        const intersect = ((yi > point.lng) !== (yj > point.lng))
            && (point.lat < (xj - xi) * (point.lng - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
    }}

    return inside;
}}

// Update quintiles for drawn boundary
function updateBoundaryQuintiles() {{
    if (!isLocalMode) return;

    const boundaryZips = getZipsInBoundary();
    const globalQ = dataMode === 'price' ? globalQuintiles['price'] : globalQuintiles[timeHorizon];
    console.log('updateBoundaryQuintiles called, found', boundaryZips ? boundaryZips.length : 0, 'ZIPs');

    if (!boundaryZips || boundaryZips.length < 2) {{
        currentQuintiles = globalQ;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQ, boundaryZips ? boundaryZips.length : 0, null, null, false);
    }} else {{
        const values = boundaryZips.map(z => getZipValue(z)).filter(v => v !== undefined);
        const populations = boundaryZips.map(z => z.pop);
        currentQuintiles = calculateQuintiles(values);
        currentMinPop = Math.min(...populations);
        currentMaxPop = Math.max(...populations);
        const isSmallSample = boundaryZips.length < 5;
        console.log('New quintiles:', currentQuintiles);
        console.log('Pop range:', currentMinPop, '-', currentMaxPop);
        updateLegend(currentQuintiles, boundaryZips.length, currentMinPop, currentMaxPop, isSmallSample);
    }}

    // Only update markers if not in boundary view mode
    if (!isBoundaryView) {{
        updateMarkers();
    }}
}}

// Custom tooltip
const tooltip = document.getElementById('tooltip');

// Update markers
function updateMarkers() {{
    const zoom = map.getZoom();
    const bounds = map.getBounds();

    if (markersLayer) {{
        map.removeLayer(markersLayer);
    }}

    // If boundary is drawn, only show ZIPs within boundary
    let visibleZips;
    if (drawnBoundary && isLocalMode) {{
        const boundaryZips = getZipsInBoundary();
        visibleZips = boundaryZips ? boundaryZips.filter(d => bounds.contains([d.lat, d.lon])) : [];
    }} else {{
        visibleZips = zipData.filter(d => bounds.contains([d.lat, d.lon]));
    }}

    // Filter out ZIPs that don't have data for the current mode/horizon
    visibleZips = visibleZips.filter(zip => {{
        const value = getZipValue(zip);
        return value !== undefined && value !== null;
    }});

    const markers = [];

    visibleZips.forEach(zip => {{
        let radius = zip.r;
        
        // Local mode population-relative sizing
        if (isLocalMode && currentMinPop !== null && currentMaxPop !== null && currentMaxPop > currentMinPop) {{
            const relativePosition = (zip.pop - currentMinPop) / (currentMaxPop - currentMinPop);

            // Use narrow range for boundary mode with small samples, wider range for regular local view
            if (drawnBoundary && visibleZips.length < 10) {{
                // Boundary mode with small sample: narrow range to prevent over-shrinking
                radius = 10 + (relativePosition * 12);  // 10-22px range
            }} else {{
                // Regular local view: wider range for better differentiation
                radius = 5 + (relativePosition * 20);   // 5-25px range
            }}
            
            if (zoom <= 3) {{
                radius = radius * 0.5;
            }} else if (zoom <= 5) {{
                radius = radius * 0.7;
            }} else if (zoom >= 9) {{
                radius = radius * 1.3;
            }}
        }} else {{
            // Global mode zoom scaling
            if (zoom <= 1) {{
                radius = radius * 0.02;
            }} else if (zoom === 2) {{
                radius = radius * 0.05;
            }} else if (zoom === 3) {{
                radius = radius * 0.15;
            }} else if (zoom === 4) {{
                radius = radius * 0.3;
            }} else if (zoom === 5) {{
                radius = radius * 0.5;
            }} else if (zoom === 6) {{
                radius = radius * 0.8;
            }} else if (zoom >= 7 && zoom < 9) {{
                radius = radius * 1.0;
            }} else if (zoom >= 9) {{
                radius = radius * 1.5;
            }}
        }}
        
        // Opacity based on zoom and population
        let fillOpacity = 0.8;
        if (zoom <= 1) {{
            fillOpacity = 0.4;
        }} else if (zoom === 2) {{
            if (zip.pop < 50000) fillOpacity = 0.3;
            else if (zip.pop < 75000) fillOpacity = 0.4;
            else fillOpacity = 0.5;
        }} else if (zoom === 3) {{
            if (zip.pop < 30000) fillOpacity = 0.4;
            else if (zip.pop < 50000) fillOpacity = 0.6;
            else fillOpacity = 0.75;
        }} else if (zoom === 4) {{
            if (zip.pop < 20000) fillOpacity = 0.5;
            else if (zip.pop < 50000) fillOpacity = 0.7;
        }} else if (zoom === 5) {{
            if (zip.pop < 5000) fillOpacity = 0.4;
            else if (zip.pop < 15000) fillOpacity = 0.6;
            else if (zip.pop < 30000) fillOpacity = 0.7;
        }}

        const value = getZipValue(zip);

        const marker = L.circleMarker([zip.lat, zip.lon], {{
            radius: radius,
            fillColor: getColor(value),
            color: 'transparent',
            weight: 0,
            opacity: 1,
            fillOpacity: fillOpacity,
            interactive: zoom >= 8,
            pane: 'markerPane'
        }});

        if (zoom >= 8) {{
            marker.zipData = zip;

            marker.on('mouseover', function(e) {{
                const data = e.target.zipData;
                const value = getZipValue(data);
                let dataLine;
                if (value === undefined || value === null) {{
                    dataLine = horizons[timeHorizon].label + ': No data';
                }} else if (dataMode === 'price') {{
                    dataLine = 'Price: $' + value.toLocaleString();
                }} else {{
                    const changeText = value >= 0 ? `+${{value}}%` : `${{value}}%`;
                    dataLine = horizons[timeHorizon].label + ': ' + changeText;
                }}
                tooltip.innerHTML = '<strong>' + data.z + '</strong><br>' +
                                  data.n + '<br>' +
                                  dataLine + '<br>' +
                                  data.pop.toLocaleString() + ' pop';
                tooltip.style.display = 'block';
            }});
            
            marker.on('mousemove', function(e) {{
                tooltip.style.left = (e.originalEvent.pageX + 10) + 'px';
                tooltip.style.top = (e.originalEvent.pageY - 28) + 'px';
            }});
            
            marker.on('mouseout', function() {{
                tooltip.style.display = 'none';
            }});
        }}
        
        markers.push(marker);
    }});
    
    markersLayer = L.layerGroup(markers);
    markersLayer.addTo(map);
}}

// Search functionality
function handleSearch(event) {{
    const query = event.target.value.trim();
    const suggestionsDiv = document.getElementById('suggestions');
    
    // Handle arrow keys
    if (event.key === 'ArrowDown') {{
        event.preventDefault();
        if (currentSuggestions.length > 0) {{
            selectedSuggestionIndex = Math.min(selectedSuggestionIndex + 1, currentSuggestions.length - 1);
            updateSelectedSuggestion();
        }}
        return;
    }} else if (event.key === 'ArrowUp') {{
        event.preventDefault();
        if (currentSuggestions.length > 0) {{
            selectedSuggestionIndex = Math.max(selectedSuggestionIndex - 1, -1);
            updateSelectedSuggestion();
        }}
        return;
    }} else if (event.key === 'Enter') {{
        event.preventDefault();
        if (selectedSuggestionIndex >= 0) {{
            goToLocation(currentSuggestions[selectedSuggestionIndex].zip);
        }} else {{
            performSearch();
        }}
        return;
    }} else if (event.key === 'Escape') {{
        suggestionsDiv.classList.remove('active');
        selectedSuggestionIndex = -1;
        return;
    }}
    
    // Reset selection when typing
    selectedSuggestionIndex = -1;
    
    if (query.length < 2) {{
        suggestionsDiv.classList.remove('active');
        currentSuggestions = [];
        return;
    }}
    
    showSuggestions(query);
}}

function showSuggestions(query) {{
    const queryLower = query.toLowerCase();
    const suggestionsDiv = document.getElementById('suggestions');
    
    // Find matches
    let matches = [];
    
    // Exact ZIP match
    if (/^\\d{{1,5}}$/.test(query)) {{
        matches = searchIndex.filter(item => item.zip.startsWith(query)).slice(0, 10);
    }}
    
    // Name match
    if (matches.length === 0) {{
        matches = searchIndex.filter(item => item.name.includes(queryLower)).slice(0, 10);
    }}
    
    currentSuggestions = matches;
    
    if (matches.length > 0) {{
        suggestionsDiv.innerHTML = matches.map((item, index) => 
            `<div class="suggestion-item" onclick="goToLocation('${{item.zip}}')" data-index="${{index}}">
                <strong>${{item.zip}}</strong> - ${{item.nameOriginal}}
            </div>`
        ).join('');
        suggestionsDiv.classList.add('active');
    }} else {{
        suggestionsDiv.classList.remove('active');
        currentSuggestions = [];
    }}
}}

function updateSelectedSuggestion() {{
    const items = document.querySelectorAll('.suggestion-item');
    items.forEach((item, index) => {{
        if (index === selectedSuggestionIndex) {{
            item.classList.add('selected');
        }} else {{
            item.classList.remove('selected');
        }}
    }});
}}

function performSearch() {{
    const query = document.getElementById('searchBox').value.trim();
    if (!query) return;
    
    const queryLower = query.toLowerCase();
    
    // Try exact ZIP match first
    let found = searchIndex.find(item => item.zip === query);
    
    // Try ZIP prefix
    if (!found && /^\\d{{1,5}}$/.test(query)) {{
        found = searchIndex.find(item => item.zip.startsWith(query));
    }}
    
    // Try name match
    if (!found) {{
        found = searchIndex.find(item => item.name.includes(queryLower));
    }}
    
    if (found) {{
        goToLocation(found.zip);
    }} else {{
        alert('Location not found. Try a ZIP code or city name.');
    }}
}}

function goToLocation(zipCode) {{
    const location = searchIndex.find(item => item.zip === zipCode);
    if (!location) return;
    
    // Close suggestions and update search box
    document.getElementById('suggestions').classList.remove('active');
    document.getElementById('searchBox').value = zipCode + ' - ' + location.nameOriginal;
    selectedSuggestionIndex = -1;
    currentSuggestions = [];
    
    // Fly to location with animation
    map.flyTo([location.lat, location.lon], 10, {{
        animate: true,
        duration: 1.5
    }});
    
    // Update markers after flight
    setTimeout(() => {{
        updateMarkers();

        // Show popup for the location
        const value = getZipValue(location.zipData);
        let valueText, label;

        if (value === undefined || value === null) {{
            valueText = 'No data';
            label = horizons[timeHorizon].label;
        }} else if (dataMode === 'price') {{
            valueText = '$' + value.toLocaleString();
            label = 'Price';
        }} else {{
            valueText = value >= 0 ? `+${{value}}%` : `${{value}}%`;
            label = horizons[timeHorizon].label;
        }}

        const popup = L.popup()
            .setLatLng([location.lat, location.lon])
            .setContent(`
                <strong>${{location.zip}}</strong><br>
                ${{location.nameOriginal}}<br>
                ${{label}}: ${{valueText}}<br>
                Population: ${{location.pop.toLocaleString()}}
            `)
            .openOn(map);
    }}, 1600);
}}

// Map event handlers
map.on('moveend zoomend', () => {{
    clearTimeout(updateTimeout);
    updateTimeout = setTimeout(() => {{
        // Enable/disable visualization toggle based on zoom
        const zoom = map.getZoom();
        const visualToggle = document.getElementById('visualToggle');
        const boundaryHint = document.getElementById('boundaryHint');
        if (zoom >= 6) {{
            visualToggle.classList.remove('disabled');
            if (boundaryHint) boundaryHint.style.display = 'none';
        }} else {{
            visualToggle.classList.add('disabled');
            if (boundaryHint) boundaryHint.style.display = 'block';

            // If boundary view is active but zoom < 6, switch back to bubbles
            if (isBoundaryView) {{
                toggleVisualization();
            }}
        }}

        // Update boundary view if active
        if (isBoundaryView) {{
            updateBoundaryView();
        }} else if (isLocalMode) {{
            if (drawnBoundary) {{
                updateBoundaryQuintiles();
            }} else {{
                updateLocalQuintiles();
            }}
        }} else {{
            updateMarkers();
        }}
    }}, 300);
}});

// Drawing event handlers
map.on(L.Draw.Event.CREATED, function(event) {{
    const layer = event.layer;

    // Remove any existing boundary
    if (drawnBoundary) {{
        drawnItems.removeLayer(drawnBoundary);
    }}

    drawnBoundary = layer;
    drawnItems.addLayer(layer);

    // Update UI
    const drawBtn = document.getElementById('drawBoundaryBtn');
    const drawBtnText = document.getElementById('drawBtnText');
    drawBtn.classList.add('drawing');
    drawBtn.classList.remove('active');
    drawBtnText.textContent = 'Clear Boundary';

    // Remove drop-shadow effect in boundary mode
    const markerPane = map.getPane('markerPane');
    if (markerPane) {{
        markerPane.classList.add('boundary-active');
    }}

    // Remove draw control
    map.removeControl(drawControl);
    isDrawingMode = false;

    // Disable GLOBAL toggle (drawn boundary is inherently local)
    const toggleGlobal = document.getElementById('toggleGlobal');
    const toggleLocal = document.getElementById('toggleLocal');
    toggleGlobal.style.opacity = '0.3';
    toggleGlobal.style.pointerEvents = 'none';

    // Enable local mode if not already
    if (!isLocalMode) {{
        toggleView();
    }} else {{
        // If in boundary view, refresh boundaries with new filtering
        if (isBoundaryView) {{
            updateBoundaryQuintiles();
            loadAndShowBoundaries();
        }} else {{
            updateBoundaryQuintiles();
        }}
    }}
}});

map.on(L.Draw.Event.DELETED, function(event) {{
    const layers = event.layers;
    layers.eachLayer(function(layer) {{
        if (layer === drawnBoundary) {{
            clearDrawnBoundary();
        }}
    }});
}});

// Multi-resolution state boundaries
let currentStateBoundaries = null;
let stateBoundariesLow = null;
let stateBoundariesMedium = null;
let stateBoundariesHigh = null;

async function loadStateBoundaries(resolution) {{
    const files = {{
        'low': 'state_boundaries_low.json',
        'medium': 'state_boundaries_medium.json',
        'high': 'state_boundaries_high.json'
    }};

    const response = await fetch(files[resolution]);
    return await response.json();
}}

async function updateStateBoundaries() {{
    const zoom = map.getZoom();
    let targetResolution;

    if (zoom <= 5) {{
        targetResolution = 'low';
    }} else if (zoom <= 8) {{
        targetResolution = 'medium';
    }} else {{
        targetResolution = 'high';
    }}

    // Load the appropriate resolution if not already loaded
    if (targetResolution === 'low' && !stateBoundariesLow) {{
        stateBoundariesLow = await loadStateBoundaries('low');
    }} else if (targetResolution === 'medium' && !stateBoundariesMedium) {{
        stateBoundariesMedium = await loadStateBoundaries('medium');
    }} else if (targetResolution === 'high' && !stateBoundariesHigh) {{
        stateBoundariesHigh = await loadStateBoundaries('high');
    }}

    // Remove existing layer
    if (currentStateBoundaries) {{
        map.removeLayer(currentStateBoundaries);
    }}

    // Add new layer with appropriate resolution
    const data = targetResolution === 'low' ? stateBoundariesLow :
                 targetResolution === 'medium' ? stateBoundariesMedium :
                 stateBoundariesHigh;

    const color = zoom >= 6 ? '#000000' : '#ffffff';

    currentStateBoundaries = L.geoJSON(data, {{
        style: {{
            color: color,
            weight: 1.5,
            opacity: 0.8,
            fillOpacity: 0,
            interactive: false
        }},
        pane: 'stateBoundaries'
    }}).addTo(map);
}}

// Initial load
updateStateBoundaries();

// Update on zoom
map.on('zoomend', updateStateBoundaries);

// Add labels on top
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    pane: 'markerPane',
    zIndex: 1000
}}).addTo(map);

// Initial render
updateMarkers();
</script>
</body>
</html>"""

# Write HTML file
output_file = 'output/ProMap.html'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html_content)

# Write long-term data JSON file
long_term_file = 'output/long_term_changes.json'
with open(long_term_file, 'w', encoding='utf-8') as f:
    json.dump(long_term_data, f, separators=(',', ':'))

print(f"\n✅ Successfully created: {output_file}")
print(f"📏 HTML file size: {len(html_content)/1024/1024:.1f} MB")
print(f"📏 Long-term data size: {len(json.dumps(long_term_data, separators=(',', ':')))/1024/1024:.1f} MB")
print(f"\n✅ Also created: {long_term_file}")
print("\n🎯 Features implemented:")
print("   • Multiple time horizons (3M, 6M, 1Y embedded; 3Y, 5Y, 10Y, 15Y lazy-loaded)")
print("   • Search with autocomplete for ZIP codes and place names")
print("   • Local view mode with dynamic quintile recalculation")
print("   • Boundary drawing for custom area analysis")
print("   • Boundary visualization toggle")
print("   • Smooth fly-to animations when searching")
print("   • Population-weighted bubble sizing")
print("   • State boundaries that change color with zoom")