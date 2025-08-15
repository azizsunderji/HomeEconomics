#!/usr/bin/env python3
"""
Exact Metro Chart Generator
Replicates the Denver chart styling for any metro,
without hard-coded, machine-specific font paths.
"""

from datetime import datetime, timedelta
import os

import numpy as np
import pandas as pd

# Set backend BEFORE importing pyplot
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from matplotlib import gridspec
from matplotlib.patches import Rectangle, Patch
from matplotlib.lines import Line2D

# Color palette - EXACT from Denver
COLORS = {
    "blue": "#0BB4FF",
    "yellow": "#FEC439",
    "background": "#F6F7F3",
    "red": "#F4743B",
    "light_red": "#FBCAB5",
    "black": "#3D3733",
    "gray": "#808080",
    "green": "#67A275",
    "light_green": "#C6DCCB",
}

# Fonts are registered globally by make_charts._load_abc_oracle().
# Do NOT hard-code font family names or absolute file paths here.
def setup_fonts():
    """
    Respect the globally registered fonts.
    (Optional) If HE_FONT_DIR is set locally, the loader in make_charts.py will pick it up.
    """
    plt.rcParams["font.size"] = 18  # keep Denver-ish sizing; family comes from the global loader


def format_value(value, unit_label, decimals, is_percentage):
    """Format value for display - EXACT from Denver"""
    if unit_label == "$":
        return f"${value:,.0f}"
    elif unit_label == "%" or is_percentage:
        if value < 1:
            return f"{value*100:.{decimals}f}%"
        else:
            return f"{value:.{decimals}f}%"
    else:
        return f"{value:,.{decimals}f}"


def normalize_metric_for_histograms(df, column_name, normalization_type, historical_avg=None):
    """Normalize metric values - EXACT from Denver"""
    if normalization_type == "per_1000_active":
        if "ACTIVE_LISTINGS" in df.columns:
            return (df[column_name] / df["ACTIVE_LISTINGS"]) * 1000
        else:
            return df[column_name]
    elif normalization_type == "percent_of_new":
        if "ADJUSTED_AVERAGE_NEW_LISTINGS" in df.columns:
            return (df[column_name] / df["ADJUSTED_AVERAGE_NEW_LISTINGS"]) * 100
        else:
            return df[column_name]
    elif normalization_type == "percent_of_historical":
        if historical_avg is not None and historical_avg > 0:
            return (df[column_name] / historical_avg) * 100
        else:
            return df[column_name]
    else:
        return df[column_name]


def create_exact_metro_chart(df, metro_name, metric_config, output_filename):
    """
    Create chart for a metro using Denver styling.

    Args:
        df: Full dataframe with all data
        metro_name: Name of metro (e.g., 'Denver, CO metro area')
        metric_config: Dictionary with metric configuration
        output_filename: Where to save the chart
    """
    # Setup fonts (size only; family already set globally)
    setup_fonts()

    # Extract metric configuration
    column_name = metric_config["column"]
    metric_name = metric_config["name"]
    unit_label = metric_config.get("unit", "")
    decimals = metric_config.get("decimals", 1)
    is_percentage = metric_config.get("is_percentage", False)
    normalize_for_histogram = metric_config.get("normalize_for_histogram", None)
    normalized_unit_label = metric_config.get("normalized_unit_label", None)

    # Get metro display name (city only, no "metro area")
    metro_display = metro_name.split(",")[0] if "," in metro_name else metro_name

    # Filter for this metro
    metric_data = df[
        (df["REGION_NAME"] == metro_name) & (df["DURATION"] == "4 weeks")
    ].copy()
    metric_data = metric_data.sort_values("PERIOD_END")

    # Get latest values
    latest = metric_data.iloc[-1]
    latest_date = latest["PERIOD_END"]
    latest_value = latest[column_name]
    current_week = latest_date.isocalendar()[1]
    current_month = latest_date.month
    current_day = latest_date.day

    # Create figure - EXACT dimensions from Denver
    fig = plt.figure(figsize=(9, 26), facecolor=COLORS["background"])

    # Create GridSpec - EXACT from Denver
    gs = gridspec.GridSpec(
        5,
        1,
        height_ratios=[2.5, 1.5, 1.2, 2, 2],
        top=0.88,
        bottom=0.02,
        left=0.12,
        right=0.95,
        hspace=0.85,
    )

    # Title - use global family; set bold via fontweight
    fig.text(
        0.5,
        0.985,
        metric_name.upper(),
        ha="center",
        va="top",
        fontsize=32,
        fontweight="bold",
        color=COLORS["black"],
    )
    fig.text(
        0.5,
        0.965,
        f'{metro_display} Metro • {latest_date.strftime("%B %d, %Y")}',
        ha="center",
        va="top",
        fontsize=20,
        color=COLORS["gray"],
    )

    # ============================
    # 1. HISTORICAL TREND - EXACT from Denver
    # ============================
    ax_history = fig.add_subplot(gs[0])
    ax_history.set_facecolor(COLORS["background"])

    # Plot the filled area
    ax_history.fill_between(
        metric_data["PERIOD_END"],
        0,
        metric_data[column_name],
        color=COLORS["blue"],
        alpha=0.3,
    )

    # Highlight the 3-month periods - EXACT logic
    for year in range(latest_date.year, 2016, -1):
        if year == latest_date.year:
            year_end_date = latest_date
        else:
            year_all = metric_data[metric_data["PERIOD_END"].dt.year == year]
            if len(year_all) == 0:
                continue
            try:
                target_date = datetime(year, current_month, current_day)
            except Exception:
                target_date = datetime(year, current_month, 28)
            closest_idx = (year_all["PERIOD_END"] - target_date).abs().idxmin()
            year_end_date = year_all.loc[closest_idx, "PERIOD_END"]

        year_start_date = year_end_date - timedelta(days=90)
        period_data = metric_data[
            (metric_data["PERIOD_END"] >= year_start_date)
            & (metric_data["PERIOD_END"] <= year_end_date)
        ]

        if len(period_data) > 0:
            import matplotlib as mpl

            mpl.rcParams["hatch.linewidth"] = 0.3
            ax_history.fill_between(
                period_data["PERIOD_END"],
                0,
                period_data[column_name],
                color="none",
                edgecolor=COLORS["blue"],
                hatch="/////",
                alpha=1.0,
                linewidth=0,
            )

    # Add vertical lines and dots
    for year in range(latest_date.year, 2016, -1):
        if year == latest_date.year:
            year_end_date = latest_date
        else:
            year_all = metric_data[metric_data["PERIOD_END"].dt.year == year]
            if len(year_all) == 0:
                continue
            try:
                target_date = datetime(year, current_month, current_day)
            except Exception:
                target_date = datetime(year, current_month, 28)
            closest_idx = (year_all["PERIOD_END"] - target_date).abs().idxmin()
            year_end_date = year_all.loc[closest_idx, "PERIOD_END"]

        value_at_date = metric_data[metric_data["PERIOD_END"] == year_end_date][
            column_name
        ].iloc[0]
        ax_history.vlines(
            year_end_date, 0, value_at_date, color=COLORS["black"], linewidth=1.5, zorder=10
        )
        ax_history.plot(
            year_end_date,
            value_at_date,
            "o",
            color=COLORS["black"],
            markersize=4,
            zorder=17,
        )

    # Plot the line on top
    ax_history.plot(
        metric_data["PERIOD_END"],
        metric_data[column_name],
        color=COLORS["blue"],
        linewidth=2,
        zorder=15,
    )

    # Format - EXACT from Denver
    ax_history.set_ylabel(
        f"{metric_name.title()}",
        fontsize=20,
        fontweight="normal",
        color=COLORS["black"],
        labelpad=15,
    )
    ax_history.text(
        0.0,
        1.12,
        "Historical Trend: Weekly Data",
        transform=ax_history.transAxes,
        fontsize=22,
        fontweight="bold",
        ha="left",
        va="top",
        color=COLORS["black"],
    )

    ax_history.xaxis.set_major_locator(mdates.YearLocator())
    ax_history.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_history.xaxis.set_minor_locator(plt.NullLocator())

    ax_history.grid(True, alpha=0.3, axis="y")
    ax_history.tick_params(axis="both", colors=COLORS["black"], labelsize=16)
    ax_history.tick_params(axis="y", which="both", length=0)
    ax_history.tick_params(
        axis="x", which="major", length=8, direction="out", width=1, color=COLORS["black"]
    )

    for spine in ax_history.spines.values():
        spine.set_visible(False)

    # Add legend
    hatch_patch = Rectangle(
        (0, 0), 1, 1, facecolor="none", edgecolor=COLORS["blue"], hatch="/////", linewidth=0
    )
    line_with_dot = Line2D(
        [0], [0], color=COLORS["black"], linewidth=1.5, marker="o", markersize=4, markerfacecolor=COLORS["black"]
    )

    legend = ax_history.legend(
        [hatch_patch, line_with_dot],
        ["3-month Lookback", "Current Week"],
        loc="upper right",
        fontsize=14,
        frameon=True,
        fancybox=False,
        shadow=False,
        framealpha=1.0,
        facecolor=COLORS["background"],
        edgecolor="none",
        bbox_to_anchor=(1, 1.25),
    )

    # ============================
    # 2. HISTORICAL RANKING - EXACT from Denver
    # ============================
    ax_ranking = fig.add_subplot(gs[1])
    ax_ranking.set_facecolor(COLORS["background"])

    # Get same week data for each year
    years = []
    values = []
    start_year = 2019

    for year in range(start_year, latest_date.year + 1):
        year_data = metric_data[metric_data["PERIOD_END"].dt.year == year]

        if len(year_data) > 0:
            week_data = year_data[year_data["PERIOD_END"].dt.isocalendar().week == current_week]

            if len(week_data) == 0:
                try:
                    target_date = datetime(year, latest_date.month, latest_date.day)
                except Exception:
                    target_date = datetime(year, latest_date.month, 28)

                closest_idx = (year_data["PERIOD_END"] - target_date).abs().idxmin()
                value = year_data.loc[closest_idx, column_name]
            else:
                value = week_data.iloc[0][column_name]

            years.append(str(year))
            values.append(value)

    # Sort by values (highest to lowest)
    sorted_data = sorted(zip(years, values), key=lambda x: x[1], reverse=True)
    sorted_years = [x[0] for x in sorted_data]
    sorted_values = [x[1] for x in sorted_data]

    # Grid first
    ax_ranking.grid(True, alpha=0.3, axis="y", zorder=0)

    # Create bars
    x_pos = np.arange(len(sorted_years))
    bar_colors = [COLORS["black"] if y == str(latest_date.year) else COLORS["blue"] for y in sorted_years]

    bars = ax_ranking.bar(
        x_pos,
        sorted_values,
        color=bar_colors,
        edgecolor=bar_colors,
        linewidth=0,
        alpha=1.0,
        zorder=3,
    )

    # Add value labels
    for i, (bar, val) in enumerate(zip(bars, sorted_values)):
        label = format_value(val, unit_label, decimals, is_percentage)
        ax_ranking.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(sorted_values) * 0.02,
            label,
            ha="center",
            va="bottom",
            fontsize=16,
            color=COLORS["black"],
            fontweight="light",
        )

    ax_ranking.set_ylabel(
        f"{metric_name.title()}",
        fontsize=20,
        fontweight="normal",
        color=COLORS["black"],
        labelpad=15,
    )
    ax_ranking.text(
        0.0,
        1.3,
        f'Historical Comparison: Same Week ({latest_date.strftime("%B %d")})',
        transform=ax_ranking.transAxes,
        fontsize=22,
        fontweight="bold",
        ha="left",
        va="top",
        color=COLORS["black"],
    )

    ax_ranking.set_xticks(x_pos)
    ax_ranking.set_xticklabels(sorted_years)
    ax_ranking.tick_params(axis="both", which="both", length=0)
    ax_ranking.tick_params(axis="both", colors=COLORS["black"], labelsize=16)
    ax_ranking.set_xlim(-0.5, len(sorted_years) - 0.5)

    for spine in ax_ranking.spines.values():
        spine.set_visible(False)

    # ============================
    # 3. MOMENTUM TRIANGLE - EXACT from Denver
    # ============================
    ax_momentum = fig.add_subplot(gs[2])
    ax_momentum.set_facecolor(COLORS["background"])

    # Calculate momentum data
    three_months_ago = latest_date - timedelta(days=90)
    past_data = metric_data[metric_data["PERIOD_END"] <= three_months_ago]
    if len(past_data) > 0:
        past_value = past_data.iloc[-1][column_name]
        change = latest_value - past_value
        change_label = f"{change:+.{decimals}f}"
    else:
        change = 0
        change_label = "0"

    # Calculate historical average change
    historical_changes = []
    for year in range(2019, latest_date.year):
        year_data = metric_data[
            (metric_data["PERIOD_END"].dt.year == year)
            & (metric_data["PERIOD_END"].dt.month == current_month)
        ]
        if len(year_data) > 0:
            year_latest = year_data.iloc[-1]
            year_latest_date = year_latest["PERIOD_END"]
            year_latest_value = year_latest[column_name]

            year_three_months_ago = year_latest_date - timedelta(days=90)
            year_past_data = metric_data[
                (metric_data["PERIOD_END"] <= year_three_months_ago)
                & (metric_data["PERIOD_END"] >= year_three_months_ago - timedelta(days=30))
            ]

            if len(year_past_data) > 0:
                year_past_value = year_past_data.iloc[-1][column_name]
                year_change = year_latest_value - year_past_value
                historical_changes.append(year_change)

    avg_historical_change = np.mean(historical_changes) if historical_changes else 0

    # Triangles
    triangle_width = 0.9
    triangle_current = mpatches.Polygon(
        [(0, 0), (triangle_width, change), (triangle_width, 0)],
        closed=True,
        fill=False,
        edgecolor=COLORS["black"],
        linewidth=2,
        hatch="///",
        alpha=1.0,
    )

    triangle_historical = mpatches.Polygon(
        [(0, 0), (triangle_width, avg_historical_change), (triangle_width, 0)],
        closed=True,
        fill=True,
        facecolor=COLORS["blue"],
        edgecolor=COLORS["blue"],
        linewidth=2,
        alpha=0.8,
    )

    ax_momentum.add_patch(triangle_historical)
    ax_momentum.add_patch(triangle_current)

    ax_momentum.text(
        0.0,
        1.35,
        "Momentum: 3-Month Change",
        transform=ax_momentum.transAxes,
        fontsize=22,
        fontweight="bold",
        ha="left",
        va="top",
        color=COLORS["black"],
    )

    # Value labels
    current_label = f"{change:+.{decimals}f} {unit_label}"
    historical_label = f"{avg_historical_change:+.{decimals}f} {unit_label}"

    ax_momentum.text(
        triangle_width + 0.05,
        change,
        current_label,
        fontsize=18,
        va="center",
        color=COLORS["black"],
        fontweight="bold",
    )
    ax_momentum.text(
        triangle_width + 0.05,
        avg_historical_change,
        historical_label,
        fontsize=16,
        va="center",
        color=COLORS["blue"],
    )

    legend_elements = [
        Patch(facecolor="none", edgecolor=COLORS["black"], hatch="///", label="Current Period"),
        Patch(facecolor=COLORS["blue"], edgecolor=COLORS["blue"], alpha=0.8, label="Historical Average"),
    ]
    ax_momentum.legend(
        handles=legend_elements,
        loc="upper right",
        fontsize=14,
        frameon=True,
        fancybox=False,
        shadow=False,
        framealpha=1.0,
        facecolor=COLORS["background"],
        edgecolor="none",
        bbox_to_anchor=(1, 1.25),
    )

    # Limits
    max_abs_change = max(abs(change), abs(avg_historical_change))
    min_height = abs(latest_value) * 0.1
    y_max = min_height if max_abs_change < min_height else max_abs_change * 1.3
    y_max_with_space = y_max * 1.6

    ax_momentum.set_xlim(-0.1, 1.3)
    ax_momentum.set_ylim(-y_max, y_max_with_space)

    for spine in ax_momentum.spines.values():
        spine.set_visible(False)
    ax_momentum.set_xticks([])
    ax_momentum.set_yticks([])

    # ============================
    # 4. CURRENT LEVEL HISTOGRAM - EXACT from Denver
    # ============================
    ax_hist1 = fig.add_subplot(gs[3])
    ax_hist1.set_facecolor(COLORS["background"])

    metro_data_all = df[
        (df["REGION_TYPE_ID"] == -2)
        & (df["DURATION"] == "4 weeks")
        & (df["PERIOD_END"] == latest_date)
        & (df["REGION_NAME"] != "All Redfin Metros")
    ].copy()

    current_data = metro_data_all.dropna(subset=[column_name]).copy()

    if normalize_for_histogram:
        current_data["normalized_value"] = normalize_metric_for_histograms(
            current_data, column_name, normalize_for_histogram
        )
        values_for_hist = current_data["normalized_value"]
    else:
        values_for_hist = current_data[column_name]

    # Remove outliers
    q1 = values_for_hist.quantile(0.25)
    q3 = values_for_hist.quantile(0.75)
    iqr = q3 - q1
    lower_bound = q1 - 2.5 * iqr
    upper_bound = q3 + 2.5 * iqr

    target_row = current_data[current_data["REGION_NAME"] == metro_name]
    if len(target_row) > 0:
        if normalize_for_histogram:
            target_value_hist = target_row["normalized_value"].iloc[0]
        else:
            target_value_hist = target_row[column_name].iloc[0]

        # Expand bounds to include target if needed
        if target_value_hist < lower_bound:
            lower_bound = target_value_hist * 0.9
        if target_value_hist > upper_bound:
            upper_bound = target_value_hist * 1.1

        values_filtered = values_for_hist[
            (values_for_hist >= lower_bound) & (values_for_hist <= upper_bound)
        ]

        # Histogram
        n_bins = 100
        counts, bins, patches = ax_hist1.hist(
            values_filtered,
            bins=n_bins,
            color=COLORS["blue"],
            alpha=0.3,
            edgecolor=COLORS["blue"],
            linewidth=0.5,
        )

        # Highlight target bin
        target_bin_idx = np.digitize(target_value_hist, bins) - 1
        if 0 <= target_bin_idx < len(patches):
            patches[target_bin_idx].set_facecolor(COLORS["black"])
            patches[target_bin_idx].set_edgecolor(COLORS["black"])
            patches[target_bin_idx].set_alpha(1.0)

        # Percentile
        percentile = (values_for_hist < target_value_hist).sum() / len(values_for_hist) * 100

        # Median line
        median_val = np.median(values_for_hist)
        ax_hist1.axvline(median_val, color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7)

        # Legend
        target_label = format_value(
            target_value_hist if normalize_for_histogram else latest_value,
            normalized_unit_label if normalize_for_histogram else unit_label,
            decimals,
            is_percentage,
        )
        median_label = format_value(
            median_val,
            normalized_unit_label if normalize_for_histogram else unit_label,
            decimals,
            is_percentage,
        )

        legend_elements = [
            Rectangle((0, 0), 1, 1, facecolor=COLORS["black"], alpha=1.0, label=f"{metro_display}: {target_label}"),
            Rectangle((0, 0), 1, 1, facecolor=COLORS["blue"], alpha=0.3, label="Other metros"),
            Line2D([0], [0], color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7, label=f"Median: {median_label}"),
        ]
        ax_hist1.legend(
            handles=legend_elements,
            loc="upper right",
            fontsize=14,
            frameon=True,
            fancybox=False,
            shadow=False,
            framealpha=1.0,
            facecolor=COLORS["background"],
            edgecolor="none",
        )

        # Formatting
        x_label = f'{metric_name.title()} {normalized_unit_label if normalize_for_histogram else ""}'
        ax_hist1.set_xlabel(x_label, fontsize=20, fontweight="normal", color=COLORS["black"], labelpad=10)
        ax_hist1.set_ylabel("Number of Metros", fontsize=20, fontweight="normal", color=COLORS["black"], labelpad=15)
        ax_hist1.text(
            0.0,
            1.15,
            f"Current {metric_name.title()} ({percentile:.0f}th percentile)",
            transform=ax_hist1.transAxes,
            fontsize=22,
            fontweight="bold",
            ha="left",
            va="top",
            color=COLORS["black"],
        )

        ax_hist1.grid(True, alpha=0.3, axis="y")
        ax_hist1.tick_params(axis="both", colors=COLORS["black"], labelsize=16)
        for spine in ax_hist1.spines.values():
            spine.set_visible(False)

    # ============================
    # 5. 3-MONTH CHANGE HISTOGRAM - EXACT from Denver
    # ============================
    ax_hist2 = fig.add_subplot(gs[4])
    ax_hist2.set_facecolor(COLORS["background"])

    three_months_ago = latest_date - timedelta(days=90)
    past_data_all = df[
        (df["REGION_TYPE_ID"] == -2)
        & (df["DURATION"] == "4 weeks")
        & (df["PERIOD_END"] <= three_months_ago)
        & (df["PERIOD_END"] >= three_months_ago - timedelta(days=30))
        & (df["REGION_NAME"] != "All Redfin Metros")
    ].copy()
    past_data_all = past_data_all.sort_values(["REGION_NAME", "PERIOD_END"]).groupby("REGION_NAME").last()

    change_df = current_data.merge(
        past_data_all[[column_name]],
        left_on="REGION_NAME",
        right_index=True,
        suffixes=("", "_past"),
        how="inner",
    )

    if normalize_for_histogram:
        change_df["current_value"] = change_df["normalized_value"]
        change_df["past_value"] = normalize_metric_for_histograms(
            change_df, f"{column_name}_past", normalize_for_histogram
        )
    else:
        change_df["current_value"] = change_df[column_name]
        change_df["past_value"] = change_df[f"{column_name}_past"]

    change_df["change_3m"] = change_df["current_value"] - change_df["past_value"]

    target_row = change_df[change_df["REGION_NAME"] == metro_name]
    if len(target_row) > 0:
        target_data_hist = target_row.iloc[0]
        target_change = target_data_hist["change_3m"]

        change_values = change_df["change_3m"].values
        change_values = change_values[~np.isnan(change_values)]

        if len(change_values) > 0 and np.std(change_values) > 0:
            q1_change = np.percentile(change_values, 25)
            q3_change = np.percentile(change_values, 75)
            iqr_change = q3_change - q1_change

            lower_bound_change = q1_change - 2.5 * iqr_change
            upper_bound_change = q3_change + 2.5 * iqr_change

            if target_change < lower_bound_change:
                lower_bound_change = target_change - abs(target_change) * 0.1 - 1
            if target_change > upper_bound_change:
                upper_bound_change = target_change + abs(target_change) * 0.1 + 1

            change_values_filtered = change_values[
                (change_values >= lower_bound_change) & (change_values <= upper_bound_change)
            ]
        else:
            change_values_filtered = change_values

        if len(change_values_filtered) > 0:
            n_bins = 100
            counts, bins, patches = ax_hist2.hist(
                change_values_filtered,
                bins=n_bins,
                color=COLORS["blue"],
                alpha=0.7,
                edgecolor=COLORS["blue"],
                linewidth=0.5,
            )

            for patch in patches:
                patch.set_facecolor(COLORS["blue"])
                patch.set_edgecolor(COLORS["blue"])
                patch.set_alpha(0.3)

            target_change_bin_idx = np.digitize(target_change, bins) - 1
            if 0 <= target_change_bin_idx < len(patches):
                patches[target_change_bin_idx].set_facecolor(COLORS["black"])
                patches[target_change_bin_idx].set_edgecolor(COLORS["black"])
                patches[target_change_bin_idx].set_alpha(1.0)

            percentile_change = (change_values < target_change).sum() / len(change_values) * 100

            median_change = np.median(change_values_filtered)
            ax_hist2.axvline(median_change, color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7)

            target_change_label = format_value(
                target_change,
                normalized_unit_label if normalize_for_histogram else unit_label,
                decimals,
                is_percentage,
            )
            median_change_label = format_value(
                median_change,
                normalized_unit_label if normalize_for_histogram else unit_label,
                decimals,
                is_percentage,
            )

            legend_elements = [
                Rectangle((0, 0), 1, 1, facecolor=COLORS["black"], alpha=1.0, label=f"{metro_display}: {target_change_label}"),
                Rectangle((0, 0), 1, 1, facecolor=COLORS["blue"], alpha=0.3, label="Other metros"),
                Line2D([0], [0], color=COLORS["gray"], linestyle=":", linewidth=1, alpha=0.7, label=f"Median: {median_change_label}"),
            ]
            ax_hist2.legend(
                handles=legend_elements,
                loc="upper right",
                fontsize=14,
                frameon=True,
                fancybox=False,
                shadow=False,
                framealpha=1.0,
                facecolor=COLORS["background"],
                edgecolor="none",
            )

            ax_hist2.set_xlabel(
                f"3-Month Change in {metric_name.title()}",
                fontsize=20,
                fontweight="normal",
                color=COLORS["black"],
                labelpad=10,
            )
            ax_hist2.set_ylabel("Number of Metros", fontsize=20, fontweight="normal", color=COLORS["black"], labelpad=15)
            ax_hist2.text(
                0.0,
                1.15,
                f"3-Month Change ({percentile_change:.0f}th percentile)",
                transform=ax_hist2.transAxes,
                fontsize=22,
                fontweight="bold",
                ha="left",
                va="top",
                color=COLORS["black"],
            )

            ax_hist2.grid(True, alpha=0.3, axis="y")
            ax_hist2.tick_params(axis="both", colors=COLORS["black"], labelsize=16)
            for spine in ax_hist2.spines.values():
                spine.set_visible(False)

    # Save figure
    plt.savefig(
        output_filename,
        dpi=150,
        bbox_inches="tight",
        facecolor=COLORS["background"],
        pad_inches=0.3,
    )
    plt.close()

    return True
