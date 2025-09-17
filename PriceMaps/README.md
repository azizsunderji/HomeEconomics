# Price Maps

Automated daily generation of interactive US home price maps using Zillow data.

This is a subfolder of the main HomeEconomics repository.

## Features

- **Price Levels Map**: Shows current home prices by ZIP code
- **Year-over-Year Map**: Shows price changes compared to one year ago
- **Daily Updates**: Automated checks for new Zillow data
- **Interactive Search**: Find specific ZIP codes or cities
- **Population-weighted Bubbles**: Visual representation scaled by population

## Maps

### Live Maps (GitHub Pages)
Once deployed, the maps will be available at:
- Price Levels: `https://[username].github.io/home-economics-pricemap/us_price_levels_with_search.html`
- YoY Changes: `https://[username].github.io/home-economics-pricemap/us_yoy_price_map_with_search.html`

## Data Sources

- **Housing Data**: [Zillow Research](https://www.zillow.com/research/data/)
  - Dataset: ZIP Code ZHVI (Typical Home Value)
  - Tier: 0.33-0.67 (Middle tier homes)
  - Updates: Monthly

- **Geographic Data**: US Census Bureau ZIP Code Tabulation Areas (2020)
- **Population Data**: US Census population by ZIP code

## Automation

The repository uses GitHub Actions to:
1. Check daily for new Zillow data (9 AM EST)
2. Download updated data when available
3. Generate both maps
4. Commit changes and deploy to GitHub Pages

## Local Development

### Prerequisites
```bash
pip install pandas numpy geopandas requests
```

### Running Locally
```bash
# Check for updates
python scripts/check_for_updates.py

# Download data
python scripts/download_data.py

# Generate maps
python scripts/create_price_levels.py
python scripts/create_yoy_map.py
```

### Manual Update
You can trigger an update manually from the GitHub Actions tab.

## Project Structure

```
home-economics-pricemap/
├── .github/workflows/
│   └── update_maps.yml      # Daily automation
├── scripts/
│   ├── check_for_updates.py # Detect new data
│   ├── download_data.py     # Fetch Zillow data
│   ├── create_price_levels.py
│   └── create_yoy_map.py
├── data/                    # Downloaded data (gitignored)
├── output/                  # Generated HTML maps
└── resources/              # Reference files
```

## Setup Instructions

1. Fork/clone this repository
2. Add reference data files to `resources/` folder:
   - Population data: `resources/populations/PopulationByZIP.csv`
   - Shapefile: `resources/shapefiles/cb_2020_us_zcta520_500k.*`
3. Enable GitHub Actions in your repository
4. (Optional) Enable GitHub Pages from Settings → Pages → Source: GitHub Actions

## Color Scheme

Maps use the Home Economics brand colors:
- Blue: #0BB4FF
- Yellow: #FEC439
- Green: #67A275
- Red: #F4743B
- Black: #3D3733
- Background: #F6F7F3

## Notes

- Data updates typically occur monthly
- Maps are optimized for performance with ~30,000 ZIP codes
- Search functionality includes ZIP codes and city names
- File sizes are approximately 2-3 MB per map

## License

This project uses publicly available data from Zillow Research.
Please review Zillow's terms of use for their data.