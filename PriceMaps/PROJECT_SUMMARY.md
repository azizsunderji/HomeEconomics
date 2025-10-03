# PriceMaps Pro - Project Summary

## Overview
Interactive home price mapping tool with advanced features for investors, developers, and real estate professionals.

## Current Status: Week 1 Complete ✅

### Completed Features
- ✅ **Week 1: Boundary Drawing** (SHIPPED)
  - Draw custom polygons/rectangles on map
  - Dynamic rebucketing of ZIPs within drawn boundaries
  - Only ZIPs inside boundary remain visible
  - "Clear Boundary" functionality
  - Small sample handling (< 5 ZIPs uses equal-width buckets)
  - Subtle "(small sample)" indicator for 2-4 ZIP selections

### Core Features (Already Implemented)
- Search with autocomplete (ZIP codes and place names)
- Local view mode (viewport-based quintile recalculation)
- Population-weighted bubble sizing
- Interactive tooltips
- State boundaries that adapt to zoom level
- Smooth fly-to animations

## Product Development Roadmap (6-Week Pro Launch)

### Week 2: Price Level vs Y/Y Toggle
**Goal**: Let users switch between viewing current price levels and year-over-year changes
- Add toggle button in UI
- Load both datasets (price levels + Y/Y changes)
- Switch which field is displayed: `price` vs `p` (y/y change)
- Update legend and color scales dynamically
- **File size impact**: +400 KB (price data for 26K ZIPs)

### Week 3: ZIP Code Boundaries (Geometry View)
**Goal**: Show actual ZIP boundaries instead of bubbles
- Add "Show Boundaries" toggle (zoom-dependent, only 6+)
- Lazy-load simplified GeoJSON geometries (~10 MB)
- Viewport-only loading (fetch visible ZIPs first)
- Two-tier simplification (ultra at zoom 6-7, medium at 8+)
- IndexedDB caching for subsequent visits
- **Strategy**: Most users never trigger load (zoom-dependent)

### Week 4: Acceleration/Deceleration Detector
**Goal**: Show markets that are speeding up or slowing down (momentum)
- Requires: 3-month, 6-month, 12-month historical data
- Calculate rate of change of price changes
- Color-code by acceleration (green) vs deceleration (red)
- New metric: "3mo vs 6mo vs 12mo growth rate comparison"
- **Value**: Catch inflection points BEFORE markets peak or bottom
- **Critical prep**: Verify Zillow data has monthly history snapshots

### Week 5: Time Machine (Historical Playback)
**Goal**: Animate map through time, watch growth patterns spread
- Month-by-month playback over 3-5 years
- Scrubber control (like video player)
- Play/pause/speed controls
- Watch "heat waves" of growth spread geographically
- **Data req**: Store 36 monthly snapshots (~80 MB total, needs compression)
- **Alternative**: Generate on-the-fly from historical Zillow files

### Week 6: Home Economics Forecasts
**Goal**: 6-month price projections based on momentum + indicators
- Simple momentum-based model initially
- Display: "If current trends continue, this ZIP will be at $X in 6 months"
- Show confidence intervals
- **Legal**: Add prominent disclaimers
- **Builds on**: Week 4 acceleration data
- **Note**: Consider swapping with Week 5 (time machine helps validate forecasts)

## Technical Architecture

### Files Structure
```
PriceMaps/
├── scripts/
│   ├── create_sophisticated_map.py  → Generates ProMap.html (Week 1 complete)
│   ├── create_yoy_map.py           → Standard Y/Y map (unchanged)
│   ├── create_price_levels.py      → Standard price levels (unchanged)
│   └── download_data.py            → Zillow data fetcher
├── output/
│   ├── ProMap.html                 → NEW Pro version (2.2 MB)
│   ├── us_yoy_price_map_with_search.html       → Existing (6.2 MB)
│   └── us_price_levels_with_search.html        → Existing
└── .github/workflows/
    ├── update_maps.yml             → Existing workflow (untouched)
    └── update_pro_map.yml          → NEW workflow (to be created)
```

### ProMap.html Features
- Leaflet.draw integration for boundary drawing
- Dynamic quintile calculation (viewport, boundary, or global)
- Equal-width bucketing for small samples (< 5 ZIPs)
- Smart sample size indicators
- All existing features: search, local view, zoom-adaptive styling

### Data Flow
1. Zillow CSV downloaded daily (GitHub Actions)
2. Python script processes data → generates HTML with embedded JSON
3. FTP deployment to home-economics.us server
4. ProMap.html = standalone file (no external data dependencies)

## Next Steps (Immediate)

### 1. Create GitHub Workflow
- **File**: `.github/workflows/update_pro_map.yml`
- Daily at 9 AM EST (same as existing maps)
- Runs `create_sophisticated_map.py`
- Deploys ProMap.html to server
- **Server path**: `/home2/yxwrmjmy/public_html/wp-content/uploads/reports/live/PriceMaps/ProMap.html`

### 2. Commit & Push
- `scripts/create_sophisticated_map.py` (updated)
- `output/ProMap.html` (initial version)
- `.github/workflows/update_pro_map.yml` (new)
- `PROJECT_SUMMARY.md` (this file)

### 3. Test Workflow
- Manual trigger on GitHub
- Verify generation works
- Verify FTP deployment succeeds

## Critical Path Items (For Future Weeks)

### Week 3 Prep (ZIP Boundaries)
- [ ] Extract ZIP shapefiles from Census data
- [ ] Run simplification script (Douglas-Peucker, 20-30 points per ZIP)
- [ ] Generate test regional JSON files
- [ ] Verify compressed file sizes (~8-10 MB target)

### Week 4 Prep (Acceleration)
- [ ] **URGENT**: Verify Zillow data has monthly history (not just latest + 1yr ago)
- [ ] If not: Start collecting monthly snapshots NOW
- [ ] Need minimum: current, -3mo, -6mo, -12mo

### Week 6 Prep (Time Machine)
- [ ] Archive monthly Zillow CSVs (if not already doing this)
- [ ] Or: Build script to regenerate from Zillow historical API
- [ ] Decide lookback period: 2 years? 5 years? 10 years?
- [ ] Test compression strategies for 36+ monthly snapshots

## Monetization Strategy
- **Free maps**: Price levels + Y/Y changes (existing)
- **Pro map**: Boundary drawing, future features (paywalled)
- **Target**: $50-100/month for serious investors/developers
- **Value prop**: Tools that make money (inflection detection, forecasting)

## Performance Metrics
- **File size**: 2.2 MB (ProMap.html with boundary drawing)
- **Load time**: < 2 seconds on decent connection
- **With gzip**: ~700 KB actual transfer
- **ZIP count**: 26,303 active ZIPs
- **Features**: 8 major features implemented (Week 1)

## Risk Assessment
| Week | Risk Level | Mitigation |
|------|-----------|------------|
| 2 | Low | Straightforward data addition |
| 3 | Medium | Start geometry prep early |
| 4 | Medium | **Verify data availability first** |
| 5 | High | Most complex feature, may take 2 weeks |
| 6 | Medium-High | Add strong legal disclaimers |

## Success Criteria
- ✅ Week 1: Boundary drawing working flawlessly
- ⏳ Week 2-6: Each feature ships on schedule
- 🎯 End of Week 6: Pro map has 5+ unique features
- 💰 Post-launch: Paywall generates revenue

---

**Last Updated**: October 2, 2025
**Status**: Week 1 complete, Week 2 planning
**Next Milestone**: GitHub workflow deployment
