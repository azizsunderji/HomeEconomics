# Visual Chart Designer

A browser-based drag-and-drop chart designer for creating perfect social media charts.

## Quick Start

1. **Run the designer:**
   ```bash
   python3 run_designer.py
   ```
   This will open the designer in your browser at http://localhost:8888/chart_designer.html

2. **Or open directly:**
   Just double-click `chart_designer.html` to open in browser (some features may be limited)

## Features

### 🎨 Visual Design
- **Drag & Drop**: Click and drag any element to reposition
- **Real-time Preview**: See changes instantly on 1200x1200 canvas
- **Property Panel**: Select any element to edit its properties
- **Color Picker**: Choose from your brand colors with one click

### 🧩 Elements
- **Text**: Add titles, labels, descriptions
- **Lines**: Add horizontal dividers
- **Charts**: Placeholder for time series, bar charts, histograms

### 🎯 Key Controls

#### Left Toolbar
- **Add Text**: Creates a new text element
- **Add Line**: Creates a horizontal line
- **Load Title Section**: Pre-built title template

#### Top Bar
- **Save Config**: Download design as JSON
- **Load Config**: Import saved design
- **Export PNG**: Download as image
- **Generate Python**: Get matplotlib code
- **Clear All**: Remove all elements
- **Zoom**: Adjust canvas zoom (25-150%)

#### Properties Panel (Right)
When you select an element:
- **Position**: X/Y coordinates
- **Text**: Edit text content
- **Size**: Font size slider
- **Weight**: Normal or Bold
- **Color**: Click color swatches

### 💡 Tips

1. **Start with template**: Click "Load Title Section" for a pre-built title
2. **Precise positioning**: Use X/Y inputs in properties for exact placement
3. **Align elements**: Drag elements and watch X/Y values to align
4. **Test font sizes**: Use the slider to find perfect size
5. **Save often**: Save your config to reuse layouts

### 🔄 Workflow

1. Design your chart layout visually
2. Save configuration (JSON)
3. Generate Python code
4. Use in production chart generator

### ⌨️ Keyboard Shortcuts
- Coming soon: Delete key, Undo/Redo, Copy/Paste

### 🎨 Your Brand Colors
- Blue: #0BB4FF
- Yellow: #FEC439
- Cream: #F6F7F3 (background)
- Red: #F4743B
- Black: #3D3733
- Gray: #808080
- Green: #67A275

## Example Workflow

1. Run `python3 run_designer.py`
2. Click "Load Title Section" to start with template
3. Drag elements to perfect positions
4. Select text and change to "NEW LISTINGS"
5. Change metro text to your city
6. Adjust font sizes with sliders
7. Save Config when perfect
8. Generate Python to get matplotlib code