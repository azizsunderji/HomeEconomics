#!/usr/bin/python3

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Home Economics Charts
# @raycast.mode fullOutput

# Optional parameters:
# @raycast.icon 📊
# @raycast.argument1 { "type": "text", "placeholder": "e.g. dallas median price [mobile]" }
# @raycast.packageName Home Economics
# @raycast.description Look up a Home Economics chart URL by city + metric
# @raycast.author Aziz Sunderji
# @raycast.authorURL https://home-economics.us

import sys
import subprocess
from pathlib import Path
from difflib import get_close_matches

# Chart types configuration
CHART_TYPES = {
    'social': {
        'base_url': 'https://home-economics.us/charts/social',
        'suffix': '_social.png',
        'date_format': '2025-08-22'
    },
    'mobile': {
        'base_url': 'https://home-economics.us/charts/mobile', 
        'suffix': '_mobile.png',
        'date_format': '2025-08-22'
    }
}

# Metric mappings
METRICS = {
    'median_sale_price': ['price', 'sale price', 'median price', 'home price'],
    'weeks_supply': ['supply', 'weeks', 'inventory weeks', 'weeks supply'],
    'new_listings': ['new', 'listings', 'new homes', 'new listings'],
    'active_listings': ['active', 'available', 'for sale', 'active listings'],
    'age_of_inventory': ['age', 'inventory age', 'age of inventory'],
    'homes_sold': ['sold', 'sales', 'closed', 'homes sold'],
    'pending_sales': ['pending', 'under contract', 'pending sales'],
    'off_market_in_2_weeks': ['off market', 'quick sale', '2 weeks', 'fast sale'],
    'median_days_on_market': ['dom', 'days on market', 'time to sell'],
    'median_days_to_close': ['close', 'closing time', 'days to close'],
    'sale_to_list_ratio': ['ratio', 'sale to list', 'discount', 'over asking'],
    'pct_listings_w__price_drops': ['price drops', 'drops', 'reductions', 'price cuts']
}

def load_metro_data():
    """Load metro data from chart directory"""
    charts_dir = Path('/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/2025-08-22')
    metros = {}
    
    if charts_dir.exists():
        for metro_dir in charts_dir.iterdir():
            if metro_dir.is_dir():
                metro_slug = metro_dir.name
                metro_name = ' '.join(word.title() for word in metro_slug.split('_'))
                metros[metro_slug] = metro_name
    
    return metros

def find_metro(query, metros):
    """Find matching metro"""
    query = query.lower().strip()
    
    # Direct match
    query_slug = query.replace(' ', '_')
    if query_slug in metros:
        return query_slug
    
    # Fuzzy match
    metro_names = [m.lower() for m in metros.values()]
    matches = get_close_matches(query, metro_names, n=1, cutoff=0.6)
    if matches:
        for slug, name in metros.items():
            if name.lower() == matches[0]:
                return slug
    
    # Partial match
    for slug, name in metros.items():
        if query in name.lower() or query in slug:
            return slug
    
    return None

def find_metric(query):
    """Find matching metric"""
    query = query.lower().strip()
    
    for metric, aliases in METRICS.items():
        if query in aliases:
            return metric
        for alias in aliases:
            if alias in query or query in alias:
                return metric
    
    return None

def parse_query(query):
    """Parse query into city, metric, and format"""
    chart_type = 'social'  # default
    
    if 'mobile' in query.lower():
        chart_type = 'mobile'
        query = query.replace('mobile', '').strip()
    
    words = query.lower().split()
    
    # Try to split at common metric keywords
    for i, word in enumerate(words):
        if word in ['price', 'supply', 'listings', 'sold', 'dom', 'days', 'ratio', 'active', 'new', 'pending']:
            if i > 0:
                city = ' '.join(words[:i])
                metric = ' '.join(words[i:])
                return city, metric, chart_type
    
    # Default split
    if len(words) >= 2:
        city = ' '.join(words[:-1])
        metric = words[-1]
    else:
        city = query
        metric = 'price'
    
    return city, metric, chart_type

def copy_to_clipboard(text):
    """Copy to clipboard"""
    try:
        subprocess.run(['pbcopy'], input=text.encode(), check=True)
        return True
    except:
        return False

def main():
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    
    if not query:
        print("❌ Please provide a query")
        print("Examples: 'denver median price', 'austin active mobile'")
        sys.exit(1)
    
    # Parse query
    city_query, metric_query, chart_type = parse_query(query)
    
    # Load metros
    metros = load_metro_data()
    if not metros:
        print("❌ Could not load chart data")
        sys.exit(1)
    
    # Find metro
    metro_slug = find_metro(city_query, metros)
    if not metro_slug:
        print(f"❌ City '{city_query}' not found")
        print("Try: denver, austin, miami, seattle, etc.")
        sys.exit(1)
    
    # Find metric
    metric = find_metric(metric_query)
    if not metric:
        print(f"❌ Metric '{metric_query}' not found")
        print("Try: price, active, sold, dom, supply")
        sys.exit(1)
    
    # Generate URLs
    config = CHART_TYPES[chart_type]
    filename = f"{metro_slug}_{metric}{config['suffix']}"
    url = f"{config['base_url']}/{config['date_format']}/{metro_slug}/{filename}"
    
    # Copy to clipboard
    if copy_to_clipboard(url):
        print(f"✅ Copied: {metros[metro_slug]} - {metric.replace('_', ' ').title()}")
    
    print(f"🔗 {url}")

if __name__ == "__main__":
    main()