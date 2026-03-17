#!/usr/bin/env python3
"""
Refresh metro-explorer JSON data from Redfin + Zillow sources.

Downloads the latest Redfin metro monthly data and Zillow ZHVI,
then regenerates all JSON files in metro-explorer/data/.

Usage:
  python3 scripts/refresh_data.py
"""

import json, os, sys, re, gzip, io, urllib.request
from pathlib import Path
import numpy as np

# ── Paths ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DATA_LAKE = Path("/Users/azizsunderji/Dropbox/Home Economics/Data")
REDFIN_PARQUET = DATA_LAKE / "Redfin" / "monthly_metro.parquet"
ZILLOW_ALL = DATA_LAKE / "Price" / "Zillow" / "Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.parquet"
ZILLOW_SFR_CSV = DATA_LAKE / "Price" / "Zillow" / "Metro_zhvi_uc_sfr_sm_sa_month.csv"
ZILLOW_CONDO_CSV = DATA_LAKE / "Price" / "Zillow" / "Metro_zhvi_uc_condo_sm_sa_month.csv"

REDFIN_URL = "https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/redfin_metro_market_tracker.tsv000.gz"
ZILLOW_URLS = {
    'all': "https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
    'sfr': "https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfr_sm_sa_month.csv",
    'condo': "https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_condo_sm_sa_month.csv",
}
TMP_DIR = SCRIPT_DIR.parent / "_tmp"

# ── Step 1: Download fresh data ────────────────────────
def download_sources():
    """Download latest Redfin + Zillow data. Works with or without data lake."""
    import duckdb
    TMP_DIR.mkdir(exist_ok=True)
    con = duckdb.connect()

    # -- Redfin --
    redfin_tsv = TMP_DIR / "redfin_metro.tsv"
    redfin_pq = TMP_DIR / "redfin_metro.parquet"

    # If data lake parquet exists, check if remote is newer
    use_local_redfin = False
    if REDFIN_PARQUET.exists():
        req = urllib.request.Request(REDFIN_URL, method='HEAD')
        resp = urllib.request.urlopen(req)
        remote_modified = resp.headers['Last-Modified']
        source_json = REDFIN_PARQUET.with_suffix('.source.json')
        if source_json.exists():
            with open(source_json) as f:
                src = json.load(f)
            if src.get('remote_last_modified') == remote_modified:
                print(f"Redfin: local parquet is current ({remote_modified})")
                use_local_redfin = True

    if not use_local_redfin:
        print("Downloading fresh Redfin metro data...")
        resp = urllib.request.urlopen(REDFIN_URL)
        raw = gzip.decompress(resp.read())
        print(f"  Downloaded {len(raw) / 1e6:.1f} MB")
        with open(redfin_tsv, 'wb') as f:
            f.write(raw)
        con.execute(f"""
            COPY (SELECT * FROM read_csv_auto('{redfin_tsv}', delim='\t', header=true, quote='"'))
            TO '{redfin_pq}' (FORMAT PARQUET)
        """)
        redfin_tsv.unlink()
        # Also update data lake if available
        if REDFIN_PARQUET.parent.exists():
            import shutil
            shutil.copy2(redfin_pq, REDFIN_PARQUET)
            req2 = urllib.request.Request(REDFIN_URL, method='HEAD')
            resp2 = urllib.request.urlopen(req2)
            with open(REDFIN_PARQUET.with_suffix('.source.json'), 'w') as f:
                json.dump({"type": "url", "url": REDFIN_URL, "format": "tsv.gz",
                           "remote_last_modified": resp2.headers['Last-Modified']}, f, indent=2)
            print(f"  Data lake parquet updated")

    redfin_path = str(REDFIN_PARQUET) if use_local_redfin else str(redfin_pq)
    latest = con.execute(f"SELECT MAX(period_end) FROM '{redfin_path}'").fetchone()[0]
    print(f"  Redfin latest date: {latest}")

    # -- Zillow (all three property types) --
    local_zillow = {
        'all': ZILLOW_ALL,
        'sfr': Path(str(ZILLOW_SFR_CSV).replace('.csv', '.parquet')) if Path(str(ZILLOW_SFR_CSV).replace('.csv', '.parquet')).exists() else ZILLOW_SFR_CSV,
        'condo': Path(str(ZILLOW_CONDO_CSV).replace('.csv', '.parquet')) if Path(str(ZILLOW_CONDO_CSV).replace('.csv', '.parquet')).exists() else ZILLOW_CONDO_CSV,
    }
    zillow_paths = {}
    for zkey, zurl in ZILLOW_URLS.items():
        local_path = local_zillow[zkey]
        if local_path.exists():
            zillow_paths[zkey] = str(local_path)
            print(f"Zillow {zkey}: using local {local_path.name}")
        else:
            print(f"Downloading Zillow {zkey}...")
            tmp_csv = TMP_DIR / f"zillow_{zkey}.csv"
            tmp_pq = TMP_DIR / f"zillow_{zkey}.parquet"
            resp = urllib.request.urlopen(zurl)
            with open(tmp_csv, 'wb') as f:
                f.write(resp.read())
            con.execute(f"""
                COPY (SELECT * FROM read_csv_auto('{tmp_csv}', header=true))
                TO '{tmp_pq}' (FORMAT PARQUET)
            """)
            tmp_csv.unlink()
            zillow_paths[zkey] = str(tmp_pq)

    return redfin_path, zillow_paths


# ── Step 2: Generate metro JSON files ──────────────────
def generate_jsons(redfin_path, zillow_paths):
    """Read Redfin parquet + Zillow ZHVI, generate all metro-explorer JSONs."""
    import duckdb
    con = duckdb.connect()

    print("Loading Redfin data (preferring seasonally adjusted where available)...")
    # For each region+property_type+period_end, prefer SA row if it exists, else use non-SA
    redfin = con.execute(f"""
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY region, property_type, period_end
                    ORDER BY is_seasonally_adjusted DESC
                ) as rn
            FROM '{redfin_path}'
            WHERE region_type = 'metro'
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY region, property_type, period_end
    """).df()
    n_sa = con.execute(f"SELECT COUNT(*) FROM '{redfin_path}' WHERE region_type='metro' AND is_seasonally_adjusted=true").fetchone()[0]
    print(f"  {n_sa:,} seasonally adjusted rows used where available")

    # Normalize column names
    redfin.columns = [c.lower().strip('"') for c in redfin.columns]

    # Normalize dates to end-of-month
    import calendar
    def to_eom(date_val):
        """Convert any date to end-of-month string."""
        if hasattr(date_val, 'strftime'):
            y, m = date_val.year, date_val.month
            last_day = calendar.monthrange(y, m)[1]
            return f"{y:04d}-{m:02d}-{last_day:02d}"
        s = str(date_val)[:10]
        parts = s.split('-')
        if len(parts) == 3:
            y, m = int(parts[0]), int(parts[1])
            last_day = calendar.monthrange(y, m)[1]
            return f"{y:04d}-{m:02d}-{last_day:02d}"
        return s

    # Map property types
    PT_MAP = {
        'All Residential': 'all',
        'Single Family Residential': 'sfh',
        'Condo/Co-op': 'condo',
    }

    # Build metro slug from region name
    def to_slug(name):
        # "Louisville, KY metro area" -> "louisville_ky"
        name = re.sub(r'\s+metro\s+area$', '', name, flags=re.IGNORECASE).strip()
        # "Louisville/Jefferson County, KY" -> take first part
        name = name.split('/')[0].strip()
        # Extract city and state
        parts = name.split(', ')
        if len(parts) >= 2:
            city = parts[0].strip()
            state = parts[-1].strip().split('-')[0].strip()
        else:
            city = name.strip()
            state = ''
        slug = re.sub(r'[^a-z0-9]+', '_', f"{city}_{state}".lower()).strip('_')
        return slug

    def to_display(name):
        name = re.sub(r'\s+metro\s+area$', '', name, flags=re.IGNORECASE).strip()
        name = name.split('/')[0].strip()
        return name

    # Metric column mapping: JSON key -> Redfin column
    METRIC_MAP = {
        'median_sale_price': 'median_sale_price',
        'inventory': 'inventory',
        'new_listings': 'new_listings',
        'homes_sold': 'homes_sold',
        'pending_sales': 'pending_sales',
        'months_supply': 'months_of_supply',
        'median_dom': 'median_dom',
        'sale_to_list_ratio': 'avg_sale_to_list',
        'pct_price_drops': 'price_drops',
        'off_market_in_2_weeks': 'off_market_in_two_weeks',
        'median_list_price': 'median_list_price',
        'median_ppsf': 'median_ppsf',
        'sold_above_list': 'sold_above_list',
    }

    # Find actual column names (handle case/quote variations)
    actual_cols = {c.lower().strip('"'): c for c in redfin.columns}
    # Remap to find closest match
    def get_col(target):
        target_lower = target.lower().replace(' ', '_')
        for k, v in actual_cols.items():
            if k.replace('"', '').replace(' ', '_') == target_lower:
                return v
        return None

    # Load Zillow ZHVI data for all property types
    print("Loading Zillow ZHVI data...")
    # pt_key -> { lookup: {region_name -> values}, us_vals: [...], date_cols: [...] }
    zillow_by_pt = {}
    zillow_date_cols = None
    pt_zillow_map = {'all': 'all', 'sfh': 'sfr', 'condo': 'condo'}
    for pt_key, z_key in pt_zillow_map.items():
        zpath = zillow_paths.get(z_key)
        if not zpath:
            continue
        # Read as CSV or parquet based on extension
        if zpath.endswith('.csv'):
            zdf = con.execute(f"SELECT * FROM read_csv_auto('{zpath}', header=true) WHERE \"RegionType\" = 'msa'").df()
            zus = con.execute(f"SELECT * FROM read_csv_auto('{zpath}', header=true) WHERE \"RegionType\" = 'country'").df()
        else:
            zdf = con.execute(f"SELECT * FROM '{zpath}' WHERE \"RegionType\" = 'msa'").df()
            zus = con.execute(f"SELECT * FROM '{zpath}' WHERE \"RegionType\" = 'country'").df()
        dcols = sorted([c for c in zdf.columns if re.match(r'^\d{4}-\d{2}-\d{2}$', c)])
        if zillow_date_cols is None:
            zillow_date_cols = dcols
        lookup = {}
        for _, row in zdf.iterrows():
            name = str(row.get('RegionName', ''))
            vals = [row[c] if not (isinstance(row[c], float) and np.isnan(row[c])) else None for c in dcols]
            lookup[name] = vals
        us_vals = None
        if len(zus) > 0:
            us_vals = [zus.iloc[0][c] if not (isinstance(zus.iloc[0][c], float) and np.isnan(zus.iloc[0][c])) else None for c in dcols]
        zillow_by_pt[pt_key] = {'lookup': lookup, 'us_vals': us_vals, 'date_cols': dcols}
        print(f"  Zillow {pt_key}: {len(lookup)} metros, {dcols[0]} to {dcols[-1]}")

    # Backward compat aliases
    zillow_lookup = zillow_by_pt.get('all', {}).get('lookup', {})
    zillow_us_vals = zillow_by_pt.get('all', {}).get('us_vals')

    # Group Redfin data by region
    regions = redfin.groupby('region')

    index_json = {}
    summary_json = {}
    histogram_json = {}
    us_median_data = {}
    all_metro_values = {}  # for computing US median

    # Collect all values per metric/property_type for US median
    print(f"Processing {len(regions)} metros...")

    for region_name, group in regions:
        slug = to_slug(region_name)
        display = to_display(region_name)

        if not slug:
            continue

        # Get all dates for this metro (from 'all' property type)
        all_pt = group[group['property_type'].map(lambda x: PT_MAP.get(x, '')) == 'all']
        if len(all_pt) == 0:
            continue

        dates = sorted(all_pt['period_end'].unique())
        date_strs = [to_eom(d) for d in dates]

        # Build property type data
        property_types = {}
        for pt_name, pt_key in PT_MAP.items():
            pt_group = group[group['property_type'] == pt_name].sort_values('period_end')
            if len(pt_group) == 0:
                continue

            pt_dates = [to_eom(d) for d in pt_group['period_end']]
            metrics = {}
            for json_key, redfin_col in METRIC_MAP.items():
                col = get_col(redfin_col)
                if col and col in pt_group.columns:
                    vals = pt_group[col].tolist()
                    # Convert NaN/NA to None
                    def to_num(v):
                        if v is None: return None
                        if isinstance(v, str):
                            if v.strip() in ('', 'NA', 'NaN', 'null'): return None
                            try: return float(v)
                            except: return None
                        if isinstance(v, (int, float)):
                            if np.isnan(v): return None
                            return float(v)
                        return None
                    vals = [to_num(v) for v in vals]
                    # Align to master date list
                    val_by_date = dict(zip(pt_dates, vals))
                    aligned = [val_by_date.get(d, None) for d in date_strs]
                    metrics[json_key] = aligned
                else:
                    metrics[json_key] = [None] * len(date_strs)

            property_types[pt_key] = metrics

        if not property_types:
            continue

        # Match Zillow ZHVI
        # Try matching by metro name
        zillow_match = None
        for zname in zillow_lookup:
            if display.lower().replace(' ', '') in zname.lower().replace(' ', ''):
                zillow_match = zname
                break
            # Try matching just the city name
            city_part = display.split(',')[0].strip().lower()
            if city_part in zname.lower():
                zillow_match = zname
                break

        city_data = {
            'name': display,
            'dates': date_strs,
            'property_types': property_types,
        }

        # Add Zillow price data per property type
        # Top-level price_dates/price_values = "all" (backward compat)
        for pt_key in ['all', 'sfh', 'condo']:
            zpt = zillow_by_pt.get(pt_key)
            if not zpt:
                continue
            z_match = None
            if slug == 'united_states':
                if zpt['us_vals']:
                    z_match = '__us__'
            else:
                for zname in zpt['lookup']:
                    if display.lower().replace(' ', '') in zname.lower().replace(' ', ''):
                        z_match = zname
                        break
                    city_part = display.split(',')[0].strip().lower()
                    if city_part in zname.lower():
                        z_match = zname
                        break
            if z_match:
                zvals = zpt['us_vals'] if z_match == '__us__' else zpt['lookup'][z_match]
                zdates = zpt['date_cols']
                if pt_key == 'all':
                    # Top-level for backward compat
                    city_data['price_dates'] = zdates
                    city_data['price_values'] = zvals
                # Per-property-type Zillow prices
                if pt_key in property_types:
                    property_types[pt_key]['zillow_price_dates'] = zdates
                    property_types[pt_key]['zillow_price_values'] = zvals

        # Write city JSON
        with open(DATA_DIR / f"{slug}.json", 'w') as f:
            json.dump(city_data, f, separators=(',', ':'))

        # Index entry
        index_json[slug] = display

        # Summary: latest values for 'all' property type
        if 'all' in property_types:
            latest = {}
            for k, vals in property_types['all'].items():
                # Get last non-null value
                non_null = [v for v in vals if v is not None]
                if non_null:
                    latest[k] = non_null[-1]
            summary_json[slug] = latest

        # Histogram: latest values per property type
        hist_entry = {}
        for pt_key, metrics in property_types.items():
            pt_latest = {}
            for k, vals in metrics.items():
                non_null = [v for v in vals if v is not None]
                if non_null:
                    pt_latest[k] = non_null[-1]
            # Compute derived ratios for histogram
            hs = pt_latest.get('homes_sold', 0)
            inv = pt_latest.get('inventory', 0)
            if hs and hs > 0:
                for mk in ['new_listings', 'pending_sales', 'inventory']:
                    if mk in pt_latest:
                        pt_latest[f'{mk}_pct_homes_sold'] = round(pt_latest[mk] / hs * 100, 2)
            if inv and inv > 0:
                for mk in ['homes_sold']:
                    if mk in pt_latest:
                        pt_latest[f'{mk}_pct_inventory'] = round(pt_latest[mk] / inv * 100, 2)
            hist_entry[pt_key] = pt_latest
        # Add Zillow sale price for histogram (all property types)
        for pt_key in ['all', 'sfh', 'condo']:
            if pt_key not in hist_entry:
                continue
            zpt = zillow_by_pt.get(pt_key)
            if not zpt:
                continue
            z_match = None
            if slug == 'united_states' and zpt['us_vals']:
                z_match = '__us__'
            else:
                for zname in zpt['lookup']:
                    if display.lower().replace(' ', '') in zname.lower().replace(' ', ''):
                        z_match = zname
                        break
                    city_part = display.split(',')[0].strip().lower()
                    if city_part in zname.lower():
                        z_match = zname
                        break
            if z_match:
                zvals = zpt['us_vals'] if z_match == '__us__' else zpt['lookup'][z_match]
                non_null_z = [v for v in zvals if v is not None]
                if non_null_z:
                    hist_entry[pt_key]['zillow_sale_price'] = non_null_z[-1]
        histogram_json[slug] = hist_entry

        # Collect for US median computation
        for pt_key, metrics in property_types.items():
            if pt_key not in all_metro_values:
                all_metro_values[pt_key] = {}
            for k, vals in metrics.items():
                if k.startswith('zillow_'):
                    continue  # skip Zillow price arrays (not aligned to date_strs)
                non_null = [(i, v) for i, v in enumerate(vals) if v is not None]
                if non_null:
                    if k not in all_metro_values[pt_key]:
                        all_metro_values[pt_key][k] = {}
                    for i, v in non_null:
                        if i < len(date_strs):
                            d = date_strs[i]
                            if d not in all_metro_values[pt_key][k]:
                                all_metro_values[pt_key][k][d] = []
                            all_metro_values[pt_key][k][d].append(v)

    # Sort index: United States first, then alphabetical
    sorted_index = {}
    if 'united_states' in index_json:
        sorted_index['united_states'] = index_json.pop('united_states')
    for k in sorted(index_json.keys()):
        sorted_index[k] = index_json[k]

    # Write index.json
    with open(DATA_DIR / "index.json", 'w') as f:
        json.dump(sorted_index, f, separators=(',', ':'))
    print(f"  index.json: {len(sorted_index)} metros")

    # Write summary.json
    with open(DATA_DIR / "summary.json", 'w') as f:
        json.dump(summary_json, f, separators=(',', ':'))
    print(f"  summary.json: {len(summary_json)} entries")

    # Write histogram_summary.json
    with open(DATA_DIR / "histogram_summary.json", 'w') as f:
        json.dump(histogram_json, f, separators=(',', ':'))
    print(f"  histogram_summary.json: {len(histogram_json)} entries")

    # Build US median JSON (median across all metros for each date/metric)
    print("Computing US median data...")
    # Collect all unique dates across all metros (end-of-month format)
    all_dates = set()
    for pt_metrics in all_metro_values.values():
        for date_vals in pt_metrics.values():
            all_dates.update(date_vals.keys())
    us_dates = sorted(all_dates)

    us_pt = {}
    for pt_key in ['all', 'sfh', 'condo']:
        if pt_key not in all_metro_values:
            continue
        pt_metrics = {}
        for metric_key, date_vals in all_metro_values[pt_key].items():
            aligned = []
            for d in us_dates:
                vals_for_date = date_vals.get(d, [])
                if vals_for_date:
                    aligned.append(float(np.median(vals_for_date)))
                else:
                    aligned.append(None)
            pt_metrics[metric_key] = aligned
        us_pt[pt_key] = pt_metrics

    us_median = {
        'name': 'U.S. Median',
        'dates': us_dates,
        'property_types': us_pt,
    }
    # Add Zillow price data for US (all property types)
    if zillow_us_vals:
        us_median['price_dates'] = zillow_date_cols
        us_median['price_values'] = zillow_us_vals
    for pt_key in ['all', 'sfh', 'condo']:
        zpt = zillow_by_pt.get(pt_key)
        if zpt and zpt['us_vals'] and pt_key in us_pt:
            us_pt[pt_key]['zillow_price_dates'] = zpt['date_cols']
            us_pt[pt_key]['zillow_price_values'] = zpt['us_vals']

    with open(DATA_DIR / "us_median.json", 'w') as f:
        json.dump(us_median, f, separators=(',', ':'))
    print("  us_median.json written")

    # Report
    latest_date = us_dates[-1] if us_dates else "unknown"
    print(f"\nDone! {len(sorted_index)} metros, data through {latest_date}")


# ── Main ───────────────────────────────────────────────
if __name__ == '__main__':
    redfin_path, zillow_path = download_sources()
    generate_jsons(redfin_path, zillow_path)
    # Clean up temp files
    if TMP_DIR.exists():
        import shutil
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    print("\nAll metro-explorer data refreshed.")
