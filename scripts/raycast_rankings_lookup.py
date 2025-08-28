#!/usr/bin/env python3

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Get Metro Rankings
# @raycast.mode compact
# @raycast.packageName Home Economics Rankings

# Optional parameters:
# @raycast.icon 📈
# @raycast.argument1 { "type": "text", "placeholder": "Metric (e.g., price, supply, sold)", "optional": false }
# @raycast.argument2 { "type": "text", "placeholder": "Filter (e.g., large, major, all)", "optional": true }

# Documentation:
# @raycast.description Get top metro rankings for real estate metrics
# @raycast.author Aziz Sunderji
# @raycast.authorURL https://home-economics.us

import sys
import subprocess
import webbrowser

# Metric mappings
METRIC_URLS = {
    'price': 'median_sale_price',
    'median price': 'median_sale_price',
    'sale price': 'median_sale_price',
    'listings': 'active_listings',
    'active': 'active_listings',
    'available': 'active_listings',
    'supply': 'weeks_supply',
    'weeks': 'weeks_supply',
    'inventory': 'weeks_supply',
    'sold': 'homes_sold',
    'sales': 'homes_sold',
    'closed': 'homes_sold',
    'new': 'new_listings',
    'new listings': 'new_listings',
    'dom': 'median_days_on_market',
    'days on market': 'median_days_on_market',
    'days': 'median_days_on_market',
    'pending': 'pending_sales',
    'under contract': 'pending_sales',
    'off market': 'off_market_in_2_weeks',
    '2 weeks': 'off_market_in_2_weeks',
    'close': 'median_days_to_close',
    'closing': 'median_days_to_close',
    'ratio': 'sale_to_list_ratio',
    'sale to list': 'sale_to_list_ratio',
    'drops': 'pct_listings_w__price_drops',
    'price drops': 'pct_listings_w__price_drops',
    'reductions': 'pct_listings_w__price_drops',
    'age': 'age_of_inventory',
    'inventory age': 'age_of_inventory'
}

# Filter mappings (for URL parameters in future)
FILTERS = {
    'large': 'top10',
    'major': 'top25',
    'mid': 'top50',
    'midsize': 'top50',
    'all': 'all'
}

def find_metric(query):
    """Find the matching metric slug."""
    query = query.lower().strip()
    
    # Direct match
    if query in METRIC_URLS:
        return METRIC_URLS[query]
    
    # Partial match
    for key, value in METRIC_URLS.items():
        if query in key or key in query:
            return value
    
    return None

def copy_to_clipboard(text):
    """Copy text to macOS clipboard."""
    process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
    process.communicate(text.encode())

def main():
    if len(sys.argv) < 2:
        print("Usage: raycast_rankings_lookup.py <metric> [filter]")
        sys.exit(1)
    
    metric_query = sys.argv[1]
    filter_query = sys.argv[2] if len(sys.argv) > 2 else 'major'
    
    # Find metric
    metric_slug = find_metric(metric_query)
    if not metric_slug:
        print(f"❌ Metric not found: '{metric_query}'")
        print("Try: price, supply, sold, dom, pending, drops, etc.")
        sys.exit(1)
    
    # Build URL
    base_url = "https://www.home-economics.us/live/rankings/"
    ranking_url = f"{base_url}{metric_slug}.html"
    
    # Add filter parameter if needed (for future JS implementation)
    filter_param = FILTERS.get(filter_query.lower(), 'top25')
    if filter_param != 'top25':
        ranking_url += f"#{filter_param}"
    
    # Copy URL to clipboard
    copy_to_clipboard(ranking_url)
    
    # Open in browser
    webbrowser.open(ranking_url)
    
    # Display result
    metric_display = metric_slug.replace('_', ' ').title()
    filter_display = {
        'top10': 'Large Markets (Top 10%)',
        'top25': 'Major Markets (Top 25%)',
        'top50': 'Mid-Size Markets (Top 50%)',
        'all': 'All Markets'
    }.get(filter_param, 'Major Markets')
    
    print(f"📈 {metric_display} Rankings")
    print(f"🎯 Filter: {filter_display}")
    print(f"✅ Opening in browser...")
    print(f"🔗 {ranking_url}")

if __name__ == "__main__":
    main()