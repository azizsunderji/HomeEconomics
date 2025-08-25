#!/usr/bin/env python3
"""
Simple server to run the Chart Designer in browser
"""

import http.server
import socketserver
import webbrowser
import os

PORT = 8888
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def run_server():
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"""
╔══════════════════════════════════════════════════════════╗
║         📊 Visual Chart Designer Server 📊               ║
╚══════════════════════════════════════════════════════════╝

✅ Server running at: http://localhost:{PORT}/chart_designer.html

Opening in your default browser...

Press Ctrl+C to stop the server
        """)
        
        # Open browser
        webbrowser.open(f'http://localhost:{PORT}/chart_designer.html')
        
        # Start server
        httpd.serve_forever()

if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped")