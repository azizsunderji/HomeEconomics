#!/usr/bin/python3
# @raycast.schemaVersion 1
# @raycast.title Get Chart URL
# @raycast.packageName Home Economics
# @raycast.mode fullOutput
# @raycast.icon 📊
# @raycast.argument1 { "type": "text", "placeholder": "e.g. dallas median price [mobile]" }
# @raycast.description Look up a Home Economics chart URL by city + metric; copies URL to clipboard.
# @raycast.author Aziz Sunderji
# @raycast.authorURL https://home-economics.us

"""
Home Economics Chart Lookup Tool for Raycast
============================================

This script provides quick access to Redfin metro chart URLs for real estate data visualization.
Charts are hosted at home-economics.us and available in both social media and mobile formats.

USAGE EXAMPLES:
  "dallas median price"           → Dallas median sale price (social format)
  "fort collins active"          → Fort Collins active listings 
  "hot springs dom"              → Hot Springs days on market
  "austin price mobile"          → Austin median price (mobile format)

SUPPORTED METRICS:
  • price, median price, sale price    → Median Sale Price
  • active, available, for sale        → Active Listings  
  • new, listings, new homes           → New Listings
  • sold, sales, closed               → Homes Sold
  • supply, weeks, inventory          → Weeks Supply
  • dom, days on market              → Median Days on Market
  • pending, under contract          → Pending Sales
  • age, inventory age               → Age of Inventory
  • close, closing time             → Median Days to Close
  • ratio, sale to list             → Sale to List Ratio
  • drops, price cuts, reductions   → Price Drop Percentage
  • off market, quick sale          → Off Market in 2 Weeks

CHART TYPES:
  • social (default) → Social media format charts
  • mobile          → Mobile-optimized format charts

FEATURES:
  • Fuzzy city name matching (handles typos and abbreviations)
  • Comprehensive metric aliases (flexible search terms)
  • Automatic URL copying to clipboard
  • Shows both social and mobile URLs
  • Handles ambiguous queries with multiple suggestions
  • Error handling with helpful suggestions

URL STRUCTURE:
  https://home-economics.us/charts/[social|mobile]/2025-08-22/[city_slug]/[city_slug]_[metric]_[format].png
"""

import sys
import json
import subprocess
import re
from pathlib import Path
from difflib import get_close_matches
from datetime import datetime

# Chart types configuration
CHART_TYPES = {
    'social': {
        'base_url': 'https://home-economics.us/charts/social',
        'suffix': '_social.png',
        'date_format': '2025-08-22'  # Current date format used in URL
    },
    'mobile': {
        'base_url': 'https://home-economics.us/charts/mobile', 
        'suffix': '_mobile.png',
        'date_format': '2025-08-22'
    }
}

# Metric mappings with comprehensive aliases
METRICS = {
    'median_sale_price': [
        'price', 'sale price', 'median price', 'home price', 'house price', 
        'median sale price', 'sale', 'cost', 'pricing'
    ],
    'weeks_supply': [
        'supply', 'weeks', 'inventory weeks', 'weeks supply', 'weeks on market',
        'inventory supply', 'stock'
    ],
    'new_listings': [
        'new', 'listings', 'new homes', 'new listings', 'fresh', 'coming to market',
        'listed', 'new inventory'
    ],
    'active_listings': [
        'active', 'available', 'for sale', 'active listings', 'on market',
        'current listings', 'inventory', 'stock'
    ],
    'age_of_inventory': [
        'age', 'days old', 'inventory age', 'age of inventory', 'how old',
        'time on market', 'vintage'
    ],
    'homes_sold': [
        'sold', 'sales', 'closed', 'homes sold', 'sales volume', 'closings',
        'transactions', 'completed sales'
    ],
    'pending_sales': [
        'pending', 'under contract', 'pending sales', 'contracts', 'escrow',
        'in process', 'committed'
    ],
    'off_market_in_2_weeks': [
        'off market', 'quick sale', '2 weeks', 'fast sale', 'rapid',
        'quick turnaround', 'fast moving', 'hot market'
    ],
    'median_days_on_market': [
        'dom', 'days on market', 'time to sell', 'median days on market',
        'days to sell', 'market time', 'selling time', 'time on market'
    ],
    'median_days_to_close': [
        'close', 'closing time', 'days to close', 'median days to close',
        'time to close', 'closing period', 'escrow time'
    ],
    'sale_to_list_ratio': [
        'ratio', 'sale to list', 'discount', 'over asking', 'sale to list ratio',
        'price ratio', 'asking vs sale', 'negotiation'
    ],
    'pct_listings_w__price_drops': [
        'price drops', 'drops', 'reductions', 'price cuts', 'markdowns',
        'price reductions', 'discounts', 'cuts', 'reduced'
    ]
}

# Common city aliases and abbreviations
CITY_ALIASES = {
    'sf': 'san_francisco',
    'la': 'los_angeles', 
    'nyc': 'new_york',
    'dc': 'washington',
    'chi': 'chicago',
    'phx': 'phoenix',
    'austin': 'austin_tx',
    'miami': 'miami_fl',
    'denver': 'denver_co',
    'seattle': 'seattle_wa',
    'portland': 'portland_or',
    'vegas': 'las_vegas',
    'san diego': 'san_diego',
    'san francisco': 'san_francisco',
    'los angeles': 'los_angeles',
    'new york': 'new_york',
    'washington': 'washington_dc'
}

def load_metro_data():
    """Load metro data from local chart directory structure"""
    charts_dir = Path('/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/social_charts/2025-08-22')
    metros = {}
    
    if charts_dir.exists():
        for metro_dir in charts_dir.iterdir():
            if metro_dir.is_dir():
                metro_slug = metro_dir.name
                # Convert slug to display name (e.g., "austin_tx" -> "Austin TX")
                display_parts = metro_slug.split('_')
                metro_name = ' '.join(word.title() for word in display_parts)
                
                # Create search aliases
                aliases = [
                    metro_name.lower(),
                    metro_slug.replace('_', ' ').lower(),
                    display_parts[0].lower(),  # Just city name
                ]
                
                # Add state abbreviation as alias if present
                if len(display_parts) > 1:
                    state = display_parts[-1].upper()
                    aliases.extend([
                        state.lower(),
                        f"{display_parts[0].lower()} {state.lower()}",
                    ])
                
                # Add any specific aliases from our mapping
                city_lower = display_parts[0].lower()
                if city_lower in CITY_ALIASES.values():
                    # Find keys that map to this value
                    for alias, slug in CITY_ALIASES.items():
                        if slug == metro_slug:
                            aliases.append(alias)
                
                metros[metro_slug] = {
                    'name': metro_name,
                    'aliases': list(set(filter(None, aliases)))
                }
    
    return metros

def parse_query(query):
    """Parse a combined query like 'denver median price' into city and metric parts"""
    # First, try to extract chart type preference
    chart_type = 'social'  # default
    if 'mobile' in query.lower():
        chart_type = 'mobile'
        query = query.replace('mobile', '').strip()
    elif 'social' in query.lower():
        chart_type = 'social'
        query = query.replace('social', '').strip()
    
    # Common patterns to split city from metric
    words = query.lower().split()
    
    # Simple approach: try splitting at common metric keywords
    if len(words) >= 2:
        # Try splitting at common metric words
        for i, word in enumerate(words):
            if word in ['price', 'supply', 'listings', 'sold', 'dom', 'days', 'ratio', 'median', 'active', 'new', 'pending']:
                if i > 0:  # Make sure there's a city part
                    city_query = ' '.join(words[:i])
                    metric_query = ' '.join(words[i:])
                    return city_query.strip(), metric_query.strip(), chart_type
        
        # If no metric keyword found, default split: last word as metric, rest as city
        city_query = ' '.join(words[:-1])
        metric_query = words[-1]
    else:
        # Single word - assume it's a city
        city_query = query
        metric_query = 'price'  # default to simple price
    
    return city_query.strip(), metric_query.strip(), chart_type

def find_metro(query, metros):
    """Find matching metros using fuzzy search"""
    if not query:
        return []
        
    query = query.lower().strip()
    
    # Check city aliases first
    if query in CITY_ALIASES:
        target_slug = CITY_ALIASES[query]
        if target_slug in metros:
            return [(target_slug, metros[target_slug]['name'], 1.0)]
    
    # Direct slug match
    query_slug = query.replace(' ', '_').replace(',', '').lower()
    if query_slug in metros:
        return [(query_slug, metros[query_slug]['name'], 1.0)]
    
    # Build search candidates
    candidates = []
    for slug, data in metros.items():
        # Add metro name
        candidates.append((slug, data['name'].lower(), data['name'], 1.0))
        # Add all aliases
        for alias in data['aliases']:
            candidates.append((slug, alias, data['name'], 0.9))
    
    # Exact match on search strings
    exact_matches = [c for c in candidates if c[1] == query]
    if exact_matches:
        return [(c[0], c[2], c[3]) for c in exact_matches[:5]]
    
    # Fuzzy matching
    search_strings = [c[1] for c in candidates]
    fuzzy_matches = get_close_matches(query, search_strings, n=10, cutoff=0.6)
    
    results = []
    seen_slugs = set()
    
    for match in fuzzy_matches:
        for slug, search_str, display_name, base_score in candidates:
            if search_str == match and slug not in seen_slugs:
                score = base_score * (1.0 if search_str == query else 0.8)
                results.append((slug, display_name, score))
                seen_slugs.add(slug)
                break
    
    # Partial matching for broader search
    for slug, data in metros.items():
        if slug not in seen_slugs:
            if query in data['name'].lower():
                results.append((slug, data['name'], 0.7))
                seen_slugs.add(slug)
            else:
                for alias in data['aliases']:
                    if query in alias:
                        results.append((slug, data['name'], 0.6))
                        seen_slugs.add(slug)
                        break
    
    # Sort by score and return top matches
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:5]

def find_metric(query):
    """Find best matching metric using comprehensive fuzzy search"""
    if not query:
        return None
        
    query = query.lower().strip()
    
    # Direct metric name match
    query_formatted = query.replace(' ', '_').replace('-', '_')
    if query_formatted in METRICS:
        return query_formatted
    
    # Exact alias match
    for metric, aliases in METRICS.items():
        if query in aliases:
            return metric
    
    # Fuzzy match on aliases
    all_candidates = []
    for metric, aliases in METRICS.items():
        for alias in aliases:
            all_candidates.append((metric, alias))
    
    # Try exact substring matches first
    exact_substrings = []
    for metric, alias in all_candidates:
        if query in alias or alias in query:
            exact_substrings.append((metric, alias))
    
    if exact_substrings:
        # Return the metric with the shortest matching alias (most specific)
        best_match = min(exact_substrings, key=lambda x: len(x[1]))
        return best_match[0]
    
    # Fuzzy match on all aliases
    alias_strings = [alias for _, alias in all_candidates]
    fuzzy_matches = get_close_matches(query, alias_strings, n=1, cutoff=0.6)
    
    if fuzzy_matches:
        best_alias = fuzzy_matches[0]
        for metric, alias in all_candidates:
            if alias == best_alias:
                return metric
    
    # Fuzzy match on metric names themselves
    metric_names = list(METRICS.keys())
    name_matches = get_close_matches(query_formatted, metric_names, n=1, cutoff=0.6)
    if name_matches:
        return name_matches[0]
    
    return None

def generate_chart_url(metro_slug, metric, chart_type='social'):
    """Generate the chart URL based on the current URL structure"""
    config = CHART_TYPES[chart_type]
    filename = f"{metro_slug}_{metric}{config['suffix']}"
    
    # Use the URL structure: base_url/date/metro_slug/filename
    chart_url = f"{config['base_url']}/{config['date_format']}/{metro_slug}/{filename}"
    
    return chart_url

def copy_to_clipboard(text):
    """Copy text to macOS clipboard"""
    try:
        process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        process.communicate(text.encode())
        return True
    except:
        return False

def main():
    # Get the query from Raycast (or command line for testing)
    combined_query = sys.argv[1] if len(sys.argv) > 1 else ""
    
    if not combined_query:
        print("❌ No query provided")
        print("Usage: '<city> <metric>' or '<city> <metric> mobile'")
        print("Examples:")
        print("  'denver median price'")
        print("  'austin active listings'") 
        print("  'sf days on market mobile'")
        sys.exit(1)
    
    # Parse the combined query
    city_query, metric_query, chart_type = parse_query(combined_query)
    
    # Load metro data
    metros = load_metro_data()
    if not metros:
        print("❌ Could not load metro data from charts directory")
        sys.exit(1)
    
    # Find metro candidates
    metro_candidates = find_metro(city_query, metros)
    if not metro_candidates:
        print(f"❌ City not found: '{city_query}'")
        print("💡 Try: denver, austin, seattle, miami, dallas, etc.")
        sys.exit(1)
    
    # Handle multiple matches
    if len(metro_candidates) > 1 and metro_candidates[0][2] < 0.95:  # Not a clear winner
        print(f"🔍 Multiple matches for '{city_query}':")
        for i, (slug, name, score) in enumerate(metro_candidates[:3], 1):
            print(f"  {i}. {name}")
        print(f"\n➡️  Using best match: {metro_candidates[0][1]}")
        print("💡 Tip: Be more specific to avoid ambiguity")
    
    metro_slug = metro_candidates[0][0]
    metro_name = metro_candidates[0][1]
    
    # Find metric
    metric = find_metric(metric_query)
    if not metric:
        print(f"❌ Metric not found: '{metric_query}'")
        print("💡 Try: price, supply, active, sold, dom, days on market, etc.")
        available_metrics = sorted(METRICS.keys())
        print("📋 Available metrics:")
        for m in available_metrics[:8]:  # Show first 8
            display_name = m.replace('_', ' ').title()
            print(f"   • {display_name}")
        if len(available_metrics) > 8:
            print(f"   ... and {len(available_metrics) - 8} more")
        sys.exit(1)
    
    # Generate URLs for both chart types
    social_url = generate_chart_url(metro_slug, metric, 'social')
    mobile_url = generate_chart_url(metro_slug, metric, 'mobile')
    
    # Use the requested chart type
    primary_url = social_url if chart_type == 'social' else mobile_url
    
    # Copy to clipboard
    clipboard_success = copy_to_clipboard(primary_url)
    
    # Display results
    metric_display = metric.replace('_', ' ').title()
    chart_type_display = chart_type.title()
    
    print(f"\n📊 {metro_name} - {metric_display} ({chart_type_display})")
    
    if clipboard_success:
        print("✅ URL copied to clipboard!")
    else:
        print("⚠️  Could not copy to clipboard")
    
    print(f"🔗 {primary_url}")
    
    # Show alternative chart type URL
    alt_type = 'mobile' if chart_type == 'social' else 'social'
    alt_url = mobile_url if chart_type == 'social' else social_url
    print(f"📱 {alt_type.title()}: {alt_url}")

if __name__ == "__main__":
    main()