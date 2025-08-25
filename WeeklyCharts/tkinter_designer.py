#!/usr/bin/env python3
"""
Tkinter-based Chart Designer
A simple GUI for designing chart layouts
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json

class ChartDesigner:
    def __init__(self, root):
        self.root = root
        self.root.title("Chart Designer - 1200x1200px")
        self.root.geometry("1400x800")
        
        # Colors
        self.colors = {
            'blue': '#0BB4FF',
            'yellow': '#FEC439',
            'cream': '#F6F7F3',
            'red': '#F4743B',
            'black': '#3D3733',
            'gray': '#808080',
            'green': '#67A275'
        }
        
        # Data
        self.elements = []
        self.selected_element = None
        self.element_counter = 0
        self.dragging = False
        self.drag_data = {"x": 0, "y": 0}
        
        self.setup_ui()
        
    def setup_ui(self):
        # Main container
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left panel - controls
        left_panel = tk.Frame(main_frame, width=200, bg='lightgray')
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        tk.Label(left_panel, text="Add Elements", font=('Arial', 14, 'bold'), bg='lightgray').pack(pady=10)
        
        tk.Button(left_panel, text="Add Text", command=self.add_text, width=20).pack(pady=5)
        tk.Button(left_panel, text="Add Line", command=self.add_line, width=20).pack(pady=5)
        tk.Button(left_panel, text="Load Title Template", command=self.load_template, width=20).pack(pady=5)
        
        tk.Label(left_panel, text="Actions", font=('Arial', 14, 'bold'), bg='lightgray').pack(pady=(20, 10))
        tk.Button(left_panel, text="Clear All", command=self.clear_all, width=20).pack(pady=5)
        tk.Button(left_panel, text="Save Config", command=self.save_config, width=20).pack(pady=5)
        tk.Button(left_panel, text="Load Config", command=self.load_config, width=20).pack(pady=5)
        
        # Center - Canvas
        canvas_frame = tk.Frame(main_frame)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        tk.Label(canvas_frame, text="Canvas (600x600 preview, represents 1200x1200)", font=('Arial', 10)).pack()
        
        self.canvas = tk.Canvas(canvas_frame, width=600, height=600, bg=self.colors['cream'], highlightthickness=2)
        self.canvas.pack(pady=10)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(canvas_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)
        
        # Right panel - properties
        right_panel = tk.Frame(main_frame, width=250, bg='lightgray')
        right_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        
        tk.Label(right_panel, text="Properties", font=('Arial', 14, 'bold'), bg='lightgray').pack(pady=10)
        
        self.props_frame = tk.Frame(right_panel, bg='lightgray')
        self.props_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        
        self.no_selection_label = tk.Label(self.props_frame, text="Select an element", bg='lightgray')
        self.no_selection_label.pack(pady=20)
        
    def add_text(self):
        element = {
            'id': self.element_counter,
            'type': 'text',
            'text': 'New Text',
            'x': 300,
            'y': 100 + len(self.elements) * 30,
            'size': 16,
            'color': 'black'
        }
        self.element_counter += 1
        self.elements.append(element)
        self.render_canvas()
        self.select_element(element)
        self.status_var.set(f"Added text element")
        
    def add_line(self):
        element = {
            'id': self.element_counter,
            'type': 'line',
            'x': 100,
            'y': 100 + len(self.elements) * 30,
            'width': 400,
            'color': 'black'
        }
        self.element_counter += 1
        self.elements.append(element)
        self.render_canvas()
        self.select_element(element)
        self.status_var.set(f"Added line element")
        
    def load_template(self):
        self.elements = []
        self.element_counter = 0
        
        # Title
        self.elements.append({
            'id': self.element_counter,
            'type': 'text',
            'text': 'NEW LISTINGS',
            'x': 300,
            'y': 50,
            'size': 24,
            'color': 'black'
        })
        self.element_counter += 1
        
        # Line 1
        self.elements.append({
            'id': self.element_counter,
            'type': 'line',
            'x': 100,
            'y': 75,
            'width': 400,
            'color': 'black'
        })
        self.element_counter += 1
        
        # Metro name
        self.elements.append({
            'id': self.element_counter,
            'type': 'text',
            'text': 'DENVER METRO, CO',
            'x': 300,
            'y': 95,
            'size': 18,
            'color': 'blue'
        })
        self.element_counter += 1
        
        # Line 2
        self.elements.append({
            'id': self.element_counter,
            'type': 'line',
            'x': 100,
            'y': 115,
            'width': 400,
            'color': 'black'
        })
        self.element_counter += 1
        
        # Subtitle
        self.elements.append({
            'id': self.element_counter,
            'type': 'text',
            'text': 'Data based on 4 week window',
            'x': 300,
            'y': 140,
            'size': 12,
            'color': 'gray'
        })
        self.element_counter += 1
        
        self.render_canvas()
        self.status_var.set("Loaded title template")
        
    def render_canvas(self):
        # Clear canvas
        self.canvas.delete("all")
        
        # Draw each element
        for element in self.elements:
            color = self.colors.get(element['color'], element['color'])
            
            if element['type'] == 'text':
                item = self.canvas.create_text(
                    element['x'], element['y'],
                    text=element['text'],
                    font=('Arial', element['size']),
                    fill=color,
                    tags=f"element_{element['id']}"
                )
            elif element['type'] == 'line':
                item = self.canvas.create_line(
                    element['x'], element['y'],
                    element['x'] + element['width'], element['y'],
                    fill=color,
                    width=1,
                    tags=f"element_{element['id']}"
                )
            
            # Bind events
            self.canvas.tag_bind(f"element_{element['id']}", "<Button-1>", lambda e, el=element: self.on_element_click(e, el))
            self.canvas.tag_bind(f"element_{element['id']}", "<B1-Motion>", self.on_element_drag)
            self.canvas.tag_bind(f"element_{element['id']}", "<ButtonRelease-1>", self.on_element_release)
        
        # Highlight selected
        if self.selected_element:
            self.highlight_element(self.selected_element)
    
    def highlight_element(self, element):
        if element['type'] == 'text':
            bbox = self.canvas.bbox(f"element_{element['id']}")
            if bbox:
                self.canvas.create_rectangle(
                    bbox[0]-5, bbox[1]-5, bbox[2]+5, bbox[3]+5,
                    outline='blue', dash=(5, 5), width=2, tags="highlight"
                )
        elif element['type'] == 'line':
            self.canvas.create_rectangle(
                element['x']-5, element['y']-5,
                element['x'] + element['width']+5, element['y']+5,
                outline='blue', dash=(5, 5), width=2, tags="highlight"
            )
    
    def on_element_click(self, event, element):
        self.select_element(element)
        self.dragging = True
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        
    def on_element_drag(self, event):
        if not self.dragging or not self.selected_element:
            return
        
        dx = event.x - self.drag_data["x"]
        dy = event.y - self.drag_data["y"]
        
        self.selected_element['x'] += dx
        self.selected_element['y'] += dy
        
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        
        self.render_canvas()
        self.update_properties()
        
    def on_element_release(self, event):
        self.dragging = False
        self.status_var.set(f"Moved {self.selected_element['type']} to ({self.selected_element['x']}, {self.selected_element['y']})")
        
    def select_element(self, element):
        self.selected_element = element
        self.render_canvas()
        self.show_properties()
        
    def show_properties(self):
        # Clear properties frame
        for widget in self.props_frame.winfo_children():
            widget.destroy()
        
        if not self.selected_element:
            tk.Label(self.props_frame, text="Select an element", bg='lightgray').pack(pady=20)
            return
        
        # Type
        tk.Label(self.props_frame, text=f"Type: {self.selected_element['type']}", bg='lightgray').pack(pady=5)
        
        # X position
        x_frame = tk.Frame(self.props_frame, bg='lightgray')
        x_frame.pack(pady=5)
        tk.Label(x_frame, text="X:", bg='lightgray', width=10).pack(side=tk.LEFT)
        x_var = tk.IntVar(value=self.selected_element['x'])
        tk.Spinbox(x_frame, from_=0, to=600, textvariable=x_var, width=10,
                   command=lambda: self.update_prop('x', x_var.get())).pack(side=tk.LEFT)
        
        # Y position
        y_frame = tk.Frame(self.props_frame, bg='lightgray')
        y_frame.pack(pady=5)
        tk.Label(y_frame, text="Y:", bg='lightgray', width=10).pack(side=tk.LEFT)
        y_var = tk.IntVar(value=self.selected_element['y'])
        tk.Spinbox(y_frame, from_=0, to=600, textvariable=y_var, width=10,
                   command=lambda: self.update_prop('y', y_var.get())).pack(side=tk.LEFT)
        
        # Type-specific properties
        if self.selected_element['type'] == 'text':
            # Text
            text_frame = tk.Frame(self.props_frame, bg='lightgray')
            text_frame.pack(pady=5)
            tk.Label(text_frame, text="Text:", bg='lightgray', width=10).pack(side=tk.LEFT)
            text_var = tk.StringVar(value=self.selected_element['text'])
            tk.Entry(text_frame, textvariable=text_var, width=15).pack(side=tk.LEFT)
            tk.Button(text_frame, text="Set", 
                     command=lambda: self.update_prop('text', text_var.get())).pack(side=tk.LEFT)
            
            # Size
            size_frame = tk.Frame(self.props_frame, bg='lightgray')
            size_frame.pack(pady=5)
            tk.Label(size_frame, text="Size:", bg='lightgray', width=10).pack(side=tk.LEFT)
            size_var = tk.IntVar(value=self.selected_element['size'])
            tk.Spinbox(size_frame, from_=8, to=48, textvariable=size_var, width=10,
                      command=lambda: self.update_prop('size', size_var.get())).pack(side=tk.LEFT)
            
        elif self.selected_element['type'] == 'line':
            # Width
            width_frame = tk.Frame(self.props_frame, bg='lightgray')
            width_frame.pack(pady=5)
            tk.Label(width_frame, text="Width:", bg='lightgray', width=10).pack(side=tk.LEFT)
            width_var = tk.IntVar(value=self.selected_element['width'])
            tk.Spinbox(width_frame, from_=50, to=500, textvariable=width_var, width=10,
                      command=lambda: self.update_prop('width', width_var.get())).pack(side=tk.LEFT)
        
        # Color
        color_frame = tk.Frame(self.props_frame, bg='lightgray')
        color_frame.pack(pady=10)
        tk.Label(color_frame, text="Color:", bg='lightgray').pack()
        colors_grid = tk.Frame(color_frame, bg='lightgray')
        colors_grid.pack()
        
        for i, (name, color) in enumerate(self.colors.items()):
            btn = tk.Button(colors_grid, bg=color, width=4, height=2,
                          command=lambda c=name: self.update_prop('color', c))
            btn.grid(row=i//3, column=i%3, padx=2, pady=2)
        
        # Delete button
        tk.Button(self.props_frame, text="Delete Element", bg='red', fg='white',
                 command=self.delete_element).pack(pady=20)
    
    def update_properties(self):
        """Update properties display without recreating widgets"""
        self.show_properties()
    
    def update_prop(self, prop, value):
        if not self.selected_element:
            return
        self.selected_element[prop] = value
        self.render_canvas()
        self.status_var.set(f"Updated {prop} to {value}")
    
    def delete_element(self):
        if not self.selected_element:
            return
        self.elements = [e for e in self.elements if e['id'] != self.selected_element['id']]
        self.selected_element = None
        self.render_canvas()
        self.show_properties()
        self.status_var.set("Deleted element")
    
    def clear_all(self):
        if messagebox.askyesno("Clear All", "Remove all elements?"):
            self.elements = []
            self.selected_element = None
            self.render_canvas()
            self.show_properties()
            self.status_var.set("Cleared all elements")
    
    def save_config(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            # Scale coordinates to 1200x1200
            scaled_elements = []
            for el in self.elements:
                scaled = el.copy()
                scaled['x'] *= 2  # 600 -> 1200
                scaled['y'] *= 2
                if 'width' in scaled:
                    scaled['width'] *= 2
                if 'size' in scaled:
                    scaled['size'] *= 2
                scaled_elements.append(scaled)
            
            with open(filename, 'w') as f:
                json.dump(scaled_elements, f, indent=2)
            self.status_var.set(f"Saved to {filename}")
    
    def load_config(self):
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            with open(filename, 'r') as f:
                scaled_elements = json.load(f)
            
            # Scale coordinates from 1200x1200 to 600x600
            self.elements = []
            for el in scaled_elements:
                scaled = el.copy()
                scaled['x'] //= 2  # 1200 -> 600
                scaled['y'] //= 2
                if 'width' in scaled:
                    scaled['width'] //= 2
                if 'size' in scaled:
                    scaled['size'] //= 2
                self.elements.append(scaled)
            
            self.render_canvas()
            self.status_var.set(f"Loaded from {filename}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ChartDesigner(root)
    root.mainloop()