#!/usr/bin/env python3
"""
Generate social media charts for all metros
"""

# --- Font Loader (must be imported BEFORE pyplot) ---
from pathlib import Path
import matplotlib.font_manager as fm

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
        import matplotlib.pyplot as plt
        plt.rcParams["font.family"] = [fam]
        print(f"Loaded font: {fam}")
    else:
        print(f"WARNING: No ABC Oracle fonts found at {font_dir}")

_load_abc_oracle()
# --- end loader ---

import os
import sys
import pandas as pd
from datetime import datetime
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import warnings

# Suppress matplotlib warnings about legend positioning
warnings.filterwarnings('ignore', message='.*posx and posy should be finite values.*')

# Add scripts directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from social_media_chart_generator_v2 import create_exact_metro_chart
from generate_charts import METRICS

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('social_charts_generation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def process_metro(args):
    """Process a single metro - designed for parallel execution"""
    metro_name, df, metrics, output_dir = args
    
    # Create safe filename from metro name
    metro_slug = metro_name.lower().replace(", ", "_").replace(" metro area", "").replace(" ", "_")
    metro_slug = "".join(c if c.isalnum() or c == "_" else "" for c in metro_slug)
    
    metro_dir = Path(output_dir) / metro_slug
    metro_dir.mkdir(parents=True, exist_ok=True)
    
    successful = 0
    failed = 0
    failed_metrics = []
    
    for metric in metrics:
        try:
            # Create metric config for new generator
            metric_config = {
                'name': metric['display_name'],
                'column': metric['column'],
                'unit': metric['unit'],
                'decimals': metric['decimals'],
                'is_percentage': metric['is_percentage'],
                'normalize_for_histogram': metric.get('normalize_for_histogram'),
                'normalized_unit_label': metric.get('normalized_unit_label')
            }
            
            # Output filename
            output_file = metro_dir / f"{metric['name']}.png"
            
            # Generate chart using new function
            success = create_exact_metro_chart(df, metro_name, metric_config, str(output_file))
            
            if success:
                successful += 1
            else:
                failed += 1
                failed_metrics.append(metric['name'])
                
        except Exception as e:
            failed += 1
            failed_metrics.append(metric['name'])
            logger.debug(f"  Error in {metro_name} - {metric['name']}: {str(e)}")
    
    return metro_name, successful, failed, failed_metrics

def main():
    """Generate social media charts for all metros"""
    
    # Parse command-line arguments for sharding
    import argparse
    parser = argparse.ArgumentParser(description='Generate social media charts')
    parser.add_argument('--shard', type=int, default=None,
                        help='Shard number for parallel processing')
    parser.add_argument('--total-shards', type=int, default=32,
                        help='Total number of shards')
    args = parser.parse_args()
    
    # Configuration - use relative path for GitHub Actions
    output_base = Path('social_charts')
    date_str = datetime.now().strftime('%Y-%m-%d')
    output_dir = output_base / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load data
    logger.info("Loading data...")
    data_file = Path(__file__).parent.parent / 'data' / 'weekly_housing_market_data.parquet'
    if not data_file.exists():
        logger.error(f"Data file not found: {data_file}")
        return 1
    
    df = pd.read_parquet(data_file)
    df['PERIOD_END'] = pd.to_datetime(df['PERIOD_END'])
    
    # Get all unique metros (excluding national aggregate)
    all_metros = df[
        (df['REGION_TYPE_ID'] == -2) & 
        (df['REGION_NAME'] != 'All Redfin Metros')
    ]['REGION_NAME'].unique()
    
    # Apply sharding if specified
    if args.shard is not None:
        # Filter metros for this shard using modulo (same as mobile charts)
        metros = [m for i, m in enumerate(sorted(all_metros)) if i % args.total_shards == args.shard]
        logger.info(f"Shard {args.shard}/{args.total_shards}: Processing {len(metros)} of {len(all_metros)} metros")
    else:
        metros = all_metros
        logger.info(f"Found {len(metros)} metros to process")
    
    logger.info(f"Generating {len(METRICS)} metrics per metro")
    logger.info(f"Total charts to generate: {len(metros) * len(METRICS)}")
    
    # Process metros in parallel
    start_time = time.time()
    
    # Prepare arguments for parallel processing
    args_list = [(metro, df, METRICS, output_dir) for metro in metros]
    
    # Track progress
    total_successful = 0
    total_failed = 0
    metros_with_failures = []
    
    # Use ProcessPoolExecutor for parallel processing
    max_workers = min(8, os.cpu_count() or 4)  # Limit workers to avoid memory issues
    logger.info(f"Using {max_workers} parallel workers")
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {executor.submit(process_metro, args): args[0] for args in args_list}
        
        # Process completed tasks
        completed = 0
        for future in as_completed(futures):
            completed += 1
            metro_name = futures[future]
            
            try:
                metro_name, successful, failed, failed_metrics = future.result()
                total_successful += successful
                total_failed += failed
                
                if failed > 0:
                    metros_with_failures.append((metro_name, failed_metrics))
                
                # Progress update every 10 metros
                if completed % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed
                    remaining = (len(metros) - completed) / rate if rate > 0 else 0
                    logger.info(f"Progress: {completed}/{len(metros)} metros "
                              f"({completed*100/len(metros):.1f}%) - "
                              f"Est. remaining: {remaining/60:.1f} min")
                
            except Exception as e:
                logger.error(f"Failed to process {metro_name}: {str(e)}")
                total_failed += len(METRICS)
                metros_with_failures.append((metro_name, [m['name'] for m in METRICS]))
    
    # Final summary
    elapsed_time = time.time() - start_time
    logger.info("="*50)
    logger.info(f"Generation complete in {elapsed_time/60:.1f} minutes")
    logger.info(f"Total successful: {total_successful}")
    logger.info(f"Total failed: {total_failed}")
    logger.info(f"Success rate: {total_successful/(total_successful+total_failed)*100:.1f}%")
    logger.info(f"Output directory: {output_dir}")
    
    # Report metros with failures
    if metros_with_failures:
        logger.info(f"\nMetros with failures ({len(metros_with_failures)}):")
        for metro, failed_metrics in metros_with_failures[:10]:  # Show first 10
            logger.info(f"  {metro}: {', '.join(failed_metrics)}")
        if len(metros_with_failures) > 10:
            logger.info(f"  ... and {len(metros_with_failures)-10} more")
    
    # Save summary report
    summary_file = output_dir / 'generation_summary.txt'
    with open(summary_file, 'w') as f:
        f.write(f"Social Media Charts Generation Summary\n")
        f.write(f"Generated: {datetime.now()}\n")
        f.write(f"Total metros: {len(metros)}\n")
        f.write(f"Metrics per metro: {len(METRICS)}\n")
        f.write(f"Total charts attempted: {len(metros) * len(METRICS)}\n")
        f.write(f"Successful: {total_successful}\n")
        f.write(f"Failed: {total_failed}\n")
        f.write(f"Success rate: {total_successful/(total_successful+total_failed)*100:.1f}%\n")
        f.write(f"Time elapsed: {elapsed_time/60:.1f} minutes\n")
        
        if metros_with_failures:
            f.write(f"\nMetros with failures:\n")
            for metro, failed_metrics in metros_with_failures:
                f.write(f"  {metro}: {', '.join(failed_metrics)}\n")
    
    logger.info(f"Summary saved to: {summary_file}")
    
    return 0 if total_failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())