#!/usr/bin/env python3
"""
Flask server for live chart editing
Loads chart Python files and applies ChartSpec in real-time
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import base64
import io
import sys
import os
import importlib.util
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from chartspec import ChartSpec

app = Flask(__name__)
CORS(app)  # Allow browser to connect

# Store current chart module
current_chart_module = None
current_chart_file = None

@app.route('/')
def index():
    """Serve the HTML editor"""
    return send_from_directory('.', 'chart_spec_editor_live.html')

@app.route('/load_chart', methods=['POST'])
def load_chart():
    """Load a Python chart file"""
    global current_chart_module, current_chart_file
    
    data = request.json
    chart_file = data.get('file_path')
    
    # Handle both absolute and relative paths
    if not chart_file:
        return jsonify({'error': 'No file path provided'}), 400
    
    # If it's a relative path, make it absolute based on current directory
    if not os.path.isabs(chart_file):
        chart_file = os.path.abspath(os.path.join(os.getcwd(), chart_file))
    
    # Check if file exists
    if not os.path.exists(chart_file):
        # Try in the current directory as fallback
        local_file = os.path.join(os.getcwd(), os.path.basename(chart_file))
        if os.path.exists(local_file):
            chart_file = local_file
        else:
            return jsonify({'error': f'File not found: {chart_file}'}), 404
    
    try:
        # Add the file's directory to sys.path so imports work
        file_dir = os.path.dirname(chart_file)
        if file_dir not in sys.path:
            sys.path.insert(0, file_dir)
        
        # Load the Python module dynamically
        spec = importlib.util.spec_from_file_location("chart_module", chart_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules["chart_module"] = module
        spec.loader.exec_module(module)
        
        current_chart_module = module
        current_chart_file = chart_file
        
        # Check what functions are available
        available_functions = [f for f in dir(module) if callable(getattr(module, f)) and not f.startswith('_')]
        
        return jsonify({
            'success': True,
            'file': chart_file,
            'functions': available_functions
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/generate_chart', methods=['POST'])
def generate_chart():
    """Generate chart with current spec"""
    global current_chart_module
    
    if not current_chart_module:
        return jsonify({'error': 'No chart loaded'}), 400
    
    data = request.json
    spec_data = data.get('spec', {})
    
    try:
        # Convert dict to ChartSpec
        spec = ChartSpec(
            width=spec_data.get('width', 12),
            height=spec_data.get('height', 12),
            dpi=spec_data.get('dpi', 100),
            top=spec_data.get('top', 0.85),
            bottom=spec_data.get('bottom', 0.08),
            left=spec_data.get('left', 0.08),
            right=spec_data.get('right', 0.95),
            hspace=spec_data.get('hspace', 0.50),
            wspace=spec_data.get('wspace', 0.35),
            font=spec_data.get('font', 'ABC Oracle'),
            title_size=spec_data.get('title_size', 14),
            metro_size=spec_data.get('metro_size', 12),
            subtitle_size=spec_data.get('subtitle_size', 10),
            label_size=spec_data.get('label_size', 10),
            tick_size=spec_data.get('tick_size', 9),
            bg=spec_data.get('bg', '#F6F7F3'),
            fg=spec_data.get('fg', '#3D3733'),
            brand_blue=spec_data.get('brand_blue', '#0BB4FF')
        )
        
        # Update title positions if provided
        if 'titles' in spec_data:
            from chartspec import TitlePositions
            spec.titles = TitlePositions(
                main_y=spec_data['titles'].get('main_y', 0.96),
                metro_y=spec_data['titles'].get('metro_y', 0.92),
                subtitle_y=spec_data['titles'].get('subtitle_y', 0.88)
            )
        
        # Clear any existing plots
        plt.close('all')
        
        # Try different function names that might exist in the chart module
        if hasattr(current_chart_module, 'generate_chart_with_spec'):
            # New charts that accept spec
            fig = current_chart_module.generate_chart_with_spec(spec)
        elif hasattr(current_chart_module, 'generate_chart'):
            # Try to pass spec if function accepts it
            import inspect
            sig = inspect.signature(current_chart_module.generate_chart)
            if 'spec' in sig.parameters:
                fig = current_chart_module.generate_chart(spec=spec)
            else:
                # Old style - just generate without spec
                fig = current_chart_module.generate_chart()
        elif hasattr(current_chart_module, 'create_chart'):
            fig = current_chart_module.create_chart()
        elif hasattr(current_chart_module, 'main'):
            # Try to capture the figure from main()
            current_chart_module.main()
            fig = plt.gcf()
        else:
            # Try to find any function that creates a chart
            for func_name in ['test_with_spec', 'create_social_chart_with_spec', 'create_exact_metro_chart']:
                if hasattr(current_chart_module, func_name):
                    # For now, just call it with minimal args
                    # This would need to be smarter for real usage
                    fig = plt.gcf()
                    break
            else:
                return jsonify({'error': 'No suitable chart generation function found'}), 400
        
        # Convert figure to base64 image
        buf = io.BytesIO()
        
        # If fig is not a matplotlib figure, try to get current figure
        if not hasattr(fig, 'savefig'):
            fig = plt.gcf()
        
        fig.savefig(buf, format='png', dpi=spec.dpi, facecolor=spec.bg, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        
        return jsonify({
            'success': True,
            'image': f'data:image/png;base64,{img_base64}'
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/list_charts', methods=['GET'])
def list_charts():
    """List available chart files in the directory"""
    chart_files = []
    
    # Look for Python files that might be charts
    for file in os.listdir('.'):
        if file.endswith('.py') and 'chart' in file.lower() and file != 'chart_server.py':
            abs_path = os.path.abspath(file)
            chart_files.append({
                'name': file,
                'path': abs_path
            })
    
    # Special case: Add the new social chart wrapper
    social_wrapper = os.path.abspath('social_chart_editable.py')
    if os.path.exists(social_wrapper) and social_wrapper not in [c['path'] for c in chart_files]:
        chart_files.insert(0, {
            'name': '📊 Social Charts (Editable)',
            'path': social_wrapper
        })
    
    # Sort with special files first
    chart_files.sort(key=lambda x: (not x['name'].startswith('📊'), x['name']))
    
    return jsonify({'charts': chart_files})

@app.route('/save_spec', methods=['POST'])
def save_spec():
    """Save the current spec to a JSON file"""
    data = request.json
    spec_data = data.get('spec', {})
    filename = data.get('filename', 'chart_spec.json')
    
    try:
        with open(filename, 'w') as f:
            json.dump(spec_data, f, indent=2)
        return jsonify({'success': True, 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print("Starting Chart Editor Server...")
    print(f"Open http://{local_ip}:5555 in your browser")
    print(f"Also available at: http://localhost:5555")
    print(f"Or try: http://127.0.0.1:5555")
    app.run(debug=True, host='0.0.0.0', port=5555)