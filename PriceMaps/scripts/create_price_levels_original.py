import pandas as pd
import geopandas as gpd
import numpy as np
import json
import warnings
warnings.filterwarnings('ignore')

print("🎯 Creating Interactive Bubble Map with Search and Local View Features")
print("="*70)

# Load the Zillow data
print("📊 Loading Zillow price data...")
import os
from pathlib import Path
script_dir = Path(__file__).parent.parent
data_file = script_dir / "data" / "ZillowZip.csv"
df = pd.read_csv(data_file)

# Get date columns - check both formats (M/D/YY from local, YYYY-MM-DD from download)
date_columns = [col for col in df.columns if ('/' in col or '-' in col)]
if date_columns:
    # The last column should be the most recent date
    latest_date = date_columns[-1]
    # Parse the date based on format
    if '/' in latest_date:
        month, day, year = latest_date.split('/')
        latest_year = int('20' + year) if len(year) == 2 else int(year)
        latest_month = month.zfill(2)
    elif '-' in latest_date:
        # YYYY-MM-DD format from downloaded data
        year, month, day = latest_date.split('-')
        latest_year = int(year)
        latest_month = month

print(f"📅 Latest date: {latest_date}")

# Get current price levels
df_analysis = df[['RegionName', 'State', 'City', latest_date]].copy()
df_analysis = df_analysis.dropna(subset=[latest_date])
df_analysis['price_level'] = df_analysis[latest_date]
df_analysis['ZCTA5CE20'] = df_analysis['RegionName'].astype(str).str.zfill(5)

# Remove extreme outliers if any
df_analysis = df_analysis[(df_analysis['price_level'] >= 10000) & (df_analysis['price_level'] <= 10000000)]

print(f"📍 ZIP codes with price data: {len(df_analysis):,}")
print(f"💰 Price range: ${df_analysis['price_level'].min():,.0f} to ${df_analysis['price_level'].max():,.0f}")
print(f"💰 Median price: ${df_analysis['price_level'].median():,.0f}")

# Load population data
print("\n👥 Loading population data...")
pop_df = pd.read_csv(script_dir / 'resources' / 'populations' / 'PopulationByZIP.csv', encoding='latin1')
pop_df.columns = ['zcta', 'name', 'population']
pop_df['zcta'] = pop_df['zcta'].astype(str).str.zfill(5)
pop_df['population'] = pd.to_numeric(pop_df['population'], errors='coerce').fillna(1000)

# Load ZIP code shapefile for coordinates
print("\n🗺️ Loading ZIP code coordinates...")
gdf = gpd.read_file(script_dir / 'resources' / 'shapefiles' / 'cb_2020_us_zcta520_500k.shp')

# Get centroids
gdf['centroid'] = gdf.geometry.centroid
gdf['lon'] = gdf.centroid.x
gdf['lat'] = gdf.centroid.y

# Merge all data together
gdf_merged = gdf[['ZCTA5CE20', 'lon', 'lat']].merge(
    df_analysis[['ZCTA5CE20', 'price_level', 'State', 'City']], 
    on='ZCTA5CE20', 
    how='inner'
).merge(
    pop_df[['zcta', 'name', 'population']], 
    left_on='ZCTA5CE20', 
    right_on='zcta', 
    how='left'
)

gdf_merged['population'] = pd.to_numeric(gdf_merged['population'], errors='coerce').fillna(1000)
print(f"✅ Merged {len(gdf_merged):,} ZIP codes with all data")

# Calculate population-based radius
gdf_merged['pop_log'] = np.log10(gdf_merged['population'] + 1)
max_pop_log = gdf_merged['pop_log'].max()
min_pop_log = gdf_merged['pop_log'].min()
# Create EXTREME differences - make small towns almost invisible
gdf_merged['radius_linear'] = (gdf_merged['pop_log'] - min_pop_log) / (max_pop_log - min_pop_log)

# Use population thresholds for dramatic sizing
conditions = [
    gdf_merged['population'] < 5000,      # Very small towns
    gdf_merged['population'] < 20000,     # Small towns  
    gdf_merged['population'] < 50000,     # Medium towns
    gdf_merged['population'] < 100000,    # Small cities
    gdf_merged['population'] < 500000,    # Medium cities
    gdf_merged['population'] >= 500000    # Large cities
]

choices = [
    3.0,   # Tiny for < 5k
    4.0,   # Small for < 20k
    6.0,   # Medium for < 50k
    10.0,  # Larger for < 100k
    16.0,  # Big for < 500k
    25.0   # Huge for 500k+
]

gdf_merged['radius'] = np.select(conditions, choices, default=1.0)

# Calculate GLOBAL quintile breakpoints for price levels
price_values = gdf_merged['price_level'].values
global_quintiles = np.percentile(price_values, [20, 40, 60, 80])
print(f"\n📊 Global price level quintiles:")
print(f"   20th percentile: ${global_quintiles[0]:,.0f}")
print(f"   40th percentile: ${global_quintiles[1]:,.0f}")
print(f"   60th percentile: ${global_quintiles[2]:,.0f}")
print(f"   80th percentile: ${global_quintiles[3]:,.0f}")

# Create zip data
zip_data = []
for _, row in gdf_merged.iterrows():
    # Clean up name
    name = str(row.get('name', '')).strip()
    if name == 'nan' or name == '':
        # Try to use City, State
        city = str(row.get('City', '')).strip()
        state = str(row.get('State', '')).strip()
        if city and city != 'nan':
            name = f"{city}, {state}"
        else:
            name = f"ZIP {row['ZCTA5CE20']}"
    
    zip_data.append({
        'z': row['ZCTA5CE20'],
        'lat': round(row['lat'], 3),
        'lon': round(row['lon'], 3),
        'p': int(row['price_level']),  # Store as integer for cleaner display
        'r': round(row['radius'], 1),
        'pop': int(row['population']),
        'n': name
    })

print(f"  ✓ Created {len(zip_data):,} ZIP code bubbles")

# Format quintiles for display
q0_str = f"${int(global_quintiles[0]/1000)}k"
q1_str = f"${int(global_quintiles[1]/1000)}k"
q2_str = f"${int(global_quintiles[2]/1000)}k"
q3_str = f"${int(global_quintiles[3]/1000)}k"
q4_str = f"${int(global_quintiles[3]/1000 * 1.2)}k+"  # 20% above 80th percentile

# Create the HTML with local view feature
html_content = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>US Home Price Levels - All ZIP Codes</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{margin:0;padding:0;box-sizing:border-box}}
body {{font-family:'Oracle',Helvetica,Arial,sans-serif}}
#map {{height:100vh;width:100%}}

/* Search box */
.search-container {{
    position:fixed;
    top:20px;
    left:80px;
    z-index:1000;
    width:280px;
}}
.search-box {{
    width:100%;
    padding:10px 40px 10px 12px;
    font-size:13px;
    border:1px solid #e0e0e0;
    border-radius:4px;
    background:rgba(255,255,255,.95);
    transition: all 0.3s ease;
}}
.search-box:focus {{
    outline:none;
    border-color:#0bb4ff;
    box-shadow: 0 0 5px rgba(11,180,255,0.3);
}}
.search-button {{
    position:absolute;
    right:2px;
    top:2px;
    padding:8px 12px;
    background:#0bb4ff;
    color:white;
    border:none;
    border-radius:3px;
    cursor:pointer;
    font-size:12px;
}}
.search-button:hover {{
    background:#0999dd;
}}
.search-suggestions {{
    position:absolute;
    top:100%;
    left:0;
    right:0;
    background:white;
    border:1px solid #e0e0e0;
    border-top:none;
    border-radius:0 0 4px 4px;
    max-height:200px;
    overflow-y:auto;
    display:none;
    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
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
.suggestion-item:hover, .suggestion-item.selected {{
    background:#f5f5f5;
}}
.suggestion-item strong {{
    color:#0bb4ff;
}}

/* Toggle button */
.view-toggle {{
    position:fixed;
    top:20px;
    right:20px;
    background:rgba(255,255,255,.95);
    padding:8px 16px;
    z-index:1000;
    font-size:12px;
    border:1px solid #e0e0e0;
    border-radius:4px;
    cursor:pointer;
    transition: all 0.3s ease;
}}
.view-toggle:hover {{
    background:#f0f0f0;
}}
.view-toggle.local-active {{
    background:#0bb4ff;
    color:white;
    border-color:#0bb4ff;
    box-shadow: 0 0 12px rgba(11, 180, 255, 0.5);
}}

/* Legend */
.legend {{
    position:fixed;
    bottom:20px;
    left:20px;
    background:rgba(255,255,255,.9);
    padding:20px 24px;
    z-index:1000;
    font-size:11px;
    line-height:1.6;
    color:#000;
    border:1px solid #e0e0e0;
    transition: all 0.3s ease;
}}
.legend.local-mode {{
    border-color:#0bb4ff;
    box-shadow: 0 0 15px rgba(11, 180, 255, 0.4);
}}
.local-badge {{
    display:none;
    background:#0bb4ff;
    color:white;
    padding:3px 8px;
    border-radius:3px;
    font-size:9px;
    font-weight:bold;
    margin-bottom:8px;
    text-align:center;
    box-shadow: 0 0 8px rgba(11, 180, 255, 0.5);
}}
.legend.local-mode .local-badge {{
    display:block;
}}
.gradient-bar {{
    height:12px;
    background:linear-gradient(to right,
        #000000 0%, #000000 20%,      /* Black */
        #999999 20%, #999999 40%,     /* Light gray */  
        #dadfce 40%, #dadfce 60%,     /* Cream */
        #99ccff 60%, #99ccff 80%,     /* Pale blue */
        #0bb4ff 80%, #0bb4ff 100%);   /* Bright blue */
    margin:12px 0 8px 0;
    border:1px solid #ddd;
}}
.labels {{
    display:flex;
    justify-content:space-around;
    font-size:9px;
    color:#666;
    margin-bottom:16px;
    text-align:center;
}}
.info-line {{
    font-size:10px;
    color:#333;
    margin:3px 0;
}}
.note {{
    font-size:9px;
    color:#666;
    margin-top:12px;
    padding-top:12px;
    border-top:1px solid #e0e0e0;
}}
.zip-count {{
    display:none;
    font-size:9px;
    color:#666;
    margin-top:4px;
}}
.legend.local-mode .zip-count {{
    display:block;
}}
#popRange {{
    font-size:9px;
    color:#0bb4ff;
    font-weight:500;
}}

/* Custom tooltip */
.custom-tooltip {{
    position: absolute;
    background: #000;
    color: #fff;
    padding: 6px 10px;
    font-size: 11px;
    pointer-events: none;
    z-index: 9999;
    display: none;
    max-width: 180px;
    line-height: 1.3;
}}
.custom-tooltip strong {{
    font-weight: 500;
}}

/* Citation */
.citation {{
    position: fixed;
    bottom: 10px;
    right: 10px;
    background: rgba(255,255,255,0.9);
    padding: 4px 8px;
    font-size: 9px;
    font-variant: small-caps;
    letter-spacing: 0.5px;
    z-index: 999;
    border: 1px solid #e0e0e0;
    border-radius: 2px;
}}
.citation a {{
    color: #666;
    text-decoration: none;
    transition: color 0.2s ease;
}}
.citation a:hover {{
    color: #0bb4ff;
}}

/* Glow effect for markers in local mode */
.leaflet-pane.local-mode svg {{
    filter: drop-shadow(0 0 3px rgba(11, 180, 255, 0.4));
}}

/* No animations for better performance */
</style>
</head>
<body>
<div id="map"></div>
<div class="custom-tooltip" id="tooltip"></div>
<div class="search-container">
    <input type="text" class="search-box" id="searchBox" placeholder="Search ZIP or place name..." onkeyup="handleSearch(event)">
    <button class="search-button" onclick="performSearch()">Search</button>
    <div class="search-suggestions" id="suggestions"></div>
</div>
<button class="view-toggle" id="viewToggle" onclick="toggleView()">
    <span id="toggleText">Switch to Local View</span>
</button>
<div class="legend" id="legend">
<div class="local-badge">LOCAL VIEW</div>
<div class="gradient-bar"></div>
<div class="labels" style="font-size:8px; position:relative; margin-top:-2px;">
<span style="position:absolute; left:0;" id="q0">{q0_str}</span>
<span style="position:absolute; left:20%;" id="q1">{q1_str}</span>
<span style="position:absolute; left:40%;" id="q2">{q2_str}</span>
<span style="position:absolute; left:60%;" id="q3">{q3_str}</span>
<span style="position:absolute; right:0;" id="q4">{q4_str}</span>
</div>
<div class="info-line" id="priceInfo">Home Prices as of {latest_date}</div>
<div class="info-line">{len(zip_data):,} ZIP codes</div>
<div class="zip-count" id="zipCount">Analyzing 0 ZIP codes in view</div>
<div class="zip-count" id="popRange" style="display:none;">Population range: 0 - 0</div>
<div class="note" id="sizeNote">
Bubble size reflects population<br>
Zoom in for details
</div>
</div>
<div class="citation">
<a href="https://www.home-economics.us" target="_blank">www.home-economics.us</a>
</div>
<script>
// Data
const zipData = {json.dumps(zip_data, separators=(',', ':'))};

// Global quintiles (for reference)
const globalQuintiles = [{global_quintiles[0]:.0f}, {global_quintiles[1]:.0f}, {global_quintiles[2]:.0f}, {global_quintiles[3]:.0f}];

// Current mode
let isLocalMode = false;
let currentQuintiles = globalQuintiles;
let updateTimeout = null;

// Create search index
const searchIndex = zipData.map(z => ({{
    zip: z.z,
    name: z.n.toLowerCase(),
    nameOriginal: z.n,
    lat: z.lat,
    lon: z.lon,
    price: z.p,
    pop: z.pop
}}));

// Track selected suggestion index
let selectedSuggestionIndex = -1;
let currentSuggestions = [];

// Search functionality with keyboard navigation
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
        if (selectedSuggestionIndex >= 0 && selectedSuggestionIndex < currentSuggestions.length) {{
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

function updateSelectedSuggestion() {{
    const items = document.querySelectorAll('.suggestion-item');
    items.forEach((item, index) => {{
        if (index === selectedSuggestionIndex) {{
            item.classList.add('selected');
            // Update search box with selected item's text
            const selectedSuggestion = currentSuggestions[index];
            document.getElementById('searchBox').value = selectedSuggestion.zip + ' - ' + selectedSuggestion.nameOriginal;
        }} else {{
            item.classList.remove('selected');
        }}
    }});
}}

function showSuggestions(query) {{
    const queryLower = query.toLowerCase();
    const suggestions = [];
    
    // Search for ZIP codes
    if (/^\d{{1,5}}$/.test(query)) {{
        suggestions.push(...searchIndex.filter(item => 
            item.zip.startsWith(query)
        ).slice(0, 5));
    }}
    
    // Search for place names
    const nameSuggestions = searchIndex.filter(item => 
        item.name.includes(queryLower)
    ).slice(0, 5);
    
    suggestions.push(...nameSuggestions);
    
    // Remove duplicates and limit to 8
    const uniqueSuggestions = Array.from(new Map(
        suggestions.map(item => [item.zip, item])
    ).values()).slice(0, 8);
    
    // Store current suggestions for keyboard navigation
    currentSuggestions = uniqueSuggestions;
    
    const suggestionsDiv = document.getElementById('suggestions');
    
    if (uniqueSuggestions.length > 0) {{
        suggestionsDiv.innerHTML = uniqueSuggestions.map((item, index) => 
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

function performSearch() {{
    const query = document.getElementById('searchBox').value.trim();
    if (!query) return;
    
    const queryLower = query.toLowerCase();
    
    // Try exact ZIP match first
    let found = searchIndex.find(item => item.zip === query);
    
    // Try ZIP prefix
    if (!found && /^\d{{1,5}}$/.test(query)) {{
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
    
    // Close suggestions and reset selection
    document.getElementById('suggestions').classList.remove('active');
    document.getElementById('searchBox').value = zipCode + ' - ' + location.nameOriginal;
    selectedSuggestionIndex = -1;
    currentSuggestions = [];
    
    // Immediately hide markers for smooth panning
    if (markersLayer) {{
        map.removeLayer(markersLayer);
    }}
    
    // Pan and zoom to location
    map.flyTo([location.lat, location.lon], 10, {{
        animate: true,
        duration: 1.5  // 1.5 seconds for smooth but quick flight
    }});
    
    // Redraw markers and show popup after flight
    setTimeout(() => {{
        updateMarkers();
        
        // Force a refresh of the tile layers to fix ghosted labels
        map.eachLayer(function(layer) {{
            if (layer instanceof L.TileLayer) {{
                layer.redraw();
            }}
        }});
        
        // Show popup
        setTimeout(() => {{
            L.popup()
                .setLatLng([location.lat, location.lon])
                .setContent(`<strong>${{location.zip}}</strong><br>
                            ${{location.nameOriginal}}<br>
                            ${{formatPrice(location.price)}}<br>
                            ${{location.pop.toLocaleString()}} population`)
                .openOn(map);
        }}, 100);  // Small delay to ensure everything is rendered
    }}, 1500);  // Match flight duration
}}

// Close suggestions when clicking outside
document.addEventListener('click', function(event) {{
    if (!event.target.closest('.search-container')) {{
        document.getElementById('suggestions').classList.remove('active');
    }}
}})

// Map starting locations
const mapViews = {{
    usa: {{center: [39.8, -98.6], zoom: 4}},
    florida: {{center: [27.8, -81.5], zoom: 6}},
    california: {{center: [36.7, -119.4], zoom: 6}},
    texas: {{center: [31.0, -99.0], zoom: 6}},
    nyc: {{center: [40.7, -74.0], zoom: 10}},
    la: {{center: [34.05, -118.25], zoom: 10}},
    chicago: {{center: [41.88, -87.63], zoom: 10}}
}};

// Initialize map
const startView = mapViews.usa;
const map = L.map('map', {{
    center: startView.center,
    zoom: startView.zoom,
    renderer: L.svg(),
    maxZoom: 18,
    attributionControl: false
}});

// Base layer without labels
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution: '',
    opacity: 0.9
}}).addTo(map);

// Color function using current quintiles
function getColor(price) {{
    if (price <= currentQuintiles[0]) return '#000000';     // Black: Bottom 20%
    if (price <= currentQuintiles[1]) return '#999999';     // Light gray: 20-40%
    if (price <= currentQuintiles[2]) return '#dadfce';     // Cream: 40-60%
    if (price <= currentQuintiles[3]) return '#99ccff';     // Pale blue: 60-80%
    return '#0bb4ff';                                        // Bright blue: Top 20%
}}

// Format price for display
function formatPrice(price) {{
    if (price >= 1000000) {{
        return '$' + (price / 1000000).toFixed(1) + 'M';
    }} else if (price >= 1000) {{
        return '$' + Math.round(price / 1000) + 'k';
    }} else {{
        return '$' + price.toLocaleString();
    }}
}}

// Calculate quintiles for array of prices
function calculateQuintiles(prices) {{
    if (prices.length < 5) return globalQuintiles; // Not enough data
    prices.sort((a, b) => a - b);
    const quintiles = [];
    [20, 40, 60, 80].forEach(percentile => {{
        const index = Math.floor(prices.length * percentile / 100);
        quintiles.push(prices[index]);
    }});
    return quintiles;
}}

// Get visible ZIP codes
function getVisibleZips() {{
    const bounds = map.getBounds();
    return zipData.filter(d => bounds.contains([d.lat, d.lon]));
}}

// Update legend with new quintiles and population info
function updateLegend(quintiles, zipCount, minPop, maxPop) {{
    document.getElementById('q0').textContent = formatPrice(quintiles[0]);
    document.getElementById('q1').textContent = formatPrice(quintiles[1]);
    document.getElementById('q2').textContent = formatPrice(quintiles[2]);
    document.getElementById('q3').textContent = formatPrice(quintiles[3]);
    // Calculate a reasonable "top" value (add 20% to the 80th percentile)
    const topValue = Math.round(quintiles[3] * 1.2);
    document.getElementById('q4').textContent = formatPrice(topValue) + '+';
    
    if (isLocalMode) {{
        document.getElementById('zipCount').textContent = `Analyzing ${{zipCount}} ZIP codes in view`;
        const popRangeEl = document.getElementById('popRange');
        if (minPop && maxPop) {{
            popRangeEl.textContent = `Population range: ${{minPop.toLocaleString()}} - ${{maxPop.toLocaleString()}}`;
            popRangeEl.style.display = 'block';
        }}
        document.getElementById('sizeNote').innerHTML = 'Bubble size reflects relative population<br>Zoom in for details';
    }} else {{
        document.getElementById('popRange').style.display = 'none';
        document.getElementById('sizeNote').innerHTML = 'Bubble size reflects population<br>Zoom in for details';
    }}
}}

// Toggle between global and local view
function toggleView() {{
    isLocalMode = !isLocalMode;
    
    // Update button
    const toggle = document.getElementById('viewToggle');
    const toggleText = document.getElementById('toggleText');
    toggle.classList.toggle('local-active', isLocalMode);
    toggleText.textContent = isLocalMode ? 'Switch to Global View' : 'Switch to Local View';
    
    // Update legend styling
    const legend = document.getElementById('legend');
    legend.classList.toggle('local-mode', isLocalMode);
    
    // Update marker pane for glow effect
    const markerPane = map.getPane('markerPane');
    if (markerPane) {{
        markerPane.classList.toggle('local-mode', isLocalMode);
    }}
    
    // Recalculate and update colors
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

// Store current population range for visible ZIPs
let currentMinPop = null;
let currentMaxPop = null;

// Update quintiles based on visible ZIP codes
function updateLocalQuintiles() {{
    if (!isLocalMode) return;
    
    const visibleZips = getVisibleZips();
    if (visibleZips.length < 10) {{
        // Not enough ZIPs, use global
        currentQuintiles = globalQuintiles;
        currentMinPop = null;
        currentMaxPop = null;
        updateLegend(globalQuintiles, visibleZips.length, null, null);
    }} else {{
        const prices = visibleZips.map(z => z.p);
        const populations = visibleZips.map(z => z.pop);
        currentQuintiles = calculateQuintiles(prices);
        currentMinPop = Math.min(...populations);
        currentMaxPop = Math.max(...populations);
        updateLegend(currentQuintiles, visibleZips.length, currentMinPop, currentMaxPop);
    }}
    updateMarkers();
}}

// Custom tooltip element
const tooltip = document.getElementById('tooltip');

// Layer for markers
let markersLayer = null;

// Update markers based on zoom and current mode
function updateMarkers() {{
    const zoom = map.getZoom();
    const bounds = map.getBounds();
    
    if (markersLayer) {{
        map.removeLayer(markersLayer);
    }}
    
    // Filter visible ZIPs
    const visibleZips = zipData.filter(d => bounds.contains([d.lat, d.lon]));
    
    // Create markers
    const markers = [];
    visibleZips.forEach(function(zip) {{
        let radius;
        
        // In local mode, use relative sizing based on population within view
        if (isLocalMode && currentMinPop !== null && currentMaxPop !== null && currentMaxPop > currentMinPop) {{
            // Calculate relative position (0 to 1) within visible population range
            const relativePosition = (zip.pop - currentMinPop) / (currentMaxPop - currentMinPop);
            // Scale from 3 to 25 pixels based on relative position
            radius = 3 + (relativePosition * 22);
            
            // Apply zoom-based scaling on top of relative sizing
            if (zoom <= 3) {{
                radius = radius * 0.5;  // Smaller at far zoom
            }} else if (zoom <= 5) {{
                radius = radius * 0.7;  // Medium at mid zoom
            }} else if (zoom >= 9) {{
                radius = radius * 1.3;  // Larger at close zoom
            }}
        }} else {{
            // Global mode: use original radius from data
            radius = zip.r;
            
            // Apply original zoom-based scaling
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
        
        // Calculate opacity
        let fillOpacity = 0.8;
        if (zoom <= 1) {{
            fillOpacity = 0.4;
        }} else if (zoom === 2) {{
            if (zip.pop < 50000) {{
                fillOpacity = 0.3;
            }} else if (zip.pop < 75000) {{
                fillOpacity = 0.4;
            }} else {{
                fillOpacity = 0.5;
            }}
        }} else if (zoom === 3) {{
            if (zip.pop < 30000) {{
                fillOpacity = 0.4;
            }} else if (zip.pop < 50000) {{
                fillOpacity = 0.6;
            }} else {{
                fillOpacity = 0.75;
            }}
        }} else if (zoom === 4) {{
            if (zip.pop < 20000) {{
                fillOpacity = 0.5;
            }} else if (zip.pop < 50000) {{
                fillOpacity = 0.7;
            }}
        }} else if (zoom === 5) {{
            if (zip.pop < 5000) {{
                fillOpacity = 0.4;
            }} else if (zip.pop < 15000) {{
                fillOpacity = 0.6;
            }} else if (zip.pop < 30000) {{
                fillOpacity = 0.7;
            }}
        }}
        
        const markerOptions = {{
            radius: radius,
            fillColor: getColor(zip.p),
            color: 'transparent',
            weight: 0,
            opacity: 1,
            fillOpacity: fillOpacity,
            interactive: zoom >= 8
        }};
        
        const marker = L.circleMarker([zip.lat, zip.lon], markerOptions);
        
        if (zoom >= 8) {{
            marker.zipData = zip;
            
            marker.on('mouseover', function(e) {{
                const data = e.target.zipData;
                tooltip.innerHTML = '<strong>' + data.z + '</strong><br>' +
                                   data.n + '<br>' +
                                   formatPrice(data.p) + '<br>' +
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
            
            marker.on('click', function(e) {{
                const data = e.target.zipData;
                L.popup()
                    .setLatLng(e.latlng)
                    .setContent('<strong>' + data.z + '</strong><br>' +
                               data.n + '<br>' +
                               formatPrice(data.p) + '<br>' +
                               data.pop.toLocaleString() + ' pop')
                    .openOn(map);
            }});
        }}
        
        markers.push(marker);
    }});
    
    markersLayer = L.layerGroup(markers);
    markersLayer.addTo(map);
}}

// Initial render
updateMarkers();

// Update on map movement with debounce
map.on('moveend zoomend', function() {{
    clearTimeout(updateTimeout);
    updateTimeout = setTimeout(function() {{
        if (isLocalMode) {{
            updateLocalQuintiles();
        }} else {{
            updateMarkers();
        }}
    }}, 300);
}});

// Create state boundaries pane
map.createPane('stateBoundaries');
map.getPane('stateBoundaries').style.zIndex = 450;

// State boundaries layer
let stateBoundariesLayer = null;

// Add state boundaries
fetch('https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json')
    .then(response => response.json())
    .then(data => {{
        stateBoundariesLayer = L.geoJSON(data, {{
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
            stateBoundariesLayer.setStyle({{
                color: newColor
            }});
        }});
    }})
    .catch(err => console.log('Could not load state boundaries'));

// Add labels on top
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    pane: 'markerPane',
    zIndex: 1000
}}).addTo(map);

// Update hint based on zoom
map.on('zoomend', function() {{
    const zoom = map.getZoom();
    const noteElement = document.querySelector('.note');
    if (zoom < 8) {{
        noteElement.innerHTML = 'Bubble size reflects population<br>Zoom in for details';
    }} else {{
        noteElement.innerHTML = 'Bubble size reflects population<br>Hover for details';
    }}
}});
</script>
</body>
</html>'''

# Save file
output_file = script_dir / 'output' / 'us_price_levels_with_search.html'
output_file.parent.mkdir(parents=True, exist_ok=True)
with open(output_file, 'w') as f:
    f.write(html_content)

# Check size
import os
file_size = os.path.getsize(output_file)
file_size_mb = file_size / 1024 / 1024

print(f"\n✅ Created {output_file}")
print(f"📊 File size: {file_size_mb:.2f} MB")
print(f"\n🎯 Features in this complete version:")
print(f"   ✓ Search by ZIP code or place name")
print(f"   ✓ Autocomplete suggestions while typing")
print(f"   ✓ Toggle button for Global/Local view")
print(f"   ✓ Dynamic price quintile recalculation")
print(f"   ✓ Dynamic bubble size redistribution")
print(f"   ✓ Glow effect in local mode")
print(f"   ✓ Population range display")
print(f"   ✓ Smooth animations to search results")

if file_size_mb < 5:
    print(f"\n✅ SUCCESS! File is {file_size_mb:.2f} MB - ready for use!")
else:
    print(f"\n⚠️ File size ({file_size_mb:.2f} MB) exceeds 5MB limit")