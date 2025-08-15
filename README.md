# Home Economics – Metro Charts Pipeline

Automated weekly generation of housing market charts for 927 US metro areas using Redfin data.

## 🎯 Overview

This repository automatically:
- Downloads fresh Redfin housing data every Thursday at 12:05 AM ET
- Generates 12 metric charts for each of 927 metro areas
- Uploads charts to WordPress server via SFTP
- Provides consistent URLs for Kit email integration

## 📊 Metrics Generated

For each metro area, we generate mobile-optimized charts for:
1. **Weeks of Supply** - Inventory relative to sales pace
2. **New Listings** - Weekly average new listings
3. **Active Listings** - Total homes for sale
4. **Age of Inventory** - Days on market for active listings
5. **Homes Sold** - Weekly average homes sold
6. **Pending Sales** - Homes going under contract
7. **Off Market in 2 Weeks** - Share selling within 2 weeks
8. **Median Sale Price** - Median price of homes sold
9. **Median Days on Market** - Days from listing to pending
10. **Median Days to Close** - Days from pending to close
11. **Sale to List Ratio** - Sale price vs list price
12. **% Listings with Price Drops** - Share of listings reducing price

## 🔗 URL Structure

Charts are available at predictable URLs for email templates:

```
https://yourdomain.com/wp-content/uploads/reports/YYYY-MM-DD/<city>/<city>_<metric>_mobile.png
```

Example:
```
https://yourdomain.com/wp-content/uploads/reports/2025-08-15/denver_co/denver_co_weeks_supply_mobile.png
```

## ⚙️ Setup Instructions

### 1. Fork/Clone this Repository

```bash
git clone https://github.com/azizsunderji/HomeEconomics.git
cd HomeEconomics
```

### 2. Add GitHub Secrets

Go to Settings → Secrets and variables → Actions → New repository secret

Required secrets:
- `SFTP_HOST` - Your WordPress server hostname
- `SFTP_USER` - SFTP username  
- `SFTP_KEY` - Private SSH key (full text)
- `REMOTE_BASE` - Server path (e.g., `/home/account/public_html/wp-content/uploads/reports`)

### 3. Test with Manual Run

1. Edit `cities.txt` to include only 2-3 test cities
2. Go to Actions → Weekly Metro Charts → Run workflow
3. Monitor the logs for any issues
4. Verify charts uploaded to your server

### 4. Scale to Full Production

1. Restore full `cities.txt` with all 927 metros
2. Commit and push changes
3. Charts will generate automatically every Thursday at 12:05 AM ET

## 🏃 Local Development

### Setup

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Download Data

```bash
python scripts/download_data.py --output data
```

### Generate Charts for Test City

```bash
python scripts/make_charts.py \
  --cities test_cities.txt \
  --out out/reports \
  --date 2025-08-15
```

### Generate Single City

```bash
python scripts/generate_charts.py \
  --city denver_co \
  --date 2025-08-15 \
  --out out/reports
```

## 🔧 Configuration

### Adjust Schedule

Edit `.github/workflows/weekly-charts.yml`:
```yaml
schedule:
  - cron: "5 4 * * 4"  # Thursday 12:05 AM ET (EDT)
  - cron: "5 5 * * 4"  # Thursday 12:05 AM ET (EST)
```

### Change Number of Parallel Workers

Edit the `matrix.shard` array in the workflow:
```yaml
matrix:
  shard: [0, 1, 2, 3, 4, 5, 6, 7]  # 8 workers
  # Increase to [0, 1, 2, ..., 15] for 16 workers
```

### Add/Remove Cities

Edit `cities.txt` - one city slug per line:
```
denver_co
albany_ga
new_york_ny
```

## 📁 File Structure

```
.
├── cities.txt                    # List of metro area slugs
├── requirements.txt               # Python dependencies
├── scripts/
│   ├── download_data.py         # Fetch Redfin data
│   ├── exact_metro_chart_generator.py  # Chart styling engine
│   ├── generate_charts.py       # Chart generation wrapper
│   ├── chart_adapter.py         # GitHub Actions adapter
│   └── make_charts.py           # Main orchestrator
├── .github/
│   └── workflows/
│       └── weekly-charts.yml    # GitHub Actions workflow
└── out/
    └── reports/
        └── YYYY-MM-DD/
            └── <city>/
                └── <city>_<metric>_mobile.png
```

## 🚨 Troubleshooting

### Charts Not Appearing in Emails

1. Check URL in browser (incognito mode)
2. Verify WordPress hotlink protection is disabled for uploads
3. Check file permissions (should be 644)

### SFTP Upload Fails

1. Verify SSH key format (starts with `-----BEGIN RSA PRIVATE KEY-----`)
2. Test SFTP connection manually:
   ```bash
   sftp -i ~/.ssh/test_key user@host
   ```
3. Check `REMOTE_BASE` path exists on server

### Generation Takes Too Long

1. Increase number of shards in workflow (up to 16)
2. Check GitHub Actions usage limits
3. Consider processing priority metros only

### Missing Charts

1. Check workflow logs for specific city errors
2. Verify city slug format in `cities.txt`
3. Check if metro has data in Redfin dataset

## 📊 Performance

- **Total metros**: 927
- **Charts per metro**: 12
- **Total charts**: 11,124
- **Processing time**: ~10-12 hours with 8 parallel workers
- **Storage required**: ~4GB per week

## 🔄 Data Updates

Redfin updates their data every Wednesday. Our pipeline runs Thursday at 12:05 AM ET to ensure fresh data is available.

## 📝 License

Private repository - not for public distribution.

## 🤝 Support

For issues or questions:
1. Check GitHub Actions logs
2. Review troubleshooting section
3. Contact repository owner

---

Generated with care for Home Economics by Aziz Sunderji