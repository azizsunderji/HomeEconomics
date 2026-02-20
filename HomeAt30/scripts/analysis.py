import sys
import json
sys.path.insert(0, "/Users/azizsunderji/Dropbox/Home Economics/Scripts")
from home_economics import query, parquet, COLORS, DATA_LAKE
from templates.d3_base import html_wrapper, COLORS as D3_COLORS

# ── Data ───────────────────────────────────────────────────────────────────────

# df = query("SELECT * FROM '$DATA/...' LIMIT 10")
# df = parquet("Redfin/monthly_metro.parquet", sql_filter="period_end >= '2020-01-01'")

# ── Chart (always D3) ─────────────────────────────────────────────────────────

# from templates.line_chart import generate_line_chart
# from templates.bump_chart import generate_bump_chart
# from templates.scatter_chart import generate_scatter_chart
#
# html = generate_line_chart(data=..., title="...", subtitle="...", source="Source: ...")
# with open("outputs/chart.html", "w") as f:
#     f.write(html)
