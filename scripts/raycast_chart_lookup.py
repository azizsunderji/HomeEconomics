#!/usr/bin/env python3

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Get Chart URL
# @raycast.mode compact
# @raycast.packageName Home Economics Charts

# Optional parameters:
# @raycast.icon 📊
# @raycast.argument1 { "type": "text", "placeholder": "City (e.g., denver)", "optional": false }
# @raycast.argument2 { "type": "text", "placeholder": "Metric (e.g., price)", "optional": false }

# Documentation:
# @raycast.description Get URL for a city's real estate chart
# @raycast.author Aziz Sunderji
# @raycast.authorURL https://home-economics.us

import sys
import json
import subprocess
from pathlib import Path
from difflib import get_close_matches

# Metric mappings with aliases
METRICS = {
    'median_sale_price': ['price', 'sale price', 'median price', 'home price', 'house price'],
    'weeks_supply': ['supply', 'weeks', 'inventory weeks'],
    'new_listings': ['new', 'listings', 'new homes'],
    'active_listings': ['active', 'available', 'for sale'],
    'age_of_inventory': ['age', 'days old', 'inventory age'],
    'homes_sold': ['sold', 'sales', 'closed'],
    'pending_sales': ['pending', 'under contract'],
    'off_market_in_2_weeks': ['off market', 'quick sale', '2 weeks'],
    'median_days_on_market': ['dom', 'days on market', 'time to sell'],
    'median_days_to_close': ['close', 'closing time', 'days to close'],
    'sale_to_list_ratio': ['ratio', 'sale to list', 'discount', 'over asking'],
    'pct_listings_w__price_drops': ['price drops', 'drops', 'reductions', 'price cuts']
}

def load_metro_data():
    """Load metro data from JSON or generate from hardcoded list"""
    # Try to load from cached JSON first
    json_file = Path(__file__).parent / 'metro_index.json'
    
    if json_file.exists():
        with open(json_file, 'r') as f:
            return json.load(f)
    
    # Otherwise, return a comprehensive list of metros
    # This could be loaded from a cities.txt file or API in the future
    # For now, using major metros as examples
    metros = {
        'denver_co_metro_area': {'name': 'Denver, CO Metro Area', 'aliases': ['denver', 'den', 'co']},
        'seattle_wa_metro_area': {'name': 'Seattle, WA Metro Area', 'aliases': ['seattle', 'sea', 'wa']},
        'austin_tx_metro_area': {'name': 'Austin, TX Metro Area', 'aliases': ['austin', 'atx', 'tx']},
        'miami_fl_metro_area': {'name': 'Miami, FL Metro Area', 'aliases': ['miami', 'mia', 'fl']},
        'new_york_ny_metro_area': {'name': 'New York, NY Metro Area', 'aliases': ['new york', 'nyc', 'ny']},
        'los_angeles_ca_metro_area': {'name': 'Los Angeles, CA Metro Area', 'aliases': ['los angeles', 'la', 'ca']},
        'chicago_il_metro_area': {'name': 'Chicago, IL Metro Area', 'aliases': ['chicago', 'chi', 'il']},
        'san_francisco_ca_metro_area': {'name': 'San Francisco, CA Metro Area', 'aliases': ['san francisco', 'sf', 'bay area']},
        'boston_ma_metro_area': {'name': 'Boston, MA Metro Area', 'aliases': ['boston', 'bos', 'ma']},
        'washington_dc_metro_area': {'name': 'Washington, DC Metro Area', 'aliases': ['washington', 'dc', 'dmv']},
        # Add more metros as needed
    }
    
    # Save for next time
    with open(json_file, 'w') as f:
        json.dump(metros, f)
    
    return metros

def find_metro(query, metros):
    """Find matching metros using fuzzy search, return list of candidates"""
    query = query.lower().strip()
    
    # First try exact slug match
    query_slug = query.replace(' ', '_').replace(',', '').lower()
    if query_slug in metros:
        return [(query_slug, metros[query_slug]['name'], 1.0)]  # Perfect match
    
    # Build search list with all names and aliases
    search_items = []
    for slug, data in metros.items():
        search_items.append((slug, data['name'].lower(), data['name']))
        for alias in data['aliases']:
            search_items.append((slug, alias, data['name']))
    
    # Find close matches
    search_strings = [item[1] for item in search_items]
    matches = get_close_matches(query, search_strings, n=5, cutoff=0.5)
    
    results = []
    seen_slugs = set()
    
    if matches:
        # Get all matching metros
        for match in matches:
            for slug, search_str, display_name in search_items:
                if search_str == match and slug not in seen_slugs:
                    # Calculate match score
                    score = 1.0 if match == query else 0.8
                    results.append((slug, display_name, score))
                    seen_slugs.add(slug)
    
    # Also try partial matching
    for slug, data in metros.items():
        if slug not in seen_slugs:
            if query in data['name'].lower():
                results.append((slug, data['name'], 0.6))
                seen_slugs.add(slug)
            else:
                for alias in data['aliases']:
                    if query in alias and slug not in seen_slugs:
                        results.append((slug, data['name'], 0.5))
                        seen_slugs.add(slug)
                        break
    
    # Sort by score and limit to top 5
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:5]

def find_metric(query):
    """Find best matching metric using fuzzy search"""
    query = query.lower().strip()
    
    # Direct metric name match
    query_formatted = query.replace(' ', '_').replace('-', '_')
    if query_formatted in METRICS:
        return query_formatted
    
    # Check aliases
    for metric, aliases in METRICS.items():
        if query in aliases:
            return metric
        # Partial match on aliases
        for alias in aliases:
            if query in alias or alias in query:
                return metric
    
    # Fuzzy match on metric names
    metric_names = list(METRICS.keys())
    matches = get_close_matches(query_formatted, metric_names, n=1, cutoff=0.5)
    if matches:
        return matches[0]
    
    # Fuzzy match on all aliases
    all_aliases = []
    for metric, aliases in METRICS.items():
        for alias in aliases:
            all_aliases.append((metric, alias))
    
    alias_strings = [a[1] for a in all_aliases]
    matches = get_close_matches(query, alias_strings, n=1, cutoff=0.5)
    if matches:
        for metric, alias in all_aliases:
            if alias == matches[0]:
                return metric
    
    return None

def copy_to_clipboard(text):
    """Copy text to macOS clipboard"""
    process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
    process.communicate(text.encode())

def main():
    if len(sys.argv) < 3:
        print("Usage: chart_lookup.py <city> <metric>")
        sys.exit(1)
    
    city_query = sys.argv[1]
    metric_query = sys.argv[2]
    
    # Load metro data
    metros = load_metro_data()
    
    # Find metro candidates
    metro_candidates = find_metro(city_query, metros)
    if not metro_candidates:
        print(f"❌ City not found: '{city_query}'")
        print("Try: denver, austin, seattle, etc.")
        sys.exit(1)
    
    # If multiple matches, show them and ask for clarification
    if len(metro_candidates) > 1:
        print(f"🔍 Multiple matches for '{city_query}':")
        for i, (slug, name, score) in enumerate(metro_candidates, 1):
            print(f"{i}. {name}")
        
        # For Raycast, we can't do interactive input, so we'll use the best match
        # but show all options so user knows to be more specific next time
        print(f"\n➡️ Using best match: {metro_candidates[0][1]}")
        print("💡 Tip: Be more specific to avoid ambiguity")
        metro_slug = metro_candidates[0][0]
        metro_name = metro_candidates[0][1]
    else:
        metro_slug = metro_candidates[0][0]
        metro_name = metro_candidates[0][1]
    
    # Find metric
    metric = find_metric(metric_query)
    if not metric:
        print(f"❌ Metric not found: '{metric_query}'")
        print("Try: price, supply, sold, dom, etc.")
        sys.exit(1)
    
    # Generate URL for live charts on Home Economics server
    # Social charts are 1200x1200 for sharing
    chart_url = f"https://www.home-economics.us/live/social/{metro_slug}/{metric}.png"
    
    # Also generate mobile chart URL (for reference)
    mobile_url = f"https://www.home-economics.us/live/mobile/{metro_slug}/{metric}.png"
    
    # Copy social chart URL to clipboard (better for sharing)
    copy_to_clipboard(chart_url)
    
    # Display result
    metric_display = metric.replace('_', ' ').title()
    print(f"\n📊 {metro_name} - {metric_display}")
    print(f"✅ URL copied to clipboard!")
    print(f"🔗 {chart_url}")

if __name__ == "__main__":
    main()