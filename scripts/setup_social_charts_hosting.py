#!/usr/bin/env python3
"""
Set up GitHub Pages hosting for social media charts
Creates an index and pushes charts to a GitHub repository for public access
"""

import os
import json
import shutil
from pathlib import Path
from datetime import datetime
import subprocess

def create_index_html(charts_dir, output_file):
    """Create an index.html file with links to all charts"""
    
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Home Economics - Social Media Charts</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #F6F7F3;
        }
        h1 {
            color: #3D3733;
            border-bottom: 2px solid #0BB4FF;
            padding-bottom: 10px;
        }
        h2 {
            color: #0BB4FF;
            margin-top: 30px;
        }
        .metro-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        .metro-card {
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .metro-name {
            font-weight: bold;
            color: #3D3733;
            margin-bottom: 10px;
        }
        .metric-links {
            font-size: 0.9em;
            line-height: 1.6;
        }
        .metric-links a {
            color: #0BB4FF;
            text-decoration: none;
            display: block;
            padding: 2px 0;
        }
        .metric-links a:hover {
            text-decoration: underline;
        }
        .search {
            padding: 10px;
            width: 100%;
            max-width: 400px;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-bottom: 20px;
        }
        .stats {
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <h1>Home Economics - Social Media Charts</h1>
    
    <div class="stats">
        <strong>Generated:</strong> {date}<br>
        <strong>Total Metros:</strong> <span id="metro-count">0</span><br>
        <strong>Total Charts:</strong> <span id="chart-count">0</span>
    </div>
    
    <input type="text" class="search" id="search" placeholder="Search metros..." onkeyup="filterMetros()">
    
    <div class="metro-grid" id="metro-grid">
        <!-- Metro cards will be inserted here -->
    </div>
    
    <script>
        const metros = {metros_json};
        
        function initializeGrid() {{
            const grid = document.getElementById('metro-grid');
            let metroCount = 0;
            let chartCount = 0;
            
            for (const [metroSlug, metroData] of Object.entries(metros)) {{
                metroCount++;
                chartCount += metroData.charts.length;
                
                const card = document.createElement('div');
                card.className = 'metro-card';
                card.dataset.metroName = metroData.name.toLowerCase();
                
                const nameDiv = document.createElement('div');
                nameDiv.className = 'metro-name';
                nameDiv.textContent = metroData.name;
                card.appendChild(nameDiv);
                
                const linksDiv = document.createElement('div');
                linksDiv.className = 'metric-links';
                
                metroData.charts.forEach(chart => {{
                    const link = document.createElement('a');
                    link.href = chart.url;
                    link.textContent = chart.metric;
                    link.target = '_blank';
                    linksDiv.appendChild(link);
                }});
                
                card.appendChild(linksDiv);
                grid.appendChild(card);
            }}
            
            document.getElementById('metro-count').textContent = metroCount;
            document.getElementById('chart-count').textContent = chartCount;
        }}
        
        function filterMetros() {{
            const searchTerm = document.getElementById('search').value.toLowerCase();
            const cards = document.querySelectorAll('.metro-card');
            
            cards.forEach(card => {{
                const metroName = card.dataset.metroName;
                if (metroName.includes(searchTerm)) {{
                    card.style.display = 'block';
                }} else {{
                    card.style.display = 'none';
                }}
            }});
        }}
        
        // Initialize on load
        initializeGrid();
    </script>
</body>
</html>
"""
    
    # Build metros data structure
    metros = {}
    charts_path = Path(charts_dir)
    
    for metro_dir in sorted(charts_path.iterdir()):
        if metro_dir.is_dir():
            metro_slug = metro_dir.name
            metro_charts = []
            
            for chart_file in sorted(metro_dir.glob("*_social.png")):
                # Extract metric name from filename
                metric_name = chart_file.stem.replace(f"{metro_slug}_", "").replace("_social", "")
                metric_display = metric_name.replace("_", " ").title()
                
                metro_charts.append({
                    "metric": metric_display,
                    "url": f"{metro_slug}/{chart_file.name}"
                })
            
            if metro_charts:
                # Convert slug back to display name
                metro_name = metro_slug.replace("_", " ").title()
                metros[metro_slug] = {
                    "name": metro_name,
                    "charts": metro_charts
                }
    
    # Replace placeholders
    html_content = html_content.replace("{date}", datetime.now().strftime("%B %d, %Y"))
    html_content = html_content.replace("{metros_json}", json.dumps(metros))
    
    # Write index file
    with open(output_file, 'w') as f:
        f.write(html_content)
    
    return len(metros), sum(len(m["charts"]) for m in metros.values())

def create_github_repo_structure():
    """Create structure for GitHub Pages hosting"""
    
    base_dir = Path("/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics")
    social_charts_dir = base_dir / "social_charts" / "2025-08-22"
    
    # Create a new directory for GitHub Pages
    gh_pages_dir = base_dir / "social-charts-gh-pages"
    gh_pages_dir.mkdir(exist_ok=True)
    
    # Copy all charts to gh-pages directory
    dest_charts_dir = gh_pages_dir / "charts"
    if dest_charts_dir.exists():
        shutil.rmtree(dest_charts_dir)
    shutil.copytree(social_charts_dir, dest_charts_dir)
    
    # Create index.html
    metro_count, chart_count = create_index_html(dest_charts_dir, gh_pages_dir / "index.html")
    
    # Create a simple README
    readme_content = f"""# Home Economics Social Media Charts

This repository hosts social media-optimized charts for real estate metrics across US metros.

- **Metros:** {metro_count}
- **Charts:** {chart_count}
- **Format:** 1200x1200px square PNG
- **Updated:** {datetime.now().strftime("%B %d, %Y")}

Visit the [interactive index](https://yourusername.github.io/social-charts/) to browse all charts.

Each chart includes:
- Historical trend with 3-month lookback periods
- Same week year-over-year comparison
- 3-month momentum indicator
- National comparison histogram

Data source: Redfin weekly housing market data
"""
    
    with open(gh_pages_dir / "README.md", 'w') as f:
        f.write(readme_content)
    
    # Create .nojekyll file (tells GitHub not to process with Jekyll)
    (gh_pages_dir / ".nojekyll").touch()
    
    print(f"✅ GitHub Pages structure created at: {gh_pages_dir}")
    print(f"   - {metro_count} metros")
    print(f"   - {chart_count} charts")
    print(f"\nNext steps:")
    print("1. Create a new GitHub repository called 'social-charts'")
    print("2. Initialize git in the directory:")
    print(f"   cd {gh_pages_dir}")
    print("   git init")
    print("   git add .")
    print("   git commit -m 'Initial social media charts'")
    print("3. Add your GitHub remote:")
    print("   git remote add origin https://github.com/yourusername/social-charts.git")
    print("4. Push to GitHub:")
    print("   git push -u origin main")
    print("5. Enable GitHub Pages in repository settings (from main branch)")
    print("\nYour charts will be available at:")
    print("   https://yourusername.github.io/social-charts/")
    
    return gh_pages_dir

def generate_url_list(gh_pages_dir, username="azizsunderji"):
    """Generate a CSV file with all chart URLs"""
    
    csv_file = gh_pages_dir / "chart_urls.csv"
    base_url = f"https://{username}.github.io/social-charts/charts"
    
    with open(csv_file, 'w') as f:
        f.write("metro,metric,url\n")
        
        charts_dir = gh_pages_dir / "charts"
        for metro_dir in sorted(charts_dir.iterdir()):
            if metro_dir.is_dir():
                metro_name = metro_dir.name.replace("_", " ").title()
                
                for chart_file in sorted(metro_dir.glob("*_social.png")):
                    metric_name = chart_file.stem.replace(f"{metro_dir.name}_", "").replace("_social", "")
                    metric_display = metric_name.replace("_", " ").title()
                    
                    url = f"{base_url}/{metro_dir.name}/{chart_file.name}"
                    f.write(f'"{metro_name}","{metric_display}","{url}"\n')
    
    print(f"✅ URL list saved to: {csv_file}")

if __name__ == "__main__":
    gh_pages_dir = create_github_repo_structure()
    generate_url_list(gh_pages_dir)