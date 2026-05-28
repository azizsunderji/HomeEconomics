#!/usr/bin/env python3
"""
One-off decoupling: split the data baked into output/ProMap.html into a separate
promap_data.js (window.PROMAP_DATA = {...}) and emit a STATIC output/promap.html
that loads it via <script src>. The map's init code is untouched because the data
script runs synchronously before the main script.

This proves the Level-2 decoupling locally. The generator
(create_sophisticated_map.py) is updated separately to emit the same split.
"""
import re, json, sys, os

SRC = "output/ProMap.html"
OUT_HTML = "output/promap.html"
OUT_DATA = "output/promap_data.js"

# Meta we know for this vintage; the generator will compute these properly.
META = {"startDate": "2025-04-30", "endDate": "2026-04-30", "zipCount": 26268}

with open(SRC, "r", encoding="utf-8") as f:
    html = f.read()

def extract_single_line(html, varname):
    """Match `const <var> = <RHS>;` on a single line; return (RHS, full_match)."""
    m = re.search(r'(?m)^const ' + re.escape(varname) + r' = (.*);\s*$', html)
    if not m:
        sys.exit(f"FAIL: could not find single-line `const {varname} = ...;`")
    return m.group(1), m.group(0)

def extract_block(html, varname):
    """Match `const <var> = { ... };` spanning multiple lines (greedy to first `\n};`)."""
    m = re.search(r'(?m)^const ' + re.escape(varname) + r' = (\{.*?\n\});', html, re.DOTALL)
    if not m:
        sys.exit(f"FAIL: could not find block `const {varname} = {{ ... }};`")
    return m.group(1), m.group(0)

zip_rhs, zip_full = extract_single_line(html, "zipData")
msa_rhs, msa_full = extract_single_line(html, "msaAverages")
gq_rhs,  gq_full  = extract_block(html, "globalQuintiles")

print(f"zipData RHS bytes:        {len(zip_rhs):,}")
print(f"msaAverages RHS bytes:    {len(msa_rhs):,}")
print(f"globalQuintiles RHS bytes:{len(gq_rhs):,}")

# --- Build promap_data.js (verbatim RHS text — no reparsing, no mangling) ---
data_js = (
    "window.PROMAP_DATA = {\n"
    f'"meta": {json.dumps(META)},\n'
    f'"zipData": {zip_rhs},\n'
    f'"msaAverages": {msa_rhs},\n'
    f'"globalQuintiles": {gq_rhs}\n'
    "};\n"
)
with open(OUT_DATA, "w", encoding="utf-8") as f:
    f.write(data_js)
print(f"\nwrote {OUT_DATA}: {os.path.getsize(OUT_DATA)/1024/1024:.2f} MB")

# --- Rewrite HTML: replace embedded data with references to PROMAP_DATA ---
html = html.replace(zip_full, "const zipData = window.PROMAP_DATA.zipData;")
html = html.replace(msa_full, "const msaAverages = window.PROMAP_DATA.msaAverages;")
html = html.replace(gq_full,  "const globalQuintiles = window.PROMAP_DATA.globalQuintiles;")

# Insert the data loader BEFORE the main inline <script> (the first bare `<script>`).
assert html.count("\n<script>\n") >= 1, "could not find bare <script> tag"
html = html.replace("\n<script>\n",
                    '\n<script src="promap_data.js"></script>\n<script>\n', 1)

# Add IDs to the date-pill spans and the ZIP-count div so we can fill them from meta.
html = html.replace('<span class="date-pill-date">Apr 2025</span>',
                    '<span class="date-pill-date" id="dpStart">Apr 2025</span>', 1)
html = html.replace('<span class="date-pill-date">Apr 2026</span>',
                    '<span class="date-pill-date" id="dpEnd">Apr 2026</span>', 1)
assert '<div class="info-line">26,268 ZIPs</div>' in html, "ZIP-count div not found"
html = html.replace('<div class="info-line">26,268 ZIPs</div>',
                    '<div class="info-line" id="zipTotal">26,268 ZIPs</div>', 1)

# Inject a small init block right after the globalQuintiles reassignment.
anchor = "const globalQuintiles = window.PROMAP_DATA.globalQuintiles;"
init_block = anchor + """
// Populate date pill + ZIP count from the fetched data meta
(function(){
    var m = (window.PROMAP_DATA && window.PROMAP_DATA.meta) || {};
    var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function fmtMon(d){ if(!d) return ''; var p=String(d).split('-'); return MON[parseInt(p[1],10)-1]+' '+p[0]; }
    var s=document.getElementById('dpStart'), e=document.getElementById('dpEnd'), z=document.getElementById('zipTotal');
    if(s) s.textContent = fmtMon(m.startDate);
    if(e) e.textContent = fmtMon(m.endDate);
    if(z && m.zipCount) z.textContent = Number(m.zipCount).toLocaleString() + ' ZIPs';
})();"""
html = html.replace(anchor, init_block, 1)

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"wrote {OUT_HTML}: {os.path.getsize(OUT_HTML)/1024/1024:.2f} MB (was 3.8 MB)")
print("\nDone. Local test: serve output/ and open promap.html (relative paths -> local files).")
