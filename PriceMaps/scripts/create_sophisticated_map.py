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
df = pd.read_csv('/Users/azizsunderji/Dropbox/Home Economics/localmaps/PriceMaps/data/ZillowZip.csv')

# Get date columns
date_columns = [col for col in df.columns if '-' in col]
if not date_columns:
    print("❌ Error: No date columns found in data")
    exit(1)

# Get latest date
latest_date = date_columns[-1]
print(f"📅 Latest date: {latest_date}")

# Parse the date to find year-ago
date_obj = datetime.strptime(latest_date, "%Y-%m-%d")
target_year = date_obj.year - 1
target_month = date_obj.month

# Find year-ago date
year_ago_date = None
for col in date_columns:
    try:
        col_date = datetime.strptime(col, "%Y-%m-%d")
        if col_date.year == target_year and col_date.month == target_month:
            year_ago_date = col
            break
    except:
        continue

if not year_ago_date:
    # Find closest date from previous year
    year_ago_candidates = []
    for col in date_columns:
        try:
            col_date = datetime.strptime(col, "%Y-%m-%d")
            if col_date.year == target_year:
                year_ago_candidates.append((col, abs(col_date.month - target_month)))
        except:
            continue

    if year_ago_candidates:
        year_ago_candidates.sort(key=lambda x: x[1])
        year_ago_date = year_ago_candidates[0][0]

if not year_ago_date:
    print("❌ Error: Could not find year-ago data")
    exit(1)

print(f"📅 Year-ago date: {year_ago_date}")

# Calculate price appreciation
df_analysis = df[['RegionName', 'State', 'City', year_ago_date, latest_date]].copy()
df_analysis = df_analysis.dropna(subset=[year_ago_date, latest_date])
df_analysis['price_change_pct'] = ((df_analysis[latest_date] - df_analysis[year_ago_date]) / df_analysis[year_ago_date]) * 100
df_analysis['ZCTA5CE20'] = df_analysis['RegionName'].astype(str).str.zfill(5)
df_analysis = df_analysis[(df_analysis['price_change_pct'] >= -50) & (df_analysis['price_change_pct'] <= 100)]

print(f"📊 Calculated price changes for {len(df_analysis):,} ZIP codes")

# Load population data
pop_df = pd.read_csv('/Users/azizsunderji/Dropbox/Home Economics/Reference/Populations/PopulationByZIP.csv', encoding='latin1')
pop_df.columns = ['zcta', 'name', 'population']
pop_df['zcta'] = pop_df['zcta'].astype(str).str.zfill(5)
pop_df['population'] = pd.to_numeric(pop_df['population'], errors='coerce').fillna(1000)

# Load geometry for centroids
print("\n📍 Loading ZIP code geometries...")
gdf = gpd.read_file('/Users/azizsunderji/Dropbox/Home Economics/localmaps/PriceMaps/resources/shapefiles/cb_2020_us_zcta520_500k.shp')
gdf['ZCTA5CE20'] = gdf['ZCTA5CE20'].astype(str).str.zfill(5)

# Calculate centroids
gdf['centroid'] = gdf.geometry.centroid
gdf['lat'] = gdf.centroid.y
gdf['lon'] = gdf.centroid.x

# Merge all data
gdf_merged = gdf.merge(df_analysis[['ZCTA5CE20', 'price_change_pct', 'City', 'State']], 
                       on='ZCTA5CE20', how='inner')
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

# Calculate quintiles
price_values = gdf_merged['price_change_pct'].values
quintiles = np.percentile(price_values, [20, 40, 60, 80])
print(f"\n📊 Price change quintiles:")
print(f"   20th percentile: {quintiles[0]:.1f}%")
print(f"   40th percentile: {quintiles[1]:.1f}%")
print(f"   60th percentile: {quintiles[2]:.1f}%")
print(f"   80th percentile: {quintiles[3]:.1f}%")

# Create zip data
zip_data = []
for _, row in gdf_merged.iterrows():
    # Clean up name
    name = str(row['city_state'])
    if name.startswith('zip code '):
        name = name.replace('zip code ', 'ZIP ')
    name = name.replace(', United States', '')
    
    zip_data.append({
        'z': row['ZCTA5CE20'],
        'lat': round(row['lat'], 3),
        'lon': round(row['lon'], 3),
        'p': round(row['price_change_pct'], 1),
        'r': round(row['radius'], 1),
        'pop': int(row['population']),
        'n': name
    })

# Sort by population (largest first) for better layering
zip_data = sorted(zip_data, key=lambda x: x['pop'], reverse=True)

print(f"\n📦 Generated data for {len(zip_data):,} ZIP codes")

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
body {{margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}}
#map {{position:absolute; top:0; bottom:0; width:100%;}}

/* Search container */
.search-container {{
    position:fixed;
    top:20px;
    left:50%;
    transform:translateX(-50%);
    z-index:1001;
    display:flex;
    align-items:center;
    gap:10px;
}}
.search-wrapper {{
    position:relative;
    display:flex;
    background:rgba(255,255,255,0.95);
    border-radius:4px;
    box-shadow:0 2px 8px rgba(0,0,0,0.15);
    padding:2px;
}}
.search-box {{
    width:250px;
    padding:8px 12px;
    border:1px solid #ddd;
    border-radius:3px 0 0 3px;
    font-size:13px;
    outline:none;
    transition: all 0.3s ease;
}}
.search-box:focus {{
    outline:none;
    border-color:#0bb4ff;
    box-shadow: 0 0 5px rgba(11,180,255,0.3);
}}
.search-button {{
    padding:8px 16px;
    background:#0bb4ff;
    color:white;
    border:none;
    border-radius:0 3px 3px 0;
    cursor:pointer;
    font-size:13px;
    font-weight:500;
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
    background:white;
    border:1px solid #ddd;
    border-top:none;
    max-height:200px;
    overflow-y:auto;
    display:none;
    border-radius:0 0 4px 4px;
    box-shadow:0 4px 8px rgba(0,0,0,0.1);
}}
.search-suggestions.active {{
    display:block;
}}
.suggestion-item {{
    padding:8px 12px;
    cursor:pointer;
    font-size:12px;
    border-bottom:1px solid #f0f0f0;
}}
.suggestion-item:hover,
.suggestion-item.selected {{
    background:#f5f5f5;
}}
.suggestion-item strong {{
    color:#0bb4ff;
}}

/* View toggle button */
.view-toggle {{
    padding:6px 16px;
    background:white;
    border:2px solid #ddd;
    border-radius:20px;
    cursor:pointer;
    font-size:12px;
    font-weight:500;
    box-shadow:0 2px 4px rgba(0,0,0,0.1);
    transition: all 0.3s ease;
}}
.view-toggle:hover {{
    background:#f0f0f0;
}}
.view-toggle.local-active {{
    background:#0bb4ff;
    color:white;
    border-color:#0bb4ff;
}}

/* Draw boundary button */
.draw-boundary-button {{
    padding:6px 16px;
    background:white;
    border:2px solid #ddd;
    border-radius:20px;
    cursor:pointer;
    font-size:12px;
    font-weight:500;
    box-shadow:0 2px 4px rgba(0,0,0,0.1);
    transition: all 0.3s ease;
}}
.draw-boundary-button:hover {{
    background:#f0f0f0;
}}
.draw-boundary-button.active {{
    background:#67A275;
    color:white;
    border-color:#67A275;
}}
.draw-boundary-button.drawing {{
    background:#FEC439;
    color:#3D3733;
    border-color:#FEC439;
}}

/* Legend */
.legend {{
    position:fixed;
    bottom:20px;
    left:20px;
    background:rgba(255,255,255,.95);
    padding:18px 20px;
    z-index:1000;
    font-size:11px;
    line-height:1.5;
    color:#000;
    border:1px solid #e0e0e0;
    border-radius:4px;
    transition: all 0.3s ease;
}}
.legend.local-mode {{
    border-color:#0bb4ff;
    box-shadow: 0 0 15px rgba(11, 180, 255, 0.4);
}}
.local-badge {{
    position:absolute;
    top:-10px;
    right:10px;
    background:#0bb4ff;
    color:white;
    padding:2px 8px;
    font-size:9px;
    font-weight:bold;
    border-radius:10px;
    display:none;
}}
.legend.local-mode .local-badge {{
    display:block;
}}
.gradient-bar {{
    height:10px;
    background:linear-gradient(to right,
        #000000 0%, #000000 20%,
        #999999 20%, #999999 40%,
        #dadfce 40%, #dadfce 60%,
        #99ccff 60%, #99ccff 80%,
        #0bb4ff 80%, #0bb4ff 100%);
    margin:10px 0 5px 0;
    border:1px solid #ddd;
}}
.labels {{
    font-size:9px;
    position:relative;
    margin-top:2px;
    height:15px;
}}
.info-line {{
    font-size:10px;
    color:#666;
    margin:2px 0;
}}
.zip-count {{
    font-size:9px;
    color:#999;
    margin-top:4px;
}}
.note {{
    font-size:9px;
    color:#999;
    margin-top:8px;
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

/* Local mode glow effect for markers */
.leaflet-pane.local-mode {{
    filter: drop-shadow(0 0 3px rgba(11, 180, 255, 0.3));
}}
</style>
</head>
<body>
<div id="map"></div>
<div class="custom-tooltip" id="tooltip"></div>
<div class="search-container">
    <div class="search-wrapper">
        <input type="text" class="search-box" id="searchBox" placeholder="Search ZIP or place name..." onkeyup="handleSearch(event)">
        <button class="search-button" onclick="performSearch()">Search</button>
        <div class="search-suggestions" id="suggestions"></div>
    </div>
    <button class="view-toggle" id="viewToggle" onclick="toggleView()">
        <span id="toggleText">Switch to Local View</span>
    </button>
    <button class="draw-boundary-button" id="drawBoundaryBtn" onclick="toggleDrawMode()">
        <span id="drawBtnText">Draw Boundary</span>
    </button>
</div>
<div class="legend" id="legend">
<div class="local-badge">LOCAL VIEW</div>
<div class="gradient-bar"></div>
<div class="labels" style="font-size:8px; position:relative; margin-top:-2px;">
<span style="position:absolute; left:0;" id="q0">{quintiles[0]:.0f}%</span>
<span style="position:absolute; left:20%;" id="q1">{quintiles[1]:.0f}%</span>
<span style="position:absolute; left:40%;" id="q2">{quintiles[2]:.0f}%</span>
<span style="position:absolute; left:60%;" id="q3">{quintiles[3]:.0f}%</span>
<span style="position:absolute; right:0;" id="q4">+{int(max(15, quintiles[3]+5))}%</span>
</div>
<div class="info-line" id="priceInfo">Year-over-Year Change</div>
<div class="info-line">Dates: {year_ago_date} to {latest_date}</div>
<div class="info-line">{len(zip_data):,} ZIP codes</div>
<div class="zip-count" id="zipCount"></div>
<div class="zip-count" id="popRange" style="display:none;"></div>
<div class="note" id="sizeNote">
Bubble size reflects population<br>
Zoom in for details
</div>
</div>
<div class="citation">
<a href="https://www.home-economics.us" target="_blank">www.home-economics.us</a>
</div>
<script>
// ZIP data
const zipData = {json.dumps(zip_data, separators=(',', ':'))};

// Global quintiles
const globalQuintiles = [{quintiles[0]:.1f}, {quintiles[1]:.1f}, {quintiles[2]:.1f}, {quintiles[3]:.1f}];

// State variables
let isLocalMode = false;
let currentQuintiles = globalQuintiles;
let updateTimeout = null;
let markersLayer = null;
let selectedSuggestionIndex = -1;
let currentSuggestions = [];
let currentMinPop = null;
let currentMaxPop = null;

// Drawing functionality
let drawnBoundary = null;
let drawControl = null;
let drawnItems = null;
let isDrawingMode = false;

// Create search index for fast lookups
const searchIndex = zipData.map(z => ({{
    zip: z.z,
    name: z.n.toLowerCase(),
    nameOriginal: z.n,
    lat: z.lat,
    lon: z.lon,
    price: z.p,
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
map.createPane('stateBoundaries');
map.getPane('stateBoundaries').style.zIndex = 450;
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
                weight: 3
            }}
        }},
        rectangle: {{
            shapeOptions: {{
                color: '#67A275',
                weight: 3
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

    // Use light grey for middle quintile when boundary is drawn, light green otherwise
    if (value <= currentQuintiles[2]) {{
        return (drawnBoundary && isLocalMode) ? '#f8f8f8' : '#C6DCCB';
    }}

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
    if (visibleZips.length < 2) {{
        currentQuintiles = globalQuintiles;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQuintiles, visibleZips.length, null, null, false);
    }} else {{
        const prices = visibleZips.map(z => z.p);
        const populations = visibleZips.map(z => z.pop);
        currentQuintiles = calculateQuintiles(prices);
        currentMinPop = Math.min(...populations);
        currentMaxPop = Math.max(...populations);
        const isSmallSample = visibleZips.length < 5;
        updateLegend(currentQuintiles, visibleZips.length, currentMinPop, currentMaxPop, isSmallSample);
    }}
    updateMarkers();
}}

// Update legend
function updateLegend(quintiles, zipCount, minPop, maxPop, isSmallSample = false) {{
    document.getElementById('q0').textContent = quintiles[0].toFixed(0) + '%';
    document.getElementById('q1').textContent = quintiles[1].toFixed(0) + '%';
    document.getElementById('q2').textContent = quintiles[2].toFixed(0) + '%';
    document.getElementById('q3').textContent = quintiles[3].toFixed(0) + '%';
    document.getElementById('q4').textContent = '+' + Math.max(15, Math.ceil(quintiles[3] + 5)) + '%';

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
    isLocalMode = !isLocalMode;

    const toggle = document.getElementById('viewToggle');
    const toggleText = document.getElementById('toggleText');
    toggle.classList.toggle('local-active', isLocalMode);
    toggleText.textContent = isLocalMode ? 'Switch to Global View' : 'Switch to Local View';

    const legend = document.getElementById('legend');
    legend.classList.toggle('local-mode', isLocalMode);

    const markerPane = map.getPane('markerPane');
    if (markerPane) {{
        markerPane.classList.toggle('local-mode', isLocalMode);
    }}

    if (isLocalMode) {{
        updateLocalQuintiles();
    }} else {{
        currentQuintiles = globalQuintiles;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQuintiles, zipData.length, null, null);
        updateMarkers();
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

        // Return to normal local view if active
        if (isLocalMode) {{
            updateLocalQuintiles();
        }}
    }}
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
    console.log('updateBoundaryQuintiles called, found', boundaryZips ? boundaryZips.length : 0, 'ZIPs');

    if (!boundaryZips || boundaryZips.length < 2) {{
        currentQuintiles = globalQuintiles;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQuintiles, boundaryZips ? boundaryZips.length : 0, null, null, false);
    }} else {{
        const prices = boundaryZips.map(z => z.p);
        const populations = boundaryZips.map(z => z.pop);
        currentQuintiles = calculateQuintiles(prices);
        currentMinPop = Math.min(...populations);
        currentMaxPop = Math.max(...populations);
        const isSmallSample = boundaryZips.length < 5;
        console.log('New quintiles:', currentQuintiles);
        console.log('Pop range:', currentMinPop, '-', currentMaxPop);
        updateLegend(currentQuintiles, boundaryZips.length, currentMinPop, currentMaxPop, isSmallSample);
    }}
    updateMarkers();
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

    const markers = [];
    
    visibleZips.forEach(zip => {{
        let radius = zip.r;
        
        // Local mode population-relative sizing
        if (isLocalMode && currentMinPop !== null && currentMaxPop !== null && currentMaxPop > currentMinPop) {{
            const relativePosition = (zip.pop - currentMinPop) / (currentMaxPop - currentMinPop);
            radius = 10 + (relativePosition * 12);
            
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
        
        const marker = L.circleMarker([zip.lat, zip.lon], {{
            radius: radius,
            fillColor: getColor(zip.p),
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
                const changeText = data.p >= 0 ? `+${{data.p}}%` : `${{data.p}}%`;
                tooltip.innerHTML = '<strong>' + data.z + '</strong><br>' +
                                  data.n + '<br>' +
                                  'YoY Change: ' + changeText + '<br>' +
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
        const changeText = location.price >= 0 ? `+${{location.price}}%` : `${{location.price}}%`;
        const popup = L.popup()
            .setLatLng([location.lat, location.lon])
            .setContent(`
                <strong>${{location.zip}}</strong><br>
                ${{location.nameOriginal}}<br>
                Year-over-Year Change: ${{changeText}}<br>
                Population: ${{location.pop.toLocaleString()}}
            `)
            .openOn(map);
    }}, 1600);
}}

// Map event handlers
map.on('moveend zoomend', () => {{
    clearTimeout(updateTimeout);
    updateTimeout = setTimeout(() => {{
        if (isLocalMode) {{
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

    // Remove draw control
    map.removeControl(drawControl);
    isDrawingMode = false;

    // Enable local mode if not already
    if (!isLocalMode) {{
        toggleView();
    }} else {{
        updateBoundaryQuintiles();
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

// Add state boundaries
fetch('https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json')
    .then(response => response.json())
    .then(data => {{
        const stateBoundariesLayer = L.geoJSON(data, {{
            style: {{
                color: '#ffffff',
                weight: 1.5,
                opacity: 0.8,
                fillOpacity: 0,
                interactive: false
            }},
            pane: 'stateBoundaries'
        }}).addTo(map);
        
        map.on('zoomend', function() {{
            const zoom = map.getZoom();
            const newColor = zoom >= 6 ? '#000000' : '#ffffff';
            stateBoundariesLayer.setStyle({{ color: newColor }});
        }});
    }});

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
output_file = '/Users/azizsunderji/Dropbox/Home Economics/localmaps/PriceMaps/output/ProMap.html'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"\n✅ Successfully created: {output_file}")
print(f"📏 File size: {len(html_content)/1024/1024:.1f} MB")
print("\n🎯 Features implemented:")
print("   • Search with autocomplete for ZIP codes and place names")
print("   • Local view mode with dynamic quintile recalculation")
print("   • Boundary drawing for custom area analysis")
print("   • Smooth fly-to animations when searching")
print("   • Population-weighted bubble sizing")
print("   • Responsive tooltips with year-over-year change data")
print("   • State boundaries that change color with zoom")