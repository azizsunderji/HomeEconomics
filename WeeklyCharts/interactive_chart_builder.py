#!/usr/bin/env python3
"""
Interactive Chart Builder for Social Media Charts
Allows real-time adjustment of chart elements via terminal commands
"""

import os
import sys
import json
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import subprocess

# Color palette
COLORS = {
    "blue": "#0BB4FF",
    "yellow": "#FEC439",
    "background": "#F6F7F3",
    "cream": "#F6F7F3",
    "red": "#F4743B",
    "light_red": "#FBCAB5",
    "black": "#3D3733",
    "gray": "#808080",
    "green": "#67A275",
    "light_green": "#C6DCCB",
}

class ChartElement:
    """Represents a chart element with position and properties"""
    def __init__(self, name, elem_type, **kwargs):
        self.name = name
        self.type = elem_type
        self.visible = True
        self.props = kwargs
        
    def __repr__(self):
        return f"{self.name}: {self.type} at y={self.props.get('y', 'N/A')}, size={self.props.get('size', 'N/A')}"

class InteractiveChartBuilder:
    def __init__(self):
        self.elements = {}
        self.fig = None
        self.ax = None
        self.data = None
        self.metro_name = "Denver, CO metro area"
        self.metric_name = "NEW LISTINGS"
        self.output_file = "interactive_chart.png"
        self.current_font = 'default'
        self.setup_fonts()
        self.initialize_default_elements()
        
    def setup_fonts(self):
        """Set up font configuration"""
        plt.rcParams["font.size"] = 10
        
        # Try to load Oracle fonts
        oracle_font_path = "/Users/azizsunderji/Dropbox/Home Economics/Brand Assets/OracleFont/Oracle Aziz Sunderji/Desktop/"
        self.oracle_available = False
        
        if os.path.exists(oracle_font_path):
            from matplotlib import font_manager
            
            # List all font files in directory
            font_files = [f for f in os.listdir(oracle_font_path) if f.endswith(('.otf', '.ttf'))]
            fonts_loaded = []
            
            for font_file in font_files:
                font_path = os.path.join(oracle_font_path, font_file)
                try:
                    font_manager.fontManager.addfont(font_path)
                    fonts_loaded.append(font_file)
                except Exception as e:
                    pass
            
            if fonts_loaded:
                self.oracle_available = True
                # Set font - use the actual name from the font file
                plt.rcParams['font.family'] = 'sans-serif'
                plt.rcParams['font.sans-serif'] = ['ABC Oracle', 'ABCOracle', 'Oracle'] + plt.rcParams['font.sans-serif']
                self.current_font = 'oracle'
                print(f"✓ Loaded {len(fonts_loaded)} Oracle font files")
                print(f"✓ Current font: ABC Oracle")
        else:
            print(f"⚠ Oracle fonts not found at {oracle_font_path}")
            print(f"✓ Current font: Default (DejaVu Sans)")
    
    def switch_font(self, font_name):
        """Switch between Oracle and default font"""
        from matplotlib import font_manager
        
        if font_name.lower() == 'oracle' and self.oracle_available:
            plt.rcParams['font.family'] = 'sans-serif'
            plt.rcParams['font.sans-serif'] = ['ABC Oracle', 'ABCOracle', 'Oracle'] + ['DejaVu Sans']
            self.current_font = 'oracle'
            print(f"✓ Switched to Oracle font")
        else:
            plt.rcParams['font.family'] = 'sans-serif'
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial']
            self.current_font = 'default'
            print(f"✓ Switched to default font")
        
        # Regenerate chart with new font
        self.generate_chart()
    
    def initialize_default_elements(self):
        """Initialize with default title elements"""
        self.elements['title'] = ChartElement(
            'title', 'text',
            text=self.metric_name,
            x=0.5, y=0.94, size=24,
            weight='bold', ha='center', va='top',
            color=COLORS['black']
        )
        
        self.elements['line1'] = ChartElement(
            'line1', 'line',
            x1=0.2, x2=0.8, y=0.91,
            color=COLORS['black'], width=0.5
        )
        
        self.elements['metro'] = ChartElement(
            'metro', 'text',
            text=f"{self.metro_name.split(',')[0].upper()} METRO, {self.metro_name.split(',')[1].strip().split()[0].upper()}",
            x=0.5, y=0.895, size=18,
            weight='bold', ha='center', va='center',
            color=COLORS['blue']
        )
        
        self.elements['line2'] = ChartElement(
            'line2', 'line',
            x1=0.2, x2=0.8, y=0.88,
            color=COLORS['black'], width=0.5
        )
        
        self.elements['subtitle'] = ChartElement(
            'subtitle', 'text',
            text=f"Data based on 4 week window captured {datetime.now().strftime('%B %d, %Y')}",
            x=0.5, y=0.86, size=11,
            weight='normal', ha='center', va='top',
            color=COLORS['gray']
        )
    
    def generate_chart(self):
        """Generate the chart with current elements"""
        # Close any existing figure
        if self.fig:
            plt.close(self.fig)
        
        # Create new figure with cream background
        self.fig = plt.figure(figsize=(12, 12), facecolor=COLORS['background'], dpi=100)
        
        # Create axes covering full figure
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.ax.axis('off')
        self.ax.set_facecolor(COLORS['background'])
        
        # Draw all visible elements
        for name, elem in self.elements.items():
            if not elem.visible:
                continue
                
            if elem.type == 'text':
                self.ax.text(
                    elem.props['x'], elem.props['y'],
                    elem.props['text'],
                    fontsize=elem.props.get('size', 12),
                    fontweight=elem.props.get('weight', 'normal'),
                    ha=elem.props.get('ha', 'center'),
                    va=elem.props.get('va', 'center'),
                    color=elem.props.get('color', COLORS['black']),
                    transform=self.ax.transAxes
                )
            
            elif elem.type == 'line':
                self.ax.plot(
                    [elem.props['x1'], elem.props['x2']], 
                    [elem.props['y'], elem.props['y']],
                    color=elem.props.get('color', COLORS['black']),
                    linewidth=elem.props.get('width', 0.5),
                    transform=self.ax.transAxes
                )
            
            elif elem.type == 'timeseries':
                # Add time series plot (placeholder for now)
                self.add_timeseries_plot(elem)
        
        # Save the figure
        self.fig.savefig(
            self.output_file,
            dpi=100,
            bbox_inches=None,
            facecolor=COLORS['background'],
            pad_inches=0
        )
        print(f"✓ Chart saved to {self.output_file}")
        
        # Try to open the image for viewing
        try:
            subprocess.run(['open', self.output_file], check=False)
        except:
            pass
    
    def add_timeseries_plot(self, elem):
        """Add a time series plot to the chart"""
        # This is a placeholder - would add actual time series here
        pass
    
    def move_element(self, name, direction, amount):
        """Move an element up/down/left/right"""
        if name not in self.elements:
            print(f"❌ Element '{name}' not found")
            return
        
        elem = self.elements[name]
        amount = float(amount)
        
        if direction in ['up', 'down']:
            if 'y' in elem.props:
                elem.props['y'] += amount if direction == 'up' else -amount
                print(f"✓ Moved {name} {direction} by {amount} to y={elem.props['y']:.3f}")
        elif direction in ['left', 'right']:
            if 'x' in elem.props:
                elem.props['x'] += amount if direction == 'right' else -amount
                print(f"✓ Moved {name} {direction} by {amount} to x={elem.props['x']:.3f}")
    
    def resize_element(self, name, new_size):
        """Change the size of an element"""
        if name not in self.elements:
            print(f"❌ Element '{name}' not found")
            return
        
        elem = self.elements[name]
        if 'size' in elem.props:
            elem.props['size'] = float(new_size)
            print(f"✓ Resized {name} to {new_size}")
        else:
            print(f"❌ Element '{name}' doesn't have a size property")
    
    def change_color(self, name, color):
        """Change the color of an element"""
        if name not in self.elements:
            print(f"❌ Element '{name}' not found")
            return
        
        # Allow color names or hex codes
        if color in COLORS:
            color = COLORS[color]
        
        elem = self.elements[name]
        elem.props['color'] = color
        print(f"✓ Changed {name} color to {color}")
    
    def list_elements(self):
        """List all elements and their properties"""
        print("\n📊 Current Elements:")
        print("-" * 60)
        for i, (name, elem) in enumerate(self.elements.items(), 1):
            if elem.type == 'text':
                print(f"{i}. {name}: '{elem.props.get('text', '')[:30]}...'")
                print(f"   Position: x={elem.props.get('x', 0):.3f}, y={elem.props.get('y', 0):.3f}")
                print(f"   Size: {elem.props.get('size', 'N/A')}, Color: {elem.props.get('color', 'N/A')}")
            elif elem.type == 'line':
                print(f"{i}. {name}: Line from ({elem.props.get('x1', 0):.2f}, {elem.props.get('y', 0):.3f}) to ({elem.props.get('x2', 0):.2f}, {elem.props.get('y', 0):.3f})")
            print()
    
    def save_config(self, filename):
        """Save current configuration to JSON"""
        config = {}
        for name, elem in self.elements.items():
            config[name] = {
                'type': elem.type,
                'visible': elem.visible,
                'props': elem.props
            }
        
        with open(filename, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"✓ Configuration saved to {filename}")
    
    def load_config(self, filename):
        """Load configuration from JSON"""
        if not os.path.exists(filename):
            print(f"❌ File {filename} not found")
            return
        
        with open(filename, 'r') as f:
            config = json.load(f)
        
        self.elements = {}
        for name, data in config.items():
            elem = ChartElement(name, data['type'], **data['props'])
            elem.visible = data.get('visible', True)
            self.elements[name] = elem
        
        print(f"✓ Configuration loaded from {filename}")
    
    def process_command(self, command):
        """Process a user command"""
        parts = command.strip().split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        
        # Movement commands - check if second word is a direction
        if len(parts) >= 3 and parts[1] in ['up', 'down', 'left', 'right']:
            self.move_element(parts[0], parts[1], parts[2])
            self.generate_chart()
        
        # Size command
        elif cmd == 'size' and len(parts) >= 3:
            self.resize_element(parts[1], parts[2])
            self.generate_chart()
        
        # Color command
        elif cmd == 'color' and len(parts) >= 3:
            self.change_color(parts[1], parts[2])
            self.generate_chart()
        
        # Font command
        elif cmd == 'font' and len(parts) >= 2:
            self.switch_font(parts[1])
        
        # Show current font
        elif cmd == 'font' and len(parts) == 1:
            print(f"Current font: {self.current_font}")
            print(f"Oracle available: {self.oracle_available}")
        
        # List elements
        elif cmd == 'list':
            self.list_elements()
        
        # Show chart
        elif cmd in ['show', 'refresh']:
            self.generate_chart()
        
        # Save configuration
        elif cmd == 'save' and len(parts) >= 2:
            self.save_config(parts[1])
        
        # Load configuration
        elif cmd == 'load' and len(parts) >= 2:
            self.load_config(parts[1])
            self.generate_chart()
        
        # Add timeseries
        elif cmd == 'add' and len(parts) >= 2 and parts[1] == 'timeseries':
            print("⚠ Time series component not yet implemented")
        
        # Help
        elif cmd == 'help':
            self.show_help()
        
        # Quit
        elif cmd in ['quit', 'exit', 'q']:
            return False
        
        else:
            print(f"❌ Unknown command: {command}")
            print("Type 'help' for available commands")
        
        return True
    
    def show_help(self):
        """Show help information"""
        print("""
📊 Interactive Chart Builder Commands:
=====================================

Movement:
  <element> up <amount>     - Move element up
  <element> down <amount>   - Move element down
  <element> left <amount>   - Move element left
  <element> right <amount>  - Move element right
  
  Example: metro up 0.01

Properties:
  size <element> <size>     - Change font size
  color <element> <color>   - Change color
  font <name>               - Switch font (oracle/default)
  font                      - Show current font
  
  Example: size title 20
  Example: color metro blue
  Example: font oracle

Display:
  list                      - List all elements
  show / refresh            - Regenerate and display chart

Save/Load:
  save <filename>           - Save current configuration
  load <filename>           - Load saved configuration
  
  Example: save perfect_title.json

Other:
  help                      - Show this help
  quit / exit / q           - Exit the program

Available elements: title, metro, subtitle, line1, line2
Available colors: blue, yellow, red, green, black, gray
        """)
    
    def run(self):
        """Run the interactive command loop"""
        print("""
╔══════════════════════════════════════════════════════════╗
║     📊 Interactive Social Media Chart Builder 📊         ║
╚══════════════════════════════════════════════════════════╝

Type 'help' for commands, 'quit' to exit
        """)
        
        # Generate initial chart
        self.generate_chart()
        self.list_elements()
        
        # Command loop
        while True:
            try:
                command = input("\n> ").strip()
                if command:
                    if not self.process_command(command):
                        break
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

if __name__ == "__main__":
    builder = InteractiveChartBuilder()
    builder.run()