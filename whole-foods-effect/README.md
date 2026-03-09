# Whole Foods Effect

**Do store openings (Whole Foods, Trader Joe's, Wegmans, Starbucks, Aldi) predict home price appreciation?**

An event-study diff-in-diff analysis using Zillow ZHVI ZIP-level data and store location data from Wikipedia and OpenStreetMap.

## Structure

```
data/raw/          # Raw downloads (ZHVI, scraped store locations)
data/processed/    # Cleaned and merged datasets
notebooks/         # Analysis notebooks
src/               # Pipeline modules
```

## Pipeline

1. **`src/scrape_stores.py`** — Scrape store locations and opening dates from Wikipedia; supplement with OSM Overpass API
2. **`src/fetch_zhvi.py`** — Download and reshape Zillow ZHVI ZIP-level data
3. **`src/match_controls.py`** — Match treatment ZIPs to control ZIPs on baseline price level and urban density
4. **`src/event_study.py`** — Run stacked diff-in-diff event study in [-36, +36] month window
5. **`src/visualize.py`** — Produce stacked event-study charts by chain

## Usage

```bash
pip install -r requirements.txt
python -m src.fetch_zhvi
python -m src.scrape_stores
python -m src.match_controls
python -m src.event_study
python -m src.visualize
```

Or run the full pipeline:
```bash
python -m src.run_pipeline
```
