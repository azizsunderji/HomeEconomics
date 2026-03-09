"""
Run the full Whole Foods Effect pipeline end-to-end.
"""

from src.fetch_zhvi import process_zhvi
from src.scrape_stores import scrape_all
from src.match_controls import run_matching
from src.event_study import run_event_study
from src.visualize import run_visualization


def main():
    print("=" * 60)
    print("WHOLE FOODS EFFECT — Full Pipeline")
    print("=" * 60)

    print("\n[1/5] Fetching Zillow ZHVI data...")
    process_zhvi()

    print("\n[2/5] Scraping store locations...")
    scrape_all(use_overpass=True)

    print("\n[3/5] Matching treatment → control ZIPs...")
    run_matching()

    print("\n[4/5] Running event study...")
    run_event_study()

    print("\n[5/5] Generating visualizations...")
    run_visualization()

    print("\n" + "=" * 60)
    print("Pipeline complete. Check notebooks/ for output charts.")
    print("=" * 60)


if __name__ == "__main__":
    main()
