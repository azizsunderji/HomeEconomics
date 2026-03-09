"""
Match treatment ZIPs (where a store opened) to control ZIPs.

Matching criteria:
  1. Baseline home price level (ZHVI in the 12 months before opening)
  2. Urban density proxy (population density from Census ZCTA data)

Uses nearest-neighbor matching (sklearn) on standardized features.
Each treatment ZIP gets K control ZIPs that did NOT receive a store
opening from the same chain in the event window.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Census ZCTA population data URL (ACS 5-year)
# Users should download this manually or via Census API
ZCTA_POP_FILE = RAW_DIR / "zcta_population.csv"

K_CONTROLS = 5  # number of matched controls per treatment ZIP


def load_baseline_prices(zhvi: pd.DataFrame, stores: pd.DataFrame) -> pd.DataFrame:
    """
    For each treatment ZIP × opening event, compute the baseline ZHVI
    (mean of the 12 months before store opening).
    """
    baselines = []

    for _, row in stores.iterrows():
        z = row["zip"]
        if pd.isna(row.get("open_year")):
            continue
        # Approximate opening date as July of opening year
        open_date = pd.Timestamp(year=int(row["open_year"]), month=7, day=1)
        window_start = open_date - pd.DateOffset(months=12)

        mask = (
            (zhvi["zip"] == z)
            & (zhvi["date"] >= window_start)
            & (zhvi["date"] < open_date)
        )
        subset = zhvi.loc[mask, "zhvi"]
        if len(subset) >= 6:
            baselines.append(
                {
                    "zip": z,
                    "chain": row["chain"],
                    "open_year": int(row["open_year"]),
                    "baseline_zhvi": subset.mean(),
                }
            )

    return pd.DataFrame(baselines)


def load_density() -> pd.DataFrame:
    """
    Load ZIP-level population density.

    If the ZCTA population file doesn't exist, create a stub that can be
    replaced with real Census data later.
    """
    if ZCTA_POP_FILE.exists():
        df = pd.read_csv(ZCTA_POP_FILE, dtype={"zip": str, "zcta": str})
        if "zip" not in df.columns and "zcta" in df.columns:
            df = df.rename(columns={"zcta": "zip"})
        return df[["zip", "population", "land_area_sq_mi"]].copy()

    print(
        f"  Warning: {ZCTA_POP_FILE} not found. "
        "Using ZHVI coverage as a density proxy."
    )
    return pd.DataFrame()


def build_matching_features(
    zhvi: pd.DataFrame, stores: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build feature matrices for treatment and candidate-control ZIPs.

    Returns (treatment_df, universe_df) with columns:
      zip, chain, open_year, baseline_zhvi, density_proxy
    """
    # Treatment baselines
    treatment = load_baseline_prices(zhvi, stores)
    if treatment.empty:
        print("  No treatment ZIPs with valid baselines.")
        return treatment, pd.DataFrame()

    # Compute a simple density proxy: number of months with ZHVI data
    # (denser/more-urban ZIPs tend to have longer ZHVI coverage)
    density_raw = load_density()

    zip_coverage = (
        zhvi.groupby("zip")["date"]
        .count()
        .reset_index()
        .rename(columns={"date": "coverage_months"})
    )

    if not density_raw.empty and "population" in density_raw.columns:
        universe = zip_coverage.merge(density_raw, on="zip", how="left")
        universe["density_proxy"] = universe["population"] / universe[
            "land_area_sq_mi"
        ].replace(0, np.nan)
        universe["density_proxy"] = universe["density_proxy"].fillna(
            universe["coverage_months"]
        )
    else:
        universe = zip_coverage.copy()
        universe["density_proxy"] = universe["coverage_months"]

    # Baseline ZHVI for all ZIPs (most recent 12 months as a simple proxy)
    latest = zhvi["date"].max()
    recent = zhvi[zhvi["date"] >= latest - pd.DateOffset(months=12)]
    zip_means = (
        recent.groupby("zip")["zhvi"]
        .mean()
        .reset_index()
        .rename(columns={"zhvi": "baseline_zhvi"})
    )
    universe = universe.merge(zip_means, on="zip", how="inner")

    # Merge density proxy onto treatment
    treatment = treatment.merge(
        universe[["zip", "density_proxy"]], on="zip", how="left"
    )
    treatment["density_proxy"] = treatment["density_proxy"].fillna(
        treatment["density_proxy"].median()
    )

    return treatment, universe


def match_controls(
    treatment: pd.DataFrame, universe: pd.DataFrame, k: int = K_CONTROLS
) -> pd.DataFrame:
    """
    For each treatment ZIP, find k nearest-neighbor control ZIPs
    that are NOT treatment ZIPs for the same chain.
    """
    if treatment.empty or universe.empty:
        return pd.DataFrame()

    features = ["baseline_zhvi", "density_proxy"]
    scaler = StandardScaler()

    # Fit on universe
    X_universe = scaler.fit_transform(universe[features].fillna(0))
    nn = NearestNeighbors(n_neighbors=k + 50, metric="euclidean")
    nn.fit(X_universe)

    treatment_zips_by_chain = treatment.groupby("chain")["zip"].apply(set).to_dict()

    matches = []
    for _, trow in treatment.iterrows():
        x = scaler.transform(
            pd.DataFrame([trow[features].fillna(0).values], columns=features)
        )
        dists, idxs = nn.kneighbors(x)

        chain = trow["chain"]
        exclude = treatment_zips_by_chain.get(chain, set())
        control_count = 0

        for dist, idx in zip(dists[0], idxs[0]):
            czip = universe.iloc[idx]["zip"]
            if czip in exclude or czip == trow["zip"]:
                continue
            matches.append(
                {
                    "treatment_zip": trow["zip"],
                    "chain": chain,
                    "open_year": trow["open_year"],
                    "control_zip": czip,
                    "match_distance": dist,
                }
            )
            control_count += 1
            if control_count >= k:
                break

    result = pd.DataFrame(matches)
    print(
        f"  Matched {result['treatment_zip'].nunique()} treatment ZIPs "
        f"to {len(result)} treatment-control pairs."
    )
    return result


def run_matching() -> pd.DataFrame:
    """Load data, run matching, save output."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Load processed data
    zhvi_path = PROCESSED_DIR / "zhvi_long.parquet"
    stores_path = PROCESSED_DIR / "store_locations_wiki.csv"

    if not zhvi_path.exists():
        raise FileNotFoundError(
            f"{zhvi_path} not found. Run fetch_zhvi.py first."
        )
    if not stores_path.exists():
        raise FileNotFoundError(
            f"{stores_path} not found. Run scrape_stores.py first."
        )

    zhvi = pd.read_parquet(zhvi_path)
    stores = pd.read_csv(stores_path, dtype={"zip": str})

    treatment, universe = build_matching_features(zhvi, stores)
    matched = match_controls(treatment, universe)

    if not matched.empty:
        out = PROCESSED_DIR / "matched_controls.csv"
        matched.to_csv(out, index=False)
        print(f"  Saved to {out}")

    return matched


if __name__ == "__main__":
    run_matching()
