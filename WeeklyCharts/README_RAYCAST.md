# Home Economics Chart Lookup for Raycast

This directory contains a Raycast script for quickly accessing Redfin metro chart URLs.

## Setup

1. **Install the script in Raycast:**
   - Open Raycast
   - Go to Extensions → Script Commands
   - Click "+" to add a new script directory
   - Select this WeeklyCharts directory
   - The script will appear as "Get Chart URL" in Raycast

2. **Usage in Raycast:**
   - Open Raycast (⌘ + Space)
   - Type "Get Chart URL" or just "chart"
   - Enter your search query like: `dallas median price`
   - The URL will be automatically copied to your clipboard

## Search Examples

- `dallas median price` → Dallas median sale price (social format)
- `fort collins active` → Fort Collins active listings  
- `hot springs dom` → Hot Springs days on market
- `austin price mobile` → Austin price (mobile format)

## Supported Metrics

- **price**, median price, sale price → Median Sale Price
- **active**, available, for sale → Active Listings  
- **new**, listings, new homes → New Listings
- **sold**, sales, closed → Homes Sold
- **supply**, weeks, inventory → Weeks Supply
- **dom**, days on market → Median Days on Market
- **pending**, under contract → Pending Sales
- **age**, inventory age → Age of Inventory
- **close**, closing time → Median Days to Close
- **ratio**, sale to list → Sale to List Ratio
- **drops**, price cuts → Price Drop Percentage

## Chart Types

- **social** (default) → Social media format charts
- **mobile** → Mobile-optimized format charts

Add "mobile" to your query to get mobile format: `dallas price mobile`

## Features

- ✅ Fuzzy city name matching (handles typos)
- ✅ Comprehensive metric aliases
- ✅ Automatic clipboard copying
- ✅ Shows both social and mobile URLs
- ✅ Error handling with suggestions
- ✅ Handles ambiguous queries

## Files

- `raycast_chart_lookup.py` - Main Raycast script
- `README_RAYCAST.md` - This documentation file

## URL Structure

Charts are hosted at:
```
https://home-economics.us/charts/[social|mobile]/2025-08-22/[city_slug]/[city_slug]_[metric]_[format].png
```

Example:
```
https://home-economics.us/charts/social/2025-08-22/dallas_tx/dallas_tx_median_sale_price_social.png
```