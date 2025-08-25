#!/usr/bin/env python3
"""
Open the HTML file directly in the browser with proper file:// URL
"""
import webbrowser
import os

# Get the absolute path
file_path = os.path.abspath('chart_spec_editor.html')
url = f'file://{file_path}'

print(f"Opening: {url}")
webbrowser.open(url)

# Also try the simple test
simple_path = os.path.abspath('super_simple.html')
simple_url = f'file://{simple_path}'
print(f"Also opening: {simple_url}")
webbrowser.open(simple_url)