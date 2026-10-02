# Metro Explorer — data pipeline

The Metro Explorer is live at https://homeeconomics.us/metro-explorer (moved from the old
WordPress host on 2 Oct 2026).

- **The page** now lives in the `homeeconomics/portal` repo at `public/metro-explorer-app/index.html`.
  Edit it there. The `index.html` in this folder is the pre-move version, kept for history only.
- **The data** is still built here. `scripts/refresh_data.py` regenerates `data/*.json` from Redfin and
  Zillow; `scripts/upload_to_blob.mjs` publishes the files to Vercel Blob, where the page reads them.
  The `Refresh Metro Explorer Data` workflow does both every Tuesday.

## Redfin source change (June 2026)

Redfin stopped updating `redfin_market_tracker/*.tsv000.gz` on 2 Jun 2026 (last month: May 2026).
Current data is in `redfin_data_center/housing_market/monthly/all_metros.csv`, which is seasonally
adjusted for every metro, covers All Residential only, and has no price-drops column. Its values
differ from the old file's, so the two are not spliced:

- All Residential: whole history from the new file.
- Share of listings with price drops: old file only, ends May 2026.
- Single-family and condo series: old file only, end May 2026. Zillow prices for them continue.
