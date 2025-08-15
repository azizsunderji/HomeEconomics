#!/usr/bin/env python3
"""
Download latest Redfin weekly housing market data
"""

import os
import sys
import gzip
import shutil
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Redfin data URL
REDFIN_URL = "https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_covid19/weekly_housing_market_data_most_recent.tsv000.gz"

def download_redfin_data(output_dir: Path, force: bool = False) -> Path:
    """
    Download and process Redfin weekly data
    
    Args:
        output_dir: Directory to save the data
        force: Force re-download even if file exists
        
    Returns:
        Path to the parquet file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Output files
    gz_file = output_dir / "weekly_housing_market_data.tsv.gz"
    tsv_file = output_dir / "weekly_housing_market_data.tsv"
    parquet_file = output_dir / "weekly_housing_market_data.parquet"
    
    # Check if we need to download
    if parquet_file.exists() and not force:
        # Check if file is from today (Wednesday data)
        file_time = datetime.fromtimestamp(parquet_file.stat().st_mtime)
        if (datetime.now() - file_time).days < 1:
            logger.info(f"Using existing data from {file_time.strftime('%Y-%m-%d %H:%M')}")
            return parquet_file
    
    logger.info(f"Downloading Redfin data from {REDFIN_URL}")
    
    try:
        # Download with progress
        response = requests.get(REDFIN_URL, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(gz_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        print(f"\rDownloading: {progress:.1f}%", end='', flush=True)
        
        print()  # New line after progress
        logger.info(f"Downloaded {downloaded / 1024 / 1024:.1f} MB")
        
        # Decompress
        logger.info("Decompressing data...")
        with gzip.open(gz_file, 'rb') as f_in:
            with open(tsv_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        
        # Convert to parquet
        logger.info("Converting to parquet format...")
        df = pd.read_csv(tsv_file, sep='\t', low_memory=False)
        
        # Convert date columns
        date_cols = ['PERIOD_BEGIN', 'PERIOD_END', 'LAST_UPDATED']
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        
        # Save as parquet
        df.to_parquet(parquet_file, engine='pyarrow', compression='snappy')
        
        # Clean up intermediate files
        gz_file.unlink(missing_ok=True)
        tsv_file.unlink(missing_ok=True)
        
        logger.info(f"Data saved to {parquet_file}")
        logger.info(f"Total rows: {len(df):,}")
        logger.info(f"Latest date: {df['PERIOD_END'].max()}")
        
        return parquet_file
        
    except Exception as e:
        logger.error(f"Error downloading data: {e}")
        # Clean up partial files
        gz_file.unlink(missing_ok=True)
        tsv_file.unlink(missing_ok=True)
        raise

def main():
    """Main function for standalone execution"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Download Redfin housing data')
    parser.add_argument('--output', type=str, default='data', 
                       help='Output directory (default: data)')
    parser.add_argument('--force', action='store_true',
                       help='Force re-download even if file exists')
    
    args = parser.parse_args()
    
    try:
        parquet_file = download_redfin_data(Path(args.output), args.force)
        print(f"✅ Data ready: {parquet_file}")
        return 0
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())