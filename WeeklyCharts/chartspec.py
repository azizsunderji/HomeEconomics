#!/usr/bin/env python3
"""
Declarative chart specification system for precise layout control
"""
from dataclasses import dataclass, field
from typing import Literal, Tuple, List, Optional
import json

@dataclass
class LegendSpec:
    loc: Literal["best","upper left","upper right","lower left","lower right","center left","center right","lower center","upper center","center"] = "best"
    ncol: int = 1
    frameon: bool = False
    fontsize: float = 9
    handlelength: float = 1.4
    columnspacing: float = 1.2

@dataclass
class AnnotationRule:
    text: str
    xy: Tuple[float,float]     # data coords
    prefer: Literal["N","S","E","W","NE","NW","SE","SW"] = "NE"
    pad: float = 4.0           # points
    arrow: bool = False

@dataclass
class TitlePositions:
    """Positions for multi-line titles (figure coords)"""
    main_y: float = 0.96      # Main title
    metro_y: float = 0.92      # Metro area name
    subtitle_y: float = 0.88   # Date subtitle

@dataclass
class ChartSpec:
    # Dimensions
    width: float = 12.0     # inches (1200px at 100dpi)
    height: float = 12.0    # inches (1200px at 100dpi)
    dpi: int = 100
    
    # Colors (your brand palette)
    bg: str = "#F6F7F3"    # Cream background
    fg: str = "#3D3733"    # Black text
    brand_blue: str = "#0BB4FF"
    brand_yellow: str = "#FEC439"
    brand_green: str = "#67A275"
    brand_light_green: str = "#C6DCCB"
    brand_red: str = "#F4743B"
    brand_light_red: str = "#FBCAB5"
    
    # Fonts
    font: str = "ABC Oracle"
    base_fontsize: float = 10.0
    title_size: float = 14
    metro_size: float = 12
    subtitle_size: float = 10
    label_size: float = 10
    tick_size: float = 9
    
    # Margins (0-1 scale)
    top: float = 0.85
    right: float = 0.95
    bottom: float = 0.08
    left: float = 0.08
    
    # Grid settings for multi-panel
    grid_rows: int = 3
    grid_cols: int = 2
    height_ratios: List[float] = field(default_factory=lambda: [1.2, 0.9, 0.9])
    width_ratios: List[float] = field(default_factory=lambda: [1, 1])
    hspace: float = 0.50  # Vertical spacing between panels
    wspace: float = 0.35  # Horizontal spacing between panels
    
    # Component settings
    grid: bool = True
    grid_axis: str = "y"
    grid_alpha: float = 0.3
    x_tick_rotation: int = 0
    
    # Title positioning
    titles: TitlePositions = field(default_factory=TitlePositions)
    
    # Legend
    legend: LegendSpec = field(default_factory=LegendSpec)
    
    # Annotations
    annotations: List[AnnotationRule] = field(default_factory=list)
    repel_annotations: bool = True
    
    def to_json(self, filepath: str = None) -> str:
        """Export spec to JSON"""
        def serialize(obj):
            if hasattr(obj, '__dict__'):
                return obj.__dict__
            return obj
        
        json_str = json.dumps(self.__dict__, default=serialize, indent=2)
        if filepath:
            with open(filepath, 'w') as f:
                f.write(json_str)
        return json_str
    
    @classmethod
    def from_json(cls, filepath: str = None, json_str: str = None) -> 'ChartSpec':
        """Load spec from JSON"""
        if filepath:
            with open(filepath, 'r') as f:
                data = json.load(f)
        else:
            data = json.loads(json_str)
        
        # Reconstruct nested dataclasses
        if 'legend' in data and isinstance(data['legend'], dict):
            data['legend'] = LegendSpec(**data['legend'])
        if 'titles' in data and isinstance(data['titles'], dict):
            data['titles'] = TitlePositions(**data['titles'])
        if 'annotations' in data and isinstance(data['annotations'], list):
            data['annotations'] = [AnnotationRule(**a) if isinstance(a, dict) else a 
                                  for a in data['annotations']]
        
        return cls(**data)

# Preset configurations
def get_social_media_spec() -> ChartSpec:
    """Optimized for 1200x1200 social media posts"""
    return ChartSpec(
        width=12.0,
        height=12.0,
        dpi=100,
        title_size=14,
        metro_size=12,
        subtitle_size=10,
        label_size=10,
        tick_size=9,
        top=0.85,
        bottom=0.08,
        left=0.08,
        right=0.95,
        hspace=0.50,
        wspace=0.35,
        titles=TitlePositions(
            main_y=0.96,
            metro_y=0.92,
            subtitle_y=0.88
        )
    )

def get_mobile_spec() -> ChartSpec:
    """Optimized for mobile viewing"""
    return ChartSpec(
        width=8.0,
        height=14.0,
        dpi=100,
        title_size=20,
        metro_size=18,
        subtitle_size=14,
        label_size=12,
        tick_size=11,
        grid_rows=4,
        grid_cols=1,
        height_ratios=[1.5, 1.0, 1.0, 1.0],
        width_ratios=[1],
        hspace=0.35,
        wspace=0
    )