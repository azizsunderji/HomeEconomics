#!/usr/bin/env python3
"""
Prepare multi-resolution state geometries for ProMap.
Downloads Census Bureau shapefiles and creates 3 resolution levels.
"""

import geopandas as gpd
import json
import requests
import zipfile
import io
import os

print("🗺️  Preparing State Geometries for ProMap...")

# Create output directory if needed
os.makedirs('output', exist_ok=True)

# Census Bureau cartographic boundary files (2020)
# These are already simplified for web use
urls = {
    'low': 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_20m.zip',     # 20m - very simple
    'medium': 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_5m.zip',   # 5m - moderate detail
    'high': 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_500k.zip'    # 500k - high detail
}

state_geojson = {}

for resolution, url in urls.items():
    print(f"\n📥 Downloading {resolution} resolution shapefile...")
    print(f"   URL: {url}")

    # Download and extract
    response = requests.get(url)
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        z.extractall(f'temp_{resolution}')

    # Find the .shp file
    shp_file = [f for f in os.listdir(f'temp_{resolution}') if f.endswith('.shp')][0]
    shp_path = f'temp_{resolution}/{shp_file}'

    print(f"   Loading {shp_file}...")
    gdf = gpd.read_file(shp_path)

    # Filter to 50 states + DC (exclude territories)
    # STATEFP codes: 01-56 (excluding non-states)
    gdf = gdf[gdf['STATEFP'].astype(int) <= 56]

    # Exclude specific territories
    exclude_names = ['Puerto Rico', 'Virgin Islands', 'Guam', 'American Samoa', 'Northern Mariana Islands']
    gdf = gdf[~gdf['NAME'].isin(exclude_names)]

    print(f"   ✓ Loaded {len(gdf)} states")

    # Convert to WGS84
    gdf = gdf.to_crs(epsg=4326)

    # Convert to GeoJSON
    geojson = json.loads(gdf.to_json())

    # Simplify properties (only keep name)
    for feature in geojson['features']:
        feature['properties'] = {'name': feature['properties']['NAME']}

    state_geojson[resolution] = geojson

    # Clean up temp directory
    import shutil
    shutil.rmtree(f'temp_{resolution}')

# Save GeoJSON files
print("\n💾 Saving GeoJSON files...")

for resolution, geojson in state_geojson.items():
    filename = f'output/state_boundaries_{resolution}.json'
    with open(filename, 'w') as f:
        json.dump(geojson, f, separators=(',', ':'))

    file_size = os.path.getsize(filename) / 1024
    print(f"   ✓ {filename}: {file_size:.1f} KB")

print("\n✅ State geometry preparation complete!")
print("\nNext step: Update create_sophisticated_map.py to use these files")
print("Zoom ranges:")
print("  - Low resolution (20m):    zoom 0-5")
print("  - Medium resolution (5m):  zoom 6-8")
print("  - High resolution (500k):  zoom 9+")
