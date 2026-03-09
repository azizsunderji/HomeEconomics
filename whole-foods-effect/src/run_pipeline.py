"""
Run the full Whole Foods Effect pipeline end-to-end.
"""

from src.fetch_snap import fetch_and_process_snap
from src.fetch_starbucks import fetch_starbucks
from src.fetch_zhvi import process_zhvi
from src.scrape_stores import scrape_all
from src.match_controls import run_matching
from src.event_study import run_event_study
from src.visualize import run_visualization


def main():
    print("=" * 60)
    print("WHOLE FOODS EFFECT — Full Pipeline")
    print("=" * 60)

    # ── Data acquisition ──
    print("\n[1/7] Fetching USDA SNAP historical retailer data...")
    try:
        fetch_and_process_snap(use_github=False)
    except Exception as e:
        print(f"  SNAP (official) failed: {e}")
        try:
            fetch_and_process_snap(use_github=True)
        except Exception as e2:
            print(f"  SNAP (GitHub) also failed: {e2}")
            print("  Continuing without SNAP data.")

    print("\n[2/7] Fetching Starbucks locations from GitHub...")
    try:
        fetch_starbucks()
    except Exception as e:
        print(f"  Starbucks fetch failed: {e}")

    print("\n[3/7] Fetching Zillow ZHVI data...")
    process_zhvi()

    print("\n[4/7] Scraping store locations (Wikipedia + OSM)...")
    scrape_all(use_overpass=True)

    # ── Analysis ──
    print("\n[5/7] Matching treatment -> control ZIPs...")
    run_matching()

    print("\n[6/7] Running event study...")
    run_event_study()

    print("\n[7/7] Generating visualizations...")
    run_visualization()

    print("\n" + "=" * 60)
    print("Pipeline complete. Check notebooks/ for output charts.")
    print("=" * 60)


if __name__ == "__main__":
    main()
