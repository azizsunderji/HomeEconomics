"""
Event-study diff-in-diff framework.

For each store opening event:
  - Center time at t=0 (opening date)
  - Window: [-36, +36] months
  - Compare ZHVI growth in treatment ZIP vs. average of matched control ZIPs
  - Stack across events and compute average treatment effect by relative month

Output: DataFrame with columns
  chain | relative_month | treatment_zhvi_idx | control_zhvi_idx | diff
"""

from pathlib import Path

import numpy as np
import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

WINDOW_MONTHS = 36  # symmetric window around opening


def index_to_event_time(
    zhvi: pd.DataFrame,
    zip_code: str,
    open_date: pd.Timestamp,
    window: int = WINDOW_MONTHS,
) -> pd.DataFrame:
    """
    Extract ZHVI for a single ZIP in the event window and normalize
    to index = 100 at t=0.
    """
    start = open_date - pd.DateOffset(months=window)
    end = open_date + pd.DateOffset(months=window)

    mask = (zhvi["zip"] == zip_code) & (zhvi["date"] >= start) & (zhvi["date"] <= end)
    sub = zhvi.loc[mask, ["date", "zhvi"]].copy()

    if sub.empty:
        return pd.DataFrame()

    sub = sub.sort_values("date")

    # Relative month
    sub["relative_month"] = (
        (sub["date"].dt.year - open_date.year) * 12
        + (sub["date"].dt.month - open_date.month)
    )

    # Index to 100 at t=0 (or nearest available month)
    base_val = sub.loc[sub["relative_month"].abs().idxmin(), "zhvi"]
    if base_val == 0 or pd.isna(base_val):
        return pd.DataFrame()

    sub["zhvi_idx"] = (sub["zhvi"] / base_val) * 100

    return sub[["relative_month", "zhvi_idx"]]


def compute_event_study(
    zhvi: pd.DataFrame, matched: pd.DataFrame
) -> pd.DataFrame:
    """
    Stack event studies across all treatment-control pairs.

    Returns a DataFrame with:
      chain | relative_month | treatment_zhvi_idx | control_zhvi_idx
    """
    results = []

    events = matched.groupby(["treatment_zip", "chain", "open_year"])

    for (tz, chain, open_year), group in events:
        open_date = pd.Timestamp(year=int(open_year), month=7, day=1)

        # Treatment ZIP trajectory
        treat = index_to_event_time(zhvi, tz, open_date)
        if treat.empty or len(treat) < WINDOW_MONTHS:
            continue

        # Average control trajectory
        control_trajectories = []
        for _, crow in group.iterrows():
            ctrl = index_to_event_time(zhvi, crow["control_zip"], open_date)
            if not ctrl.empty and len(ctrl) >= WINDOW_MONTHS:
                control_trajectories.append(
                    ctrl.set_index("relative_month")["zhvi_idx"]
                )

        if not control_trajectories:
            continue

        ctrl_avg = (
            pd.concat(control_trajectories, axis=1)
            .mean(axis=1)
            .reset_index()
        )
        ctrl_avg.columns = ["relative_month", "control_zhvi_idx"]

        merged = treat.merge(ctrl_avg, on="relative_month", how="inner")
        merged = merged.rename(columns={"zhvi_idx": "treatment_zhvi_idx"})
        merged["chain"] = chain
        merged["treatment_zip"] = tz
        merged["open_year"] = open_year

        results.append(merged)

    if not results:
        print("  No valid event studies computed.")
        return pd.DataFrame()

    stacked = pd.concat(results, ignore_index=True)

    # Compute diff
    stacked["diff"] = stacked["treatment_zhvi_idx"] - stacked["control_zhvi_idx"]

    print(
        f"  Stacked event study: {len(stacked):,} rows, "
        f"{stacked['treatment_zip'].nunique()} events across "
        f"{stacked['chain'].nunique()} chains."
    )

    return stacked


def aggregate_by_chain(stacked: pd.DataFrame) -> pd.DataFrame:
    """
    Average across events within each chain to get the mean
    treatment vs. control trajectory.
    """
    if stacked.empty:
        return stacked

    agg = (
        stacked.groupby(["chain", "relative_month"])
        .agg(
            treatment_zhvi_idx=("treatment_zhvi_idx", "mean"),
            control_zhvi_idx=("control_zhvi_idx", "mean"),
            diff=("diff", "mean"),
            n_events=("treatment_zip", "nunique"),
        )
        .reset_index()
    )

    return agg


def run_event_study() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load data, run event study, save outputs."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    zhvi = pd.read_parquet(PROCESSED_DIR / "zhvi_long.parquet")
    matched = pd.read_csv(
        PROCESSED_DIR / "matched_controls.csv",
        dtype={"treatment_zip": str, "control_zip": str},
    )

    stacked = compute_event_study(zhvi, matched)
    agg = aggregate_by_chain(stacked)

    if not stacked.empty:
        stacked.to_parquet(PROCESSED_DIR / "event_study_stacked.parquet", index=False)
        agg.to_csv(PROCESSED_DIR / "event_study_agg.csv", index=False)
        print(f"  Saved event study outputs to {PROCESSED_DIR}")

    return stacked, agg


if __name__ == "__main__":
    run_event_study()
