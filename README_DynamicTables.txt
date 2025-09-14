DYNAMIC TABLES TECHNICAL EXPLANATION
====================================

This document explains the complete technology stack and implementation details for the dynamic rankings tables in the Home Economics project.

## OVERVIEW
The dynamic tables (rankings) system is a comprehensive data processing and web visualization pipeline that transforms raw housing market data into interactive, sortable HTML tables with real-time filtering and analysis capabilities.

## DATA PIPELINE & MANIPULATION

### Data Source
- **Primary Data**: Redfin weekly housing market data
- **Format**: Apache Parquet file (`data/weekly_housing_market_data.parquet`)
- **Update Frequency**: Weekly
- **Coverage**: 200+ U.S. metropolitan areas
- **Time Series**: Multi-year historical data with 4-week rolling windows

### Data Processing Technology Stack
1. **Python 3.x** - Core processing language
2. **Pandas** - Primary data manipulation framework
3. **NumPy** - Numerical computations and statistical analysis
4. **Apache Parquet** - Efficient columnar data storage format

### Data Transformation Process (`scripts/generate_metro_rankings_final.py`)

#### Step 1: Data Loading and Filtering
```python
df = pd.read_parquet('data/weekly_housing_market_data.parquet')
# Filter to 4-week duration data for consistency
df = df[df['DURATION'] == '4 weeks'].copy()
# Focus on metro-level data
metros_df = df[df['REGION_TYPE'] == 'metro'].copy()
```

#### Step 2: Market Size Classification
- Calculates 5-year total homes sold for each metro
- Creates market size percentiles (larger markets = lower percentile)
- Enables segmented analysis (Top 10%, Top 25%, etc.)

#### Step 3: Percentage Change Calculations
Uses date-based approach for consistent time period calculations:
- 1 Month: 30 days back
- 3 Month: 90 days back  
- 6 Month: 180 days back
- 1 Year: 365 days back
- 3 Year: 3 years back

Implements percentage change formula: `((current_value - past_value) / past_value) * 100`

#### Step 4: Metric Processing
Handles 12 different housing market metrics:
- **Median Sale Price** - Currency formatting ($1.2M, $450K)
- **Active Listings** - Count formatting (1,234)
- **Weeks of Supply** - Decimal formatting (2.3 weeks)
- **Homes Sold** - Count with seasonal adjustments
- **New Listings** - Count with seasonal adjustments
- **Days on Market** - Integer days
- **Pending Sales** - Count
- **Off Market in 2 Weeks** - Calculated percentage from counts
- **Days to Close** - Integer days
- **Sale to List Ratio** - Percentage (98.5%)
- **Price Drops** - Percentage of listings with price reductions
- **Age of Inventory** - Average days on market

## HTML GENERATION ARCHITECTURE

### Template System
- **Approach**: String interpolation with embedded CSS/JavaScript
- **Responsive Design**: CSS media queries for mobile/desktop optimization
- **Font**: Custom Oracle font loading via web fonts

### CSS Framework
- **Layout**: CSS Grid and Flexbox
- **Color Scheme**: Brand-consistent palette
  - Blue (#0BB4FF) - Primary brand color
  - Cream (#DADFCE) - Secondary/neutral
  - Background Cream (#F6F7F3)
  - Black (#3D3733) - Text
- **Typography**: Oracle custom font family
- **Responsive Breakpoint**: 768px for mobile/desktop

### JavaScript Functionality

#### Core Features
1. **Dynamic Sorting** - Multi-column sorting with visual indicators
2. **Heat Map Coloring** - Gradient coloring based on data values
3. **Real-time Filtering** - Market size and text search filtering
4. **Median Calculations** - Live median values for visible data
5. **Chart Integration** - Modal panels with housing market charts

#### Sorting Algorithm
```javascript
// Bulletproof sorting with null handling
function sortTable(column) {
    allRows.sort((a, b) => {
        const aStr = a.dataset[column];
        const bStr = b.dataset[column];
        
        // Handle null values properly
        if (aStr === 'null' && bStr === 'null') return 0;
        if (aStr === 'null') return sortAscending ? -1 : 1;
        if (bStr === 'null') return sortAscending ? 1 : -1;
        
        const aVal = parseFloat(aStr);
        const bVal = parseFloat(bStr);
        return sortAscending ? aVal - bVal : bVal - aVal;
    });
}
```

#### Heat Map Color System
Progressive color gradient for data visualization:
- **Extreme Negative** (-5%+): Black (#3D3733)
- **Moderate Negative** (-2% to -5%): Medium grey (#A09B95)
- **Slight Negative** (0% to -2%): Light cream (#DADFCE)
- **Slight Positive** (0% to 2%): Very light blue (#C6E4FF)
- **Moderate Positive** (2% to 5%): Light blue (#8CCFFF)
- **Strong Positive** (5%+): Full blue (#0BB4FF)

### Data Attributes System
Each table row contains data attributes for efficient sorting:
```html
<tr data-percentile="5.2" 
    data-metro="san_francisco_ca" 
    data-current="1250000"
    data-month1="2.5" 
    data-month3="-1.2"
    data-year1="8.7">
```

## SUMMARY GENERATION SYSTEM

### AI-Powered Analysis
- **Market Segmentation**: Analysis by market size (Top 10%, 25%, 50%, All)
- **Regional Trends**: Geographic clustering and trend analysis
- **Outlier Detection**: Identifies significant market movements
- **Inflection Point Analysis**: Detects trend reversals using 6-month vs 3-month comparisons

### Summary Features
- National context with current values
- Regional trend identification (Northeast, Southeast, etc.)
- State-level notable changes (median-based, outlier-filtered)
- Market turning points (positive/negative inflections)
- Volatility assessments
- Market spread analysis (top vs bottom performers)

## MOBILE OPTIMIZATION

### Mobile-First Features
1. **Responsive Tables**: Column hiding for essential data only
2. **Touch-Optimized**: Smooth scrolling with -webkit-overflow-scrolling
3. **Simplified Navigation**: Dropdown-based metric selection
4. **Chart Integration**: New tab opening instead of modal panels
5. **Sticky Headers**: Persistent column headers during scroll

### Mobile Column Strategy
- **Visible**: Rank, Metro Name, Current Value, 1-Year Change
- **Hidden**: 1M, 3M, 6M, 3Y, Market Percentile columns

## DEPLOYMENT ARCHITECTURE

### File Generation
- **Output**: 12 HTML files (one per metric) + index.html
- **Cache Busting**: Timestamp-based versioning
- **Size Optimization**: Efficient data attribute encoding

### Hosting Strategy
- **Platform**: WordPress Media Library
- **Path Structure**: `/wp-content/uploads/YYYY/MM/rankings/`
- **Integration**: iframe embedding in WordPress posts
- **CDN**: Leverages WordPress hosting infrastructure

### Update Process
1. **Data Refresh**: Weekly Redfin data download
2. **Processing**: Python script execution
3. **Generation**: HTML file creation
4. **Deployment**: Upload to WordPress Media Library
5. **Integration**: iframe links in content

## PERFORMANCE OPTIMIZATIONS

### Frontend Performance
- **Minimal JavaScript**: Vanilla JS, no framework dependencies
- **Efficient Sorting**: In-memory array manipulation
- **Lazy Loading**: Chart images loaded on demand
- **Custom Scrollbars**: Branded scrollbar styling

### Data Optimization
- **Parquet Format**: Columnar storage for fast I/O
- **Filtered Processing**: 4-week duration data only
- **Memory Efficiency**: Pandas operations with copy() for safety
- **Null Handling**: Explicit null value management

### Scalability Considerations
- **Market Growth**: System handles 200+ metros efficiently
- **Time Series**: Multi-year data processing
- **Responsive Design**: Works across device sizes
- **Browser Compatibility**: Modern browser support

## TECHNOLOGY DEPENDENCIES

### Python Libraries
- `pandas>=1.5.0` - Data manipulation
- `numpy>=1.20.0` - Numerical computing
- `pathlib` - File system operations
- `datetime` - Date/time calculations
- `argparse` - Command line interface
- `json` - Data serialization

### Web Technologies
- **HTML5** - Modern semantic markup
- **CSS3** - Grid, Flexbox, custom properties
- **JavaScript ES6+** - Arrow functions, destructuring, modules
- **Web Fonts** - Custom Oracle typography
- **Media Queries** - Responsive design

### Infrastructure
- **Apache Parquet** - Data storage format
- **WordPress** - Content management and hosting
- **HTTPS** - Secure content delivery
- **Responsive Images** - Chart optimization

## DATA QUALITY MEASURES

### Error Handling
- **Missing Data**: Explicit null handling with "—" display
- **Outlier Filtering**: Extreme value detection and exclusion
- **Data Validation**: Unrealistic value filtering (e.g., <$50K median prices)
- **Change Limits**: >50% changes filtered as likely errors

### Consistency Checks
- **Duration Filtering**: 4-week data consistency
- **Date Validation**: Proper datetime parsing
- **Market Size Validation**: 3+ metro minimum for regional analysis
- **Statistical Robustness**: Median-based analysis to reduce outlier impact

## EXTENSIBILITY FEATURES

### Metric Addition
- **Configuration-Driven**: New metrics added via METRICS dictionary
- **Format Support**: Currency, percentage, decimal, integer formatting
- **Color Mapping**: Automatic heat map application
- **Summary Integration**: AI analysis automatically includes new metrics

### Geographic Expansion
- **Regional Mapping**: Configurable state-to-region assignments  
- **Market Classification**: Flexible percentile-based sizing
- **International Ready**: Extensible to non-US markets

### Analysis Enhancement
- **Time Period Flexibility**: Configurable lookback periods
- **Aggregation Levels**: Support for state, county, city-level data
- **Comparison Metrics**: Year-over-year, seasonal adjustments

This technical implementation provides a robust, scalable foundation for real-time housing market analysis with excellent user experience across devices and efficient data processing capabilities.