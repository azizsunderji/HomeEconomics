"""Institutional signal triage — rate Gmail items as institutional or personal."""

from __future__ import annotations
import json
import sqlite3
import re
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).parent.parent / "data" / "pulse.db"
TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Institutional Signal Triage</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F6F7F3;color:#3D3733;line-height:1.5;padding:24px}
.header{max-width:900px;margin:0 auto 24px;border-bottom:3px solid #3D3733;padding-bottom:12px;display:flex;justify-content:space-between;align-items:baseline}
.header h1{font-size:22px;letter-spacing:-0.5px}
.stats{font-size:13px;color:#888}
.stats span{font-weight:600}
.card{max-width:900px;margin:0 auto 8px;display:flex;align-items:flex-start;gap:12px;padding:10px 12px;background:#fff;border-radius:6px;border:1px solid #e8e8e8;transition:opacity 0.3s}
.card.voted{opacity:0.3}
.card .buttons{display:flex;gap:6px;flex-shrink:0;padding-top:2px}
.card .buttons button{width:32px;height:32px;border-radius:6px;border:1px solid #ddd;background:#fff;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;transition:all 0.15s}
.card .buttons button:hover{transform:scale(1.1)}
.card .buttons .up:hover,.card .buttons .up.sel{background:#C6DCCB;border-color:#67A275}
.card .buttons .down:hover,.card .buttons .down.sel{background:#FBCAB5;border-color:#F4743B}
.card .content{flex:1;min-width:0}
.card .sender{font-size:12px;font-weight:600;color:#0BB4FF}
.card .title{font-size:14px;margin-top:2px}
.card .title a{color:#3D3733;text-decoration:none}
.card .title a:hover{color:#0BB4FF}
.card .meta{font-size:11px;color:#aaa;margin-top:2px}
</style></head><body>
<div class="header">
<h1>Institutional Signal Triage</h1>
<div class="stats"><span id="up-count">0</span> institutional &middot; <span id="down-count">0</span> personal &middot; <span id="remaining">0</span> remaining</div>
</div>
<div id="content"></div>
<script>
let items=[];
async function load(){
  items=await(await fetch('/api/items')).json();
  render();updateStats();
}
function render(){
  let h='';
  items.forEach(i=>{
    const voted=i.preference!==null&&i.preference!==undefined;
    const upS=i.preference===1?'sel':'';
    const dnS=i.preference===-1?'sel':'';
    h+=`<div class="card ${voted?'voted':''}" id="c-${i.id}">
      <div class="buttons">
        <button class="up ${upS}" onclick="vote(${i.id},1)">\\u{1F44D}</button>
        <button class="down ${dnS}" onclick="vote(${i.id},-1)">\\u{1F44E}</button>
      </div>
      <div class="content">
        <div class="sender">${esc(i.sender)}</div>
        <div class="title">${esc(i.title)}</div>
        <div class="meta">${esc(i.date)} &middot; relevance: ${i.relevance??'?'}</div>
      </div></div>`;
  });
  document.getElementById('content').innerHTML=h;
}
async function vote(id,pref){
  const i=items.find(x=>x.id===id);if(i)i.preference=pref;
  render();updateStats();
  await fetch('/api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,preference:pref,sender:i?i.sender:''})});
}
function updateStats(){
  const up=items.filter(i=>i.preference===1).length;
  const dn=items.filter(i=>i.preference===-1).length;
  document.getElementById('up-count').textContent=up;
  document.getElementById('down-count').textContent=dn;
  document.getElementById('remaining').textContent=items.length-up-dn;
}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
load();
</script></body></html>"""


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS institutional_preferences (
        id INTEGER PRIMARY KEY,
        sender TEXT DEFAULT '',
        preference INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def get_items():
    conn = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = conn.execute(
        """SELECT i.id, i.author, i.title, i.collected_at, i.relevance_score,
                  ip.preference
           FROM items i
           LEFT JOIN institutional_preferences ip ON i.id = ip.id
           WHERE i.source = 'gmail' AND i.collected_at >= ?
           ORDER BY COALESCE(ip.preference, 0) = 0 DESC, i.collected_at DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    items = []
    for r in rows:
        raw = r["author"] or ""
        m = re.match(r'"?([^"<]+)"?\s*<', raw)
        sender = m.group(1).strip() if m else raw.split("<")[0].strip() or raw
        items.append({
            "id": r["id"],
            "sender": sender,
            "title": r["title"] or "",
            "date": (r["collected_at"] or "")[:10],
            "relevance": r["relevance_score"],
            "preference": r["preference"],
        })
    return items


def save_vote(item_id, preference, sender=""):
    conn = get_db()
    conn.execute(
        """INSERT INTO institutional_preferences (id, sender, preference, created_at)
           VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET preference=excluded.preference, created_at=excluded.created_at""",
        (item_id, sender, preference, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(TEMPLATE.encode())
        elif self.path == "/api/items":
            body = json.dumps(get_items(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/vote":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            save_vote(data["id"], data["preference"], data.get("sender", ""))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = 8765
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Institutional triage: http://localhost:{port}")
    print("Thumbs up = institutional signal. Thumbs down = personal/junk.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
