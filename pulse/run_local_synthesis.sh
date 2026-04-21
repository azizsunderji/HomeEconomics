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

# Ensure all collected items are classified (GHA classification may have failed)
echo "--- Classifying unclassified items ---"
cd "$SCRIPTS_DIR"
$PYTHON run_pipeline.py classify-only

# Enrich article text using playwright + Chrome cookies
echo "--- Enriching articles ---"
cd "$PULSE_DIR"
$PYTHON enrich_articles.py --hours 24 --limit 150

# Run synthesis + email
echo "--- Running synthesis ---"
cd "$SCRIPTS_DIR"
$PYTHON run_pipeline.py synthesize

# Push enriched pulse.db back to Dropbox-synced folder (Dropbox desktop syncs to cloud)
echo "--- Copying pulse.db back to Dropbox-synced folder ---"
cp "$DATA_DIR/pulse.db" "$DROPBOX_DB"

echo "=== $(date) LOCAL SYNTHESIS DONE ==="
