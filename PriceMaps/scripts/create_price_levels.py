#!/usr/bin/env python3
"""
Create an interactive home price levels map with search functionality
Refactored for automation in GitHub Actions
"""
import pandas as pd
import geopandas as gpd
import numpy as np
import json
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

# Set up paths relative to script location
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
RESOURCES_DIR = PROJECT_ROOT / "resources"
OUTPUT_DIR = PROJECT_ROOT / "output"

def create_price_levels_map():
    """Generate the price levels map"""
    print("🎯 Creating Interactive Price Levels Map with Search")
    print("="*70)

    # Load the Zillow data
    print("📊 Loading Zillow price data...")
    zillow_file = DATA_DIR / "ZillowZip.csv"
    if not zillow_file.exists():
        print(f"Error: {zillow_file} not found. Run download_data.py first.")
        return False

    df = pd.read_csv(zillow_file)

    # Get date columns
    date_columns = [col for col in df.columns if '-' in col]
    if date_columns:
        latest_date = date_columns[-1]
        print(f"📅 Latest date: {latest_date}")
    else:
        print("Error: No date columns found")
        return False

    # Get current price levels
    df_analysis = df[['RegionName', 'State', 'City', latest_date]].copy()
    df_analysis = df_analysis.dropna(subset=[latest_date])
    df_analysis['price_level'] = df_analysis[latest_date]
    df_analysis['ZCTA5CE20'] = df_analysis['RegionName'].astype(str).str.zfill(5)

    # Remove extreme outliers
    df_analysis = df_analysis[(df_analysis['price_level'] >= 10000) & (df_analysis['price_level'] <= 10000000)]

    print(f"📍 ZIP codes with price data: {len(df_analysis):,}")
    print(f"💰 Price range: ${df_analysis['price_level'].min():,.0f} to ${df_analysis['price_level'].max():,.0f}")
    print(f"💰 Median price: ${df_analysis['price_level'].median():,.0f}")

    # Load population data
    print("\n👥 Loading population data...")
    pop_file = RESOURCES_DIR / "populations" / "PopulationByZIP.csv"
    if pop_file.exists():
        pop_df = pd.read_csv(pop_file, encoding='latin1')
        pop_df.columns = ['zcta', 'name', 'population']
        pop_df['zcta'] = pop_df['zcta'].astype(str).str.zfill(5)
        pop_df['population'] = pd.to_numeric(pop_df['population'], errors='coerce').fillna(1000)
    else:
        print(f"Warning: Population file not found at {pop_file}")
        # Create dummy population data
        pop_df = pd.DataFrame({
            'zcta': df_analysis['ZCTA5CE20'],
            'population': 5000  # Default population
        })

    # Load ZIP code shapefile for coordinates
    print("\n🗺️ Loading ZIP code coordinates...")
    shapefile = RESOURCES_DIR / "shapefiles" / "cb_2020_us_zcta520_500k.shp"
    if shapefile.exists():
        gdf = gpd.read_file(shapefile)
    else:
        print(f"Warning: Shapefile not found at {shapefile}")
        # We'll need to handle missing coordinates
        # For now, create a simple version without actual coordinates
        return create_simple_map_without_coordinates(df_analysis, latest_date)

    # Get centroids
    gdf['centroid'] = gdf.geometry.centroid
    gdf['lat'] = gdf.centroid.y
    gdf['lon'] = gdf.centroid.x

    # Merge all data
    print("\n🔄 Merging datasets...")
    merged = df_analysis.merge(
        gdf[['ZCTA5CE20', 'lat', 'lon']],
        on='ZCTA5CE20',
        how='inner'
    )

    merged = merged.merge(
        pop_df[['zcta', 'population']],
        left_on='ZCTA5CE20',
        right_on='zcta',
        how='left'
    )

    merged['population'] = merged['population'].fillna(1000)

    # Calculate bubble sizes
    merged['bubble_size'] = np.sqrt(merged['population']) * 0.5
    merged['bubble_size'] = merged['bubble_size'].clip(lower=3, upper=50)

    # Format for display
    merged['price_display'] = merged['price_level'].apply(lambda x: f"${x:,.0f}")

    print(f"\n✅ Final dataset: {len(merged):,} ZIP codes")

    # Generate the HTML map
    create_html_map(merged, latest_date)

    return True

def create_simple_map_without_coordinates(df_analysis, latest_date):
    """Create a simplified version when shapefile is missing"""
    print("Creating simplified map without geographic data...")

    # Create HTML with a table view instead
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>US Home Price Levels - {latest_date}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: #F6F7F3;
        }}
        h1 {{
            color: #3D3733;
        }}
        .warning {{
            background: #FEC439;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
        }}
        th, td {{
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background: #0BB4FF;
            color: white;
        }}
    </style>
</head>
<body>
    <h1>US Home Price Levels - {latest_date}</h1>
    <div class="warning">
        ⚠️ Geographic data files not found. Displaying tabular view.
        Please ensure shapefile and population data are available.
    </div>
    <table>
        <thead>
            <tr>
                <th>ZIP Code</th>
                <th>City</th>
                <th>State</th>
                <th>Price</th>
            </tr>
        </thead>
        <tbody>
"""

    # Add top 100 entries
    for _, row in df_analysis.head(100).iterrows():
        html_content += f"""
            <tr>
                <td>{row['ZCTA5CE20']}</td>
                <td>{row['City']}</td>
                <td>{row['State']}</td>
                <td>${row['price_level']:,.0f}</td>
            </tr>
"""

    html_content += """
        </tbody>
    </table>
</body>
</html>
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "us_price_levels_with_search.html"

    with open(output_file, 'w') as f:
        f.write(html_content)

    print(f"✓ Saved simplified map to {output_file}")
    return True

def create_html_map(merged, latest_date):
    """Generate the full interactive HTML map"""
    print("\n📝 Generating interactive HTML map...")

    # Convert to JSON
    data_json = merged[['ZCTA5CE20', 'City', 'State', 'lat', 'lon',
                       'price_level', 'price_display', 'population',
                       'bubble_size']].to_json(orient='records')

    # HTML template (simplified version of the original)
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>US Home Price Levels Map - {latest_date}</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #F6F7F3;
        }}
        #map {{
            height: 100vh;
            width: 100%;
        }}
        .search-container {{
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
            background: white;
            padding: 10px;
            border-radius: 4px;
            box-shadow: 0 1px 5px rgba(0,0,0,0.4);
        }}
        .search-input {{
            width: 200px;
            padding: 5px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}
        .info-box {{
            position: absolute;
            bottom: 10px;
            left: 10px;
            background: white;
            padding: 10px;
            border-radius: 4px;
            box-shadow: 0 1px 5px rgba(0,0,0,0.4);
            z-index: 1000;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="search-container">
        <input type="text" class="search-input" placeholder="Search ZIP code..." id="searchInput">
    </div>
    <div class="info-box">
        <div>Data: {latest_date}</div>
        <div>Total ZIPs: {len(merged):,}</div>
    </div>

    <script>
        // Load data
        const zipData = {data_json};

        // Initialize map
        const map = L.map('map').setView([39.8283, -98.5795], 4);

        // Add tile layer
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '© OpenStreetMap contributors'
        }}).addTo(map);

        // Price color scale
        function getColor(price) {{
            return price > 1000000 ? '#F4743B' :
                   price > 750000  ? '#FEC439' :
                   price > 500000  ? '#67A275' :
                   price > 350000  ? '#0BB4FF' :
                   price > 250000  ? '#C6DCCB' :
                                     '#DADFCE';
        }}

        // Create markers
        const markers = [];
        zipData.forEach(zip => {{
            const marker = L.circleMarker([zip.lat, zip.lon], {{
                radius: Math.min(zip.bubble_size, 20),
                fillColor: getColor(zip.price_level),
                color: '#3D3733',
                weight: 0.5,
                opacity: 1,
                fillOpacity: 0.7
            }}).addTo(map);

            marker.bindPopup(`
                <b>${{zip.ZCTA5CE20}}</b><br>
                ${{zip.City}}, ${{zip.State}}<br>
                <b>${{zip.price_display}}</b><br>
                Pop: ${{zip.population.toLocaleString()}}
            `);

            markers.push({{marker: marker, data: zip}});
        }});

        // Search functionality
        document.getElementById('searchInput').addEventListener('input', function(e) {{
            const searchTerm = e.target.value.toLowerCase();

            markers.forEach(item => {{
                const matches = item.data.ZCTA5CE20.includes(searchTerm) ||
                              item.data.City.toLowerCase().includes(searchTerm);

                if (matches && searchTerm.length > 0) {{
                    item.marker.setStyle({{fillOpacity: 1, weight: 2}});
                    if (searchTerm.length === 5 && item.data.ZCTA5CE20 === searchTerm) {{
                        map.setView([item.data.lat, item.data.lon], 10);
                        item.marker.openPopup();
                    }}
                }} else if (searchTerm.length === 0) {{
                    item.marker.setStyle({{fillOpacity: 0.7, weight: 0.5}});
                }} else {{
                    item.marker.setStyle({{fillOpacity: 0.1, weight: 0.5}});
                }}
            }});
        }});
    </script>
</body>
</html>
"""

    # Save the HTML
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "us_price_levels_with_search.html"

    with open(output_file, 'w') as f:
        f.write(html_content)

    print(f"✅ Map saved to {output_file}")
    print(f"📊 File size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")

def main():
    """Main function"""
    success = create_price_levels_map()
    return 0 if success else 1

if __name__ == "__main__":
    exit(main())