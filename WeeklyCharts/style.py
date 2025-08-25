#!/usr/bin/env python3
"""
Style application functions for chart specifications
"""
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib import rcParams
import numpy as np
from typing import Optional, List, Tuple
from chartspec import ChartSpec, AnnotationRule

def apply_style(spec: ChartSpec, ax=None):
    """Apply ChartSpec styling to create a figure"""
    # Set font defaults
    plt.rcParams['svg.fonttype'] = 'none'
    plt.rcParams['font.sans-serif'] = [spec.font, 'Arial', 'DejaVu Sans']
    plt.rcParams['font.size'] = spec.base_fontsize
    
    # Create figure with precise dimensions
    fig = plt.figure(figsize=(spec.width, spec.height), dpi=spec.dpi, facecolor=spec.bg)
    
    if ax is None:
        # Calculate exact axes position
        ax = fig.add_axes([spec.left, spec.bottom, spec.right-spec.left, spec.top-spec.bottom])
    
    # Apply axes styling
    ax.set_facecolor(spec.bg)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='both', labelsize=spec.tick_size, colors=spec.fg)
    ax.grid(spec.grid, axis=spec.grid_axis, linewidth=0.4, alpha=spec.grid_alpha)
    
    # Store specs for later use
    ax._legend_params = spec.legend
    ax._annotation_specs = spec.annotations
    ax._repel = spec.repel_annotations
    
    return fig, ax

def apply_multi_panel_style(spec: ChartSpec):
    """Create a multi-panel figure with GridSpec"""
    # Create figure
    fig = plt.figure(figsize=(spec.width, spec.height), dpi=spec.dpi, facecolor=spec.bg)
    
    # Set font defaults
    plt.rcParams['font.sans-serif'] = [spec.font, 'Arial', 'DejaVu Sans']
    plt.rcParams['font.size'] = spec.base_fontsize
    
    # Create GridSpec with precise spacing
    gs = gridspec.GridSpec(
        spec.grid_rows, spec.grid_cols,
        height_ratios=spec.height_ratios,
        width_ratios=spec.width_ratios,
        top=spec.top,
        bottom=spec.bottom,
        left=spec.left,
        right=spec.right,
        hspace=spec.hspace,
        wspace=spec.wspace
    )
    
    return fig, gs

def style_axis(ax, spec: ChartSpec):
    """Apply styling to a single axis"""
    ax.set_facecolor(spec.bg)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='both', labelsize=spec.tick_size, colors=spec.fg)
    ax.grid(spec.grid, axis=spec.grid_axis, linewidth=0.4, alpha=spec.grid_alpha)
    ax.xaxis.label.set_fontsize(spec.label_size)
    ax.yaxis.label.set_fontsize(spec.label_size)

def add_titles(fig, spec: ChartSpec, main_title: str, metro: str = None, subtitle: str = None):
    """Add multi-line titles with precise positioning"""
    # Main title
    fig.text(0.5, spec.titles.main_y, main_title, 
             fontsize=spec.title_size, weight='bold', 
             ha='center', va='top', color=spec.fg)
    
    # Metro area name
    if metro:
        fig.text(0.5, spec.titles.metro_y, metro, 
                fontsize=spec.metro_size, weight='normal',
                ha='center', va='top', color=spec.brand_blue)
    
    # Date subtitle
    if subtitle:
        fig.text(0.5, spec.titles.subtitle_y, subtitle,
                fontsize=spec.subtitle_size, weight='normal',
                ha='center', va='top', color=spec.fg, alpha=0.7)

def finalize(ax, spec: ChartSpec = None):
    """Finalize axis with legend and annotations"""
    # Apply legend settings if legend exists
    leg = ax.get_legend()
    if leg and hasattr(ax, '_legend_params'):
        p = ax._legend_params
        leg.set_frame_on(p.frameon)
        for t in leg.get_texts():
            t.set_fontsize(p.fontsize)
        # Note: Some legend properties need to be set during creation
    
    # Place annotations
    texts = []
    for a in getattr(ax, "_annotation_specs", []):
        txt = ax.annotate(
            a.text, xy=a.xy, xycoords='data',
            xytext=_offset_for(a.prefer, a.pad), textcoords='offset points',
            arrowprops=dict(arrowstyle='->', lw=0.6) if a.arrow else None,
            fontsize=spec.base_fontsize if spec else 9
        )
        texts.append(txt)
    
    # Apply text repelling if available
    if getattr(ax, "_repel", False) and texts:
        try:
            from adjustText import adjust_text
            adjust_text(texts, ax=ax, only_move={'points':'y','texts':'xy'}, 
                       autoalign='y', force_points=0.5, force_text=0.8)
        except ImportError:
            pass  # adjustText not installed

def _offset_for(direction: str, pad: float) -> Tuple[float, float]:
    """Calculate offset for annotation placement"""
    offsets = {
        "N": (0, pad), "S": (0, -pad), "E": (pad, 0), "W": (-pad, 0),
        "NE": (pad, pad), "NW": (-pad, pad), "SE": (pad, -pad), "SW": (-pad, -pad)
    }
    return offsets.get(direction, (pad, pad))

def export_with_svg(fig, base_filename: str, spec: ChartSpec = None):
    """Export both PNG and SVG versions"""
    # PNG export
    fig.savefig(f"{base_filename}.png", 
                dpi=spec.dpi if spec else 100,
                facecolor=spec.bg if spec else 'white',
                bbox_inches='tight', 
                pad_inches=0.05)
    
    # SVG export for inspection
    fig.savefig(f"{base_filename}.svg",
                format='svg',
                facecolor=spec.bg if spec else 'white',
                bbox_inches='tight',
                pad_inches=0.05)
    
    return f"{base_filename}.png", f"{base_filename}.svg"