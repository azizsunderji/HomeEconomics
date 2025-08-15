#!/usr/bin/env python3
# scripts/make_charts.py
from __future__ import annotations

# --- ABC Oracle font loader (must be first) ---
from pathlib import Path
from matplotlib import pyplot as plt
from matplotlib import font_manager as fm

def _load_abc_oracle():
    font_dir = Path(__file__).resolve().parent / "assets" / "fonts" / "abc-oracle"
    found = []
    if font_dir.exists():
        for p in list(font_dir.glob("*.ttf")) + list(font_dir.glob("*.otf")):
            fm.fontManager.addfont(str(p))
            found.append(fm.FontProperties(fname=str(p)).get_name())
    if found:
        # de-dupe while preserving order
        families = list(dict.fromkeys(found))
        fam = families[0]
        plt.rcParams["font.family"] = fam
        plt.rcParams["font.sans-serif"] = families + ["Arial", "DejaVu Sans"]
        # hard assert so we see a clear message in logs
        fm.findfont(fm.FontProperties(family=fam), fallback_to_default=False)
        print("FONT DEBUG loaded:", ", ".join(families))
    else:
        print(f"WARNING: No ABC Oracle fonts found at {font_dir}")

_load_abc_oracle()
# --- end loader ---



import sys
import os
from pathlib import Path
from datetime import datetime
import click
from dateutil import tz

# Add scripts to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from chart_adapter import render_city, render_national
from download_data import download_redfin_data





def today_eastern() -> str:
    et = tz.gettz("America/New_York")
    return datetime.now(et).strftime("%Y-%m-%d")

def read_cities(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"cities file not found: {path}")
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower() != s or " " in s:
            raise ValueError(f"City slug must be lowercase and underscore-separated: {s}")
        out.append(s)
    if not out:
        raise ValueError("No cities found in cities file.")
    return out

@click.command()
@click.option("--date", "date_str", help="YYYY-MM-DD. Defaults to today in US/Eastern.")
@click.option("--cities", "cities_path", type=click.Path(path_type=Path), default=Path("cities.txt"), show_default=True)
@click.option("--out", "out_root", type=click.Path(path_type=Path), default=Path("out/reports"), show_default=True)
@click.option("--national/--no-national", default=False, show_default=True, help="Generate national charts if configured.")
@click.option("--download/--no-download", default=True, show_default=True, help="Download fresh data first.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path("data"), show_default=True, help="Data directory.")
def main(date_str: str | None, cities_path: Path, out_root: Path, national: bool, download: bool, data_dir: Path):
    try:
        date_str = date_str or today_eastern()
        # Validate format
        datetime.strptime(date_str, "%Y-%m-%d")
        
        # Download data if requested
        if download:
            print(f"📥 Downloading latest Redfin data...")
            download_redfin_data(data_dir, force=False)
        
        # Read cities
        cities = read_cities(cities_path)
        print(f"📊 Generating charts for {len(cities)} cities...")

        # Generate charts for each city
        failed_cities = []
        for i, c in enumerate(cities, 1):
            try:
                print(f"  [{i}/{len(cities)}] {c}...", end='', flush=True)
                render_city(c, date_str, out_root)
                print(" ✓")
            except Exception as e:
                print(f" ✗ ({str(e)})")
                failed_cities.append((c, str(e)))

        # National charts if configured
        if national:
            try:
                print(f"  National charts...", end='', flush=True)
                render_national(date_str, out_root)
                print(" ✓")
            except Exception as e:
                print(f" ✗ ({str(e)})")

        # Summary
        print(f"\n✅ Charts done for {len(cities) - len(failed_cities)}/{len(cities)} cities @ {date_str}")
        
        if failed_cities:
            print(f"\n⚠️  Failed cities ({len(failed_cities)}):")
            for city, error in failed_cities[:10]:  # Show first 10
                print(f"  - {city}: {error}")
            if len(failed_cities) > 10:
                print(f"  ... and {len(failed_cities) - 10} more")
        
        # Exit with error if any failures
        if failed_cities:
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
