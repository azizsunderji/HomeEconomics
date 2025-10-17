#!/usr/bin/env python3
"""
Save metadata after successful map generation
"""
import json
import pandas as pd
from datetime import datetime
from pathlib import Path

METADATA_FILE = Path(__file__).parent.parent / "data" / "last_update.json"
DATA_FILE = Path(__file__).parent.parent / "data" / "ZillowZip.csv"

def main():
    """Get the latest date from the data file and save metadata"""
    try:
        # Read the data file to get the latest date
        df = pd.read_csv(DATA_FILE)
        date_columns = [col for col in df.columns if '-' in col]

        if not date_columns:
            print("Error: No date columns found in data")
            return 1

        latest_date = date_columns[-1]

        # Save metadata
        METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            'last_date': latest_date,
            'checked_at': datetime.now().isoformat()
        }

        with open(METADATA_FILE, 'w') as f:
            json.dump(metadata, f, indent=2)

        print(f"✅ Saved metadata: {latest_date}")
        return 0

    except Exception as e:
        print(f"❌ Error saving metadata: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
