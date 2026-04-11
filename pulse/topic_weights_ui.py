#!/usr/bin/env python3
"""Local web UI for editing Pulse topic weights.

Run: python topic_weights_ui.py
Then open http://localhost:8787 in your browser.

Reads/writes pulse/data/topic_weights.json. After saving, push the file
via the GitHub API to deploy to Pulse.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEIGHTS_FILE = Path(__file__).parent / "data" / "topic_weights.json"
PORT = 8787


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pulse Topic Weights</title>
<style>
  :root {
    --blue: #0BB4FF;
    --yellow: #FEC439;
    --green: #67A275;
    --red: #F4743B;
    --cream: #DADFCE;
    --bg: #F6F7F3;
    --black: #3D3733;
    --light-cream: #fafbf6;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--black);
    margin: 0;
    padding: 24px;
    max-width: 900px;
    margin: 0 auto;
  }
  h1 {
    font-size: 28px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .subtitle {
    color: #888;
    margin-bottom: 32px;
    font-size: 14px;
  }
  .tier {
    margin-bottom: 28px;
  }
  .tier-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 12px;
    border-bottom: 2px solid var(--black);
    padding-bottom: 6px;
  }
  .tier-label {
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-weight: 600;
  }
  .tier-desc {
    font-size: 13px;
    color: #888;
  }
  .topic {
    background: white;
    padding: 14px 16px;
    margin-bottom: 8px;
    border-radius: 6px;
    border: 1px solid #e8e8e8;
  }
  .topic-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 4px;
  }
  .topic-label {
    font-weight: 600;
    font-size: 16px;
  }
  .topic-weight {
    font-weight: 600;
    font-size: 18px;
    color: var(--blue);
    font-variant-numeric: tabular-nums;
    min-width: 40px;
    text-align: right;
  }
  .topic-desc {
    font-size: 13px;
    color: #666;
    margin-bottom: 10px;
    line-height: 1.4;
  }
  input[type=range] {
    width: 100%;
    -webkit-appearance: none;
    height: 6px;
    background: var(--cream);
    border-radius: 3px;
    outline: none;
  }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 22px;
    height: 22px;
    background: var(--blue);
    border-radius: 50%;
    cursor: pointer;
    border: 2px solid white;
    box-shadow: 0 1px 4px rgba(0,0,0,0.2);
  }
  .actions {
    position: sticky;
    bottom: 0;
    background: var(--bg);
    padding: 16px 0;
    margin-top: 32px;
    border-top: 2px solid var(--black);
    display: flex;
    gap: 12px;
    align-items: center;
  }
  button {
    background: var(--blue);
    color: white;
    border: none;
    padding: 12px 24px;
    font-size: 15px;
    font-weight: 600;
    border-radius: 6px;
    cursor: pointer;
  }
  button:hover { opacity: 0.9; }
  button.secondary {
    background: var(--cream);
    color: var(--black);
  }
  button.success { background: var(--green); }
  #status { font-size: 14px; color: #666; }
  .keywords {
    font-size: 11px;
    color: #999;
    margin-top: 8px;
    font-family: 'SF Mono', Menlo, monospace;
  }
  .examples {
    margin-top: 8px;
    padding: 8px 10px;
    background: var(--light-cream);
    border-left: 3px solid var(--cream);
    font-size: 12px;
    color: #555;
    line-height: 1.5;
  }
  .examples-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #999;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .example-item {
    margin-bottom: 2px;
    font-style: italic;
  }
  .example-item:before {
    content: "— ";
    color: #aaa;
    font-style: normal;
  }
</style>
</head>
<body>
  <h1>Pulse Topic Weights</h1>
  <div class="subtitle">Drag sliders to dial topics up (more prominent in Pulse) or down (less). 100 = max priority. 0 = effectively excluded.</div>

  <div id="topics"></div>

  <div class="actions">
    <button onclick="save()">Save</button>
    <button class="secondary" onclick="resetDefaults()">Reset to Defaults</button>
    <span id="status"></span>
  </div>

<script>
let DATA = null;

async function load() {
  const r = await fetch('/api/load');
  DATA = await r.json();
  render();
}

function render() {
  const container = document.getElementById('topics');
  container.innerHTML = '';

  // Group topics by tier in tier order
  const tierOrder = ['core', 'adjacent', 'occasional', 'excluded'];
  for (const tierKey of tierOrder) {
    const tier = DATA.tiers[tierKey];
    if (!tier) continue;

    const tierEl = document.createElement('div');
    tierEl.className = 'tier';
    tierEl.innerHTML = `
      <div class="tier-header">
        <div class="tier-label">${tier.label}</div>
        <div class="tier-desc">${tier.description}</div>
      </div>
    `;

    for (const [key, topic] of Object.entries(DATA.topics)) {
      if (topic.tier !== tierKey) continue;

      const topicEl = document.createElement('div');
      topicEl.className = 'topic';
      const examples = (topic.examples || []).map(e => `<div class="example-item">${escapeHtml(e)}</div>`).join('');
      const examplesBlock = examples ? `<div class="examples"><div class="examples-label">Example items</div>${examples}</div>` : '';
      topicEl.innerHTML = `
        <div class="topic-header">
          <div class="topic-label">${topic.label}</div>
          <div class="topic-weight" id="w-${key}">${topic.weight}</div>
        </div>
        <div class="topic-desc">${topic.description}</div>
        <input type="range" min="0" max="100" value="${topic.weight}"
               oninput="setWeight('${key}', this.value)" />
        ${examplesBlock}
        <div class="keywords">${(topic.keywords || []).slice(0, 8).join(' · ')}</div>
      `;
      tierEl.appendChild(topicEl);
    }

    container.appendChild(tierEl);
  }
}

function setWeight(key, val) {
  DATA.topics[key].weight = parseInt(val);
  document.getElementById('w-' + key).textContent = val;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function save() {
  const r = await fetch('/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(DATA),
  });
  const result = await r.json();
  const status = document.getElementById('status');
  if (result.ok) {
    status.textContent = 'Saved at ' + new Date().toLocaleTimeString();
    status.style.color = '#67A275';
  } else {
    status.textContent = 'Error: ' + (result.error || 'unknown');
    status.style.color = '#F4743B';
  }
}

function resetDefaults() {
  if (!confirm('Reset all topic weights to their tier defaults?')) return;
  for (const [key, topic] of Object.entries(DATA.topics)) {
    const tierDefault = DATA.tiers[topic.tier]?.default_weight ?? 50;
    topic.weight = tierDefault;
  }
  render();
}

load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif path == "/api/load":
            try:
                with open(WEIGHTS_FILE) as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data.encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path == "/api/save":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                with open(WEIGHTS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                print(f"Saved {WEIGHTS_FILE}")
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # silence noisy default logging


def main():
    if not WEIGHTS_FILE.exists():
        print(f"Error: {WEIGHTS_FILE} does not exist")
        sys.exit(1)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Pulse topic weights UI: http://localhost:{PORT}")
    print(f"Editing: {WEIGHTS_FILE}")
    print("Press Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
