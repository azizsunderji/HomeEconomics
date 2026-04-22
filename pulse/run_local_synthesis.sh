#!/bin/bash
# Local synthesis runner: pull DB → classify → enrich → synthesize + email → push DB
# Runs after GHA collect-only job has finished and synced pulse.db to Dropbox.
set -euo pipefail

source ~/.zprofile
LOG="/tmp/pulse_local_synthesis.log"
PYTHON="/Applications/anaconda3/bin/python3"
PULSE_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/pulse"
SCRIPTS_DIR="$PULSE_DIR/scripts"
DATA_DIR="$PULSE_DIR/data"
DROPBOX_DB="/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"

exec >> "$LOG" 2>&1
echo ""
echo "=== $(date) LOCAL SYNTHESIS START ==="

# Pull latest pulse.db from the Dropbox-synced local folder (GHA pushes there)
echo "--- Copying pulse.db from Dropbox-synced folder ---"
mkdir -p "$DATA_DIR"
cp "$DROPBOX_DB" "$DATA_DIR/pulse.db" && echo "Pulled $(stat -f%z "$DATA_DIR/pulse.db") bytes"

# Quick Gmail re-collect: late-arriving newsletters (Torsten Slok, Capital
# Economics, etc. often hit inbox between GHA collect at 5:30am and synthesis
# at 7am). Hit Gmail one more time to catch them before synthesis.
echo "--- Gmail re-collect (catch late-arriving newsletters) ---"
cd "$SCRIPTS_DIR"
$PYTHON -c "
import sys
sys.path.insert(0, '.')
from store import get_db, bulk_upsert, log_collection_start, log_collection_end
from collectors import gmail
conn = get_db()
run_id = log_collection_start(conn, 'gmail')
try:
    items = gmail.collect()
    new, dupe = bulk_upsert(conn, items)
    log_collection_end(conn, run_id, len(items), new, dupe)
    print(f'Gmail re-collect: {new} new, {dupe} dupes (of {len(items)})')
except Exception as e:
    log_collection_end(conn, run_id, 0, 0, 0, error=str(e))
    print(f'Gmail re-collect failed: {e}')
"

# Ensure all collected items are classified (GHA classification may have failed)
echo "--- Classifying unclassified items ---"
cd "$SCRIPTS_DIR"
$PYTHON run_pipeline.py classify-only

# Enrich article text using playwright + Chrome cookies
echo "--- Enriching articles ---"
cd "$PULSE_DIR"
$PYTHON enrich_articles.py --hours 24 --limit 150

# Fetch abstracts for today's 5 rotated journal papers (non-NBER journals
# don't ship abstracts in RSS so we fetch their pages directly)
echo "--- Fetching journal abstracts ---"
$PYTHON fetch_journal_abstracts.py

# Run synthesis + email
echo "--- Running synthesis ---"
cd "$SCRIPTS_DIR"
$PYTHON run_pipeline.py synthesize

# Push enriched pulse.db back to Dropbox-synced folder (Dropbox desktop syncs to cloud)
echo "--- Copying pulse.db back to Dropbox-synced folder ---"
cp "$DATA_DIR/pulse.db" "$DROPBOX_DB"

echo "=== $(date) LOCAL SYNTHESIS DONE ==="
