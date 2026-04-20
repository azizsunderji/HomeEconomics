#!/bin/bash
# Local synthesis runner: pull DB → enrich articles → synthesize + email → push DB
# Runs after GHA collect-only job has finished and synced pulse.db to Dropbox.
set -euo pipefail

source ~/.zprofile
LOG="/tmp/pulse_local_synthesis.log"
PYTHON="/Applications/anaconda3/bin/python3"
PULSE_DIR="/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/pulse"
SCRIPTS_DIR="$PULSE_DIR/scripts"
DATA_DIR="$PULSE_DIR/data"

exec >> "$LOG" 2>&1
echo ""
echo "=== $(date) LOCAL SYNTHESIS START ==="

# Pull latest pulse.db from Dropbox (GHA collect job pushes it there)
echo "--- Pulling pulse.db from Dropbox ---"
mkdir -p "$DATA_DIR"
rclone copy "dropbox:Home Economics/Data/Pulse/pulse.db" "$DATA_DIR/" \
    --verbose --stats-one-line || echo "WARNING: rclone pull failed — using existing DB"

# Enrich article text using playwright + Chrome cookies
echo "--- Enriching articles ---"
cd "$PULSE_DIR"
$PYTHON enrich_articles.py --hours 36 --limit 60

# Run synthesis + email
echo "--- Running synthesis ---"
cd "$SCRIPTS_DIR"
$PYTHON run_pipeline.py synthesize

# Push enriched pulse.db back to Dropbox
echo "--- Pushing pulse.db to Dropbox ---"
rclone copy "$DATA_DIR/" "dropbox:Home Economics/Data/Pulse/" \
    --include "pulse.db" --include "*.json" \
    --verbose --stats-one-line

echo "=== $(date) LOCAL SYNTHESIS DONE ==="
