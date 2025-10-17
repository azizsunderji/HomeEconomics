#!/usr/bin/env python3
"""
Prepare simplified ZIP code geometries for ProMap boundary view.
Creates two levels of simplification: ultra (zoom 6-8) and medium (zoom 9+)
"""

import geopandas as gpd
import json
import pandas as pd
from shapely.geometry import mapping
import sys

print("🗺️  Preparing ZIP Code Geometries for ProMap...")

# Load the full-resolution Census ZCTA shapefile
print("\n📂 Loading Census ZCTA 2020 shapefile (93 MB)...")
gdf = gpd.read_file('resources/shapefiles/cb_2020_us_zcta520_500k.shp')
print(f"✓ Loaded {len(gdf):,} ZIP codes")

# Ensure ZIP codes are properly formatted
gdf['ZCTA5CE20'] = gdf['ZCTA5CE20'].astype(str).str.zfill(5)

# Load Zillow data to filter to only ZIPs with price data
print("\n📊 Loading Zillow data to filter active ZIPs...")
df = pd.read_csv('data/ZillowZip.csv')
date_columns = [col for col in df.columns if '-' in col]
if not date_columns:
    print("❌ Error: No date columns found in Zillow data")
    sys.exit(1)

latest_date = date_columns[-1]
df_active = df[['RegionName', latest_date]].copy()
df_active = df_active.dropna(subset=[latest_date])
df_active['ZCTA5CE20'] = df_active['RegionName'].astype(str).str.zfill(5)
active_zips = set(df_active['ZCTA5CE20'].tolist())
print(f"✓ Found {len(active_zips):,} active ZIPs with price data")

# Filter geometries to only active ZIPs
gdf_active = gdf[gdf['ZCTA5CE20'].isin(active_zips)].copy()
print(f"✓ Filtered to {len(gdf_active):,} geometries with price data")

# Convert to WGS84 (EPSG:4326) for web mapping
print("\n🌐 Converting to WGS84 (EPSG:4326)...")
gdf_active = gdf_active.to_crs(epsg=4326)

# Create ultra-simplified version (zoom 6-8)
print("\n🔧 Creating ultra-simplified geometries (zoom 6-8)...")
print("   Tolerance: 0.01 degrees (~5-10 points per ZIP)")
gdf_ultra = gdf_active.copy()
gdf_ultra['geometry'] = gdf_ultra.geometry.simplify(tolerance=0.01, preserve_topology=True)

# Create medium-simplified version (zoom 9-11)
print("\n🔧 Creating medium-simplified geometries (zoom 9-11)...")
print("   Tolerance: 0.001 degrees (~20-30 points per ZIP)")
gdf_medium = gdf_active.copy()
gdf_medium['geometry'] = gdf_medium.geometry.simplify(tolerance=0.001, preserve_topology=True)

# Create high-detail version (zoom 12+)
print("\n🔧 Creating high-detail geometries (zoom 12+)...")
print("   Tolerance: 0.0001 degrees (~100-200 points per ZIP)")
gdf_detail = gdf_active.copy()
gdf_detail['geometry'] = gdf_detail.geometry.simplify(tolerance=0.0001, preserve_topology=True)

# Convert to GeoJSON-like structure
print("\n📦 Converting to GeoJSON format...")

def gdf_to_geojson_dict(gdf):
    """Convert GeoDataFrame to GeoJSON dictionary structure"""
    features = []
    for idx, row in gdf.iterrows():
        feature = {
            'type': 'Feature',
            'properties': {
                'zip': row['ZCTA5CE20']
            },
            'geometry': mapping(row['geometry'])
        }
        features.append(feature)

    return {
        'type': 'FeatureCollection',
        'features': features
    }

ultra_geojson = gdf_to_geojson_dict(gdf_ultra)
medium_geojson = gdf_to_geojson_dict(gdf_medium)
detail_geojson = gdf_to_geojson_dict(gdf_detail)

print(f"✓ Ultra: {len(ultra_geojson['features']):,} features")
print(f"✓ Medium: {len(medium_geojson['features']):,} features")
print(f"✓ Detail: {len(detail_geojson['features']):,} features")

# Save to JSON files
print("\n💾 Saving GeoJSON files...")

ultra_path = 'output/zip_geometries_ultra.json'
medium_path = 'output/zip_geometries_medium.json'
detail_path = 'output/zip_geometries_detail.json'

with open(ultra_path, 'w') as f:
    json.dump(ultra_geojson, f, separators=(',', ':'))

with open(medium_path, 'w') as f:
    json.dump(medium_geojson, f, separators=(',', ':'))

with open(detail_path, 'w') as f:
    json.dump(detail_geojson, f, separators=(',', ':'))

# Check file sizes
import os
ultra_size = os.path.getsize(ultra_path) / (1024 * 1024)
medium_size = os.path.getsize(medium_path) / (1024 * 1024)
detail_size = os.path.getsize(detail_path) / (1024 * 1024)

print(f"\n📏 File Sizes:")
print(f"   Ultra-simplified:  {ultra_size:.1f} MB")
print(f"   Medium-simplified: {medium_size:.1f} MB")
print(f"   High-detail:       {detail_size:.1f} MB")

# Calculate compression estimates (typical GeoJSON gzip ratio is ~10:1)
print(f"\n📦 Estimated gzipped sizes:")
print(f"   Ultra-simplified:  ~{ultra_size/10:.1f} MB")
print(f"   Medium-simplified: ~{medium_size/10:.1f} MB")
print(f"   High-detail:       ~{detail_size/10:.1f} MB")

# Analyze geometry complexity
print("\n📊 Geometry Complexity Analysis:")

def analyze_complexity(gdf, name):
    point_counts = []
    for geom in gdf.geometry:
        if geom.geom_type == 'Polygon':
            point_counts.append(len(geom.exterior.coords))
        elif geom.geom_type == 'MultiPolygon':
            total = sum(len(poly.exterior.coords) for poly in geom.geoms)
            point_counts.append(total)

    if point_counts:
        print(f"   {name}:")
        print(f"      Min points:  {min(point_counts)}")
        print(f"      Max points:  {max(point_counts)}")
        print(f"      Mean points: {sum(point_counts)/len(point_counts):.1f}")

analyze_complexity(gdf_ultra, "Ultra-simplified")
analyze_complexity(gdf_medium, "Medium-simplified")
analyze_complexity(gdf_detail, "High-detail")

print("\n✅ Geometry preparation complete!")
print(f"\n📁 Output files:")
print(f"   {ultra_path}")
print(f"   {medium_path}")
print(f"   {detail_path}")
print("\nNext step: Integrate these geometries into create_sophisticated_map.py")
