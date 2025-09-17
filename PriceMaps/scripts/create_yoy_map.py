#!/usr/bin/env python3
"""
Create an interactive year-over-year home price change map
Refactored for automation in GitHub Actions
"""
import pandas as pd
import numpy as np
import geopandas as gpd
import json
from datetime import datetime
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# Set up paths relative to script location
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
RESOURCES_DIR = PROJECT_ROOT / "resources"
OUTPUT_DIR = PROJECT_ROOT / "output"

def create_yoy_map():
    """Generate the year-over-year price change map"""
    print("🏠 Creating Year-over-Year Home Price Map")
    print("="*70)

    # Load Zillow housing data
    zillow_file = DATA_DIR / "ZillowZip.csv"
    if not zillow_file.exists():
        print(f"Error: {zillow_file} not found. Run download_data.py first.")
        return False

    df = pd.read_csv(zillow_file)

    # Get date columns
    date_columns = [col for col in df.columns if '-' in col]
    if not date_columns:
        print("Error: No date columns found")
        return False

    # Parse dates and find latest
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
        print("Error: Could not find year-ago data")
        return False

    print(f"📅 Year-ago date: {year_ago_date}")

    # Calculate YoY change
    df_yoy = df[['RegionName', 'State', 'City', latest_date, year_ago_date]].copy()
    df_yoy = df_yoy.dropna(subset=[latest_date, year_ago_date])

    df_yoy['latest_price'] = df_yoy[latest_date]
    df_yoy['year_ago_price'] = df_yoy[year_ago_date]
    df_yoy['yoy_change'] = ((df_yoy['latest_price'] - df_yoy['year_ago_price']) /
                            df_yoy['year_ago_price'] * 100)

    # Filter extreme outliers
    df_yoy = df_yoy[(df_yoy['yoy_change'] > -50) & (df_yoy['yoy_change'] < 100)]
    df_yoy['ZCTA5CE20'] = df_yoy['RegionName'].astype(str).str.zfill(5)

    print(f"📍 ZIP codes with YoY data: {len(df_yoy):,}")
    print(f"📈 YoY change range: {df_yoy['yoy_change'].min():.1f}% to {df_yoy['yoy_change'].max():.1f}%")
    print(f"📊 Median YoY change: {df_yoy['yoy_change'].median():.1f}%")

    # Load population data
    print("\n👥 Loading population data...")
    pop_file = RESOURCES_DIR / "populations" / "PopulationByZIP.csv"
    if pop_file.exists():
        pop_df = pd.read_csv(pop_file, encoding='latin1')
        pop_df.columns = ['zcta', 'name', 'population']
        pop_df['zcta'] = pop_df['zcta'].astype(str).str.zfill(5)
        pop_df['population'] = pd.to_numeric(pop_df['population'], errors='coerce').fillna(1000)
    else:
        print(f"Warning: Population file not found")
        pop_df = pd.DataFrame({
            'zcta': df_yoy['ZCTA5CE20'],
            'population': 5000
        })

    # Load ZIP code shapefile
    print("\n🗺️ Loading ZIP code coordinates...")
    shapefile = RESOURCES_DIR / "shapefiles" / "cb_2020_us_zcta520_500k.shp"
    if shapefile.exists():
        gdf = gpd.read_file(shapefile)
    else:
        print(f"Warning: Shapefile not found")
        return create_simple_yoy_table(df_yoy, latest_date, year_ago_date)

    # Get centroids
    gdf['centroid'] = gdf.geometry.centroid
    gdf['lat'] = gdf.centroid.y
    gdf['lon'] = gdf.centroid.x

    # Merge all data
    print("\n🔄 Merging datasets...")
    merged = df_yoy.merge(
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
    merged['yoy_display'] = merged['yoy_change'].apply(lambda x: f"{x:+.1f}%")
    merged['price_display'] = merged['latest_price'].apply(lambda x: f"${x:,.0f}")

    print(f"\n✅ Final dataset: {len(merged):,} ZIP codes")

    # Generate the HTML map
    create_yoy_html_map(merged, latest_date, year_ago_date)

    return True

def create_simple_yoy_table(df_yoy, latest_date, year_ago_date):
    """Create a simplified table view when shapefile is missing"""
    print("Creating simplified YoY table view...")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>US Home Price YoY Changes - {latest_date}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
        .positive {{
            color: #67A275;
        }}
        .negative {{
            color: #F4743B;
        }}
    </style>
</head>
<body>
    <h1>US Home Price YoY Changes</h1>
    <div class="warning">
        ⚠️ Geographic data files not found. Displaying tabular view.
    </div>
    <p>Comparing {latest_date} to {year_ago_date}</p>
    <table>
        <thead>
            <tr>
                <th>ZIP Code</th>
                <th>City</th>
                <th>State</th>
                <th>Current Price</th>
                <th>YoY Change</th>
            </tr>
        </thead>
        <tbody>
"""

    # Sort by YoY change and show top/bottom
    df_sorted = df_yoy.sort_values('yoy_change', ascending=False)

    for _, row in df_sorted.head(100).iterrows():
        change_class = 'positive' if row['yoy_change'] > 0 else 'negative'
        html_content += f"""
            <tr>
                <td>{row['ZCTA5CE20']}</td>
                <td>{row['City']}</td>
                <td>{row['State']}</td>
                <td>${row['latest_price']:,.0f}</td>
                <td class="{change_class}">{row['yoy_change']:+.1f}%</td>
            </tr>
"""

    html_content += """
        </tbody>
    </table>
</body>
</html>
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "us_yoy_price_map_with_search.html"

    with open(output_file, 'w') as f:
        f.write(html_content)

    print(f"✓ Saved simplified YoY table to {output_file}")
    return True

def create_yoy_html_map(merged, latest_date, year_ago_date):
    """Generate the full interactive YoY HTML map"""
    print("\n📝 Generating interactive YoY map...")

    # Convert to JSON
    data_json = merged[['ZCTA5CE20', 'City', 'State', 'lat', 'lon',
                       'yoy_change', 'yoy_display', 'latest_price',
                       'price_display', 'population', 'bubble_size']].to_json(orient='records')

    # HTML template
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>US Home Price YoY Changes - {latest_date}</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
        .legend {{
            position: absolute;
            bottom: 10px;
            right: 10px;
            background: white;
            padding: 10px;
            border-radius: 4px;
            box-shadow: 0 1px 5px rgba(0,0,0,0.4);
            z-index: 1000;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            margin: 2px 0;
        }}
        .legend-color {{
            width: 20px;
            height: 20px;
            margin-right: 5px;
            border: 1px solid #3D3733;
            border-radius: 50%;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="search-container">
        <input type="text" class="search-input" placeholder="Search ZIP code..." id="searchInput">
    </div>
    <div class="info-box">
        <div><b>YoY Price Changes</b></div>
        <div>{latest_date} vs {year_ago_date}</div>
        <div>Total ZIPs: {len(merged):,}</div>
        <div>Median: {merged['yoy_change'].median():+.1f}%</div>
    </div>
    <div class="legend">
        <div class="legend-item">
            <div class="legend-color" style="background: #F4743B;"></div>
            <span>&gt; +10%</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #FEC439;"></div>
            <span>+5% to +10%</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #67A275;"></div>
            <span>0% to +5%</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #0BB4FF;"></div>
            <span>-5% to 0%</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #3D3733;"></div>
            <span>&lt; -5%</span>
        </div>
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

        // YoY color scale
        function getColor(change) {{
            return change > 10  ? '#F4743B' :  // Red for high increases
                   change > 5   ? '#FEC439' :  // Yellow for moderate increases
                   change > 0   ? '#67A275' :  // Green for small increases
                   change > -5  ? '#0BB4FF' :  // Blue for small decreases
                                  '#3D3733';    // Black for large decreases
        }}

        // Create markers
        const markers = [];
        zipData.forEach(zip => {{
            const marker = L.circleMarker([zip.lat, zip.lon], {{
                radius: Math.min(zip.bubble_size, 20),
                fillColor: getColor(zip.yoy_change),
                color: '#3D3733',
                weight: 0.5,
                opacity: 1,
                fillOpacity: 0.7
            }}).addTo(map);

            marker.bindPopup(`
                <b>${{zip.ZCTA5CE20}}</b><br>
                ${{zip.City}}, ${{zip.State}}<br>
                <b>YoY: ${{zip.yoy_display}}</b><br>
                Current: ${{zip.price_display}}<br>
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
    output_file = OUTPUT_DIR / "us_yoy_price_map_with_search.html"

    with open(output_file, 'w') as f:
        f.write(html_content)

    print(f"✅ YoY map saved to {output_file}")
    print(f"📊 File size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")

def main():
    """Main function"""
    success = create_yoy_map()
    return 0 if success else 1

if __name__ == "__main__":
    exit(main())