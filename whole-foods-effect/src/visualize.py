"""
Stacked event-study visualization.

Produces a multi-panel chart showing average pre/post home price trajectories
for treatment vs. control ZIPs, by chain.

Style: Arial font, #f6f7f3 background, minimalist, small fonts.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "notebooks"

# ── Style ────────────────────────────────────────────────────────────────────

BACKGROUND = "#f6f7f3"
TREATMENT_COLOR = "#2d6a4f"
CONTROL_COLOR = "#adb5bd"
DIFF_COLOR = "#e07a5f"

CHAIN_COLORS = {
    "Whole Foods": "#00674b",
    "Trader Joe's": "#c1121f",
    "Wegmans": "#003f88",
    "Starbucks": "#00704A",
    "Aldi": "#00005f",
}

plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 8,
        "axes.facecolor": BACKGROUND,
        "figure.facecolor": BACKGROUND,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.5,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
    }
)


def plot_event_study(agg: pd.DataFrame, save: bool = True) -> plt.Figure:
    """
    Stacked event-study chart: one panel per chain.

    Each panel shows:
      - Treatment ZIP trajectory (indexed to 100 at t=0)
      - Control ZIP trajectory
      - Shaded diff
      - Vertical line at t=0
    """
    chains = sorted(agg["chain"].unique())
    n = len(chains)
    if n == 0:
        print("No data to plot.")
        return None

    fig, axes = plt.subplots(
        1, n, figsize=(3.2 * n, 3.5), sharey=True, constrained_layout=True
    )
    if n == 1:
        axes = [axes]

    for ax, chain in zip(axes, chains):
        sub = agg[agg["chain"] == chain].sort_values("relative_month")
        color = CHAIN_COLORS.get(chain, TREATMENT_COLOR)

        ax.plot(
            sub["relative_month"],
            sub["treatment_zhvi_idx"],
            color=color,
            linewidth=1.5,
            label="Treatment ZIPs",
        )
        ax.plot(
            sub["relative_month"],
            sub["control_zhvi_idx"],
            color=CONTROL_COLOR,
            linewidth=1.2,
            linestyle="--",
            label="Control ZIPs",
        )

        # Shade the diff
        ax.fill_between(
            sub["relative_month"],
            sub["control_zhvi_idx"],
            sub["treatment_zhvi_idx"],
            alpha=0.12,
            color=color,
        )

        # t=0 line
        ax.axvline(0, color="#555555", linewidth=0.6, linestyle=":", alpha=0.7)

        ax.set_title(chain, fontsize=9, fontweight="bold", pad=8)
        ax.set_xlabel("Months from opening", fontsize=7)

        if ax == axes[0]:
            ax.set_ylabel("Home Value Index (100 = opening)", fontsize=7)

        # Sample size annotation
        n_events = sub["n_events"].max() if "n_events" in sub.columns else "?"
        ax.text(
            0.97,
            0.03,
            f"n = {n_events}",
            transform=ax.transAxes,
            fontsize=6,
            ha="right",
            va="bottom",
            color="#888888",
        )

        ax.tick_params(labelsize=6)

    # Legend on first axis
    axes[0].legend(fontsize=6, loc="upper left", frameon=False)

    fig.suptitle(
        "Home Price Trajectories Around Store Openings",
        fontsize=11,
        fontweight="bold",
        y=1.02,
    )

    if save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for fmt in ["svg", "png"]:
            path = OUTPUT_DIR / f"event_study.{fmt}"
            fig.savefig(path, dpi=200, bbox_inches="tight")
            print(f"  Saved {path}")

    return fig


def plot_diff_only(agg: pd.DataFrame, save: bool = True) -> plt.Figure:
    """
    Single-panel chart showing the treatment-control diff for all chains.
    """
    chains = sorted(agg["chain"].unique())
    if not chains:
        return None

    fig, ax = plt.subplots(figsize=(6, 3.5), constrained_layout=True)

    for chain in chains:
        sub = agg[agg["chain"] == chain].sort_values("relative_month")
        color = CHAIN_COLORS.get(chain, TREATMENT_COLOR)

        ax.plot(
            sub["relative_month"],
            sub["diff"],
            color=color,
            linewidth=1.3,
            label=chain,
        )

    ax.axhline(0, color="#555555", linewidth=0.5, linestyle="-", alpha=0.5)
    ax.axvline(0, color="#555555", linewidth=0.6, linestyle=":", alpha=0.7)

    ax.set_xlabel("Months from opening", fontsize=7)
    ax.set_ylabel("Treatment − Control (index points)", fontsize=7)
    ax.set_title(
        "Excess Home Price Growth Around Store Openings",
        fontsize=10,
        fontweight="bold",
    )
    ax.legend(fontsize=6, frameon=False)
    ax.tick_params(labelsize=6)

    if save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for fmt in ["svg", "png"]:
            path = OUTPUT_DIR / f"event_study_diff.{fmt}"
            fig.savefig(path, dpi=200, bbox_inches="tight")
            print(f"  Saved {path}")

    return fig


def run_visualization():
    """Load aggregated event study data and produce charts."""
    agg_path = PROCESSED_DIR / "event_study_agg.csv"
    if not agg_path.exists():
        raise FileNotFoundError(
            f"{agg_path} not found. Run event_study.py first."
        )

    agg = pd.read_csv(agg_path)
    plot_event_study(agg)
    plot_diff_only(agg)
    print("Done.")


if __name__ == "__main__":
    run_visualization()
