# Whole Foods Effect

**Do store openings (Whole Foods, Trader Joe's, Wegmans, Starbucks, Aldi) predict home price appreciation?**

An event-study diff-in-diff analysis using Zillow ZHVI ZIP-level data and store location data assembled from multiple free public sources.

## Data Sources

| Source | What it provides | Opening dates? |
|--------|-----------------|----------------|
| **USDA SNAP Historical Retailer Data** | Every SNAP-authorized retailer since 2004: name, address, ZIP, lat/lon, authorization dates | Yes — authorization date ≈ opening date |
| **Wikipedia** | Store location tables for Whole Foods, Trader Joe's, Wegmans | Partial (varies by chain) |
| **OpenStreetMap (Overpass API)** | Current store locations with lat/lon and address tags | Rare (`start_date` tag exists but seldom populated) |
| **Starbucks GitHub** (chrismeller) | Daily-updated CSV of all worldwide Starbucks | No (use SNAP or git history) |
| **Hand-curated seed data** | Wegmans confirmed opening dates from press releases | Yes (37 stores with month-level dates) |
| **Zillow ZHVI** | Monthly home value index by ZIP code | N/A (outcome variable) |

### Key insight

The **USDA SNAP data** is the single best free source for store opening dates. Every grocery store accepting SNAP benefits has an authorization date — this closely tracks the actual opening date. The historical file covers 2004–2025 with ~300k retailers.

## Structure

```
data/raw/          # Raw downloads (ZHVI, SNAP CSV, scraped store locations)
data/processed/    # Cleaned and merged datasets
notebooks/         # Analysis notebooks
src/               # Pipeline modules
```

## Pipeline

```bash
pip install -r requirements.txt

# Run individual steps:
python -m src.fetch_snap        # [1] Download + filter USDA SNAP data
python -m src.fetch_starbucks   # [2] Download Starbucks locations
python -m src.fetch_zhvi        # [3] Download Zillow ZHVI
python -m src.scrape_stores     # [4] Wikipedia + OSM + seed data
python -m src.match_controls    # [5] Match treatment → control ZIPs
python -m src.event_study       # [6] Stacked diff-in-diff
python -m src.visualize         # [7] Event-study charts

# Or run everything:
python -m src.run_pipeline
```

## Data Source Details

### USDA SNAP Historical Retailer Data
- **URL**: https://www.fns.usda.gov/snap/retailer/historical-data
- **Format**: Zipped CSV (~200MB unzipped)
- **Fields**: retailer name, type, address, city, state, ZIP, lat, lon, authorization date, end date
- **Enhanced version**: https://github.com/jshannon75/snap_retailers (adds census tract, county, PUMA)

### Starbucks Locations (GitHub)
- **URL**: https://github.com/chrismeller/StarbucksLocations
- **Format**: CSV with daily git commits (can diff for opening/closing dates)

### Wikipedia
- Whole Foods: `List_of_Whole_Foods_Market_locations`
- Trader Joe's: `List_of_Trader_Joe's_locations`
- Wegmans: `List_of_Wegmans_locations`

### OpenStreetMap
- Overpass API queries for `shop=supermarket` and `amenity=cafe` with chain name filters
- Extracts `addr:postcode`, `start_date`, and full address tags
