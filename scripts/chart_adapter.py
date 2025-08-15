# scripts/chart_adapter.py
from __future__ import annotations

import importlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# Add scripts directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

METRICS: List[str] = [
    "active_listings",
    "age_of_inventory",
    "homes_sold",
    "median_days_on_market",
    "median_days_to_close",
    "median_sale_price",
    "new_listings",
    "off_market_in_2_weeks",
    "pct_listings_w__price_drops",  # double underscore after 'w' by design
    "pending_sales",
    "sale_to_list_ratio",
    "weeks_supply",
]

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

# ---------- MODULE MODE ----------

def _module_call(module_name: str, func_name: str, *args, **kwargs) -> None:
    mod = importlib.import_module(module_name)
    fn = getattr(mod, func_name, None)
    if fn is None:
        raise RuntimeError(f"Function '{func_name}' not found in module '{module_name}'.")
    fn(*args, **kwargs)

def _try_module_city(city: str, date: str, out_dir: Path) -> bool:
    module = os.getenv("CHARTS_PY_MODULE", "scripts.generate_charts")  # Default to our module
    if not module:
        return False
    func = os.getenv("CHARTS_PY_FUNC_CITY", "render_city")
    _ensure_dir(out_dir)
    _module_call(module, func, city, date, str(out_dir), METRICS)
    return True

def _try_module_national(date: str, out_dir: Path) -> bool:
    module = os.getenv("CHARTS_PY_MODULE", "scripts.generate_charts")
    if not module:
        return False
    func = os.getenv("CHARTS_PY_FUNC_NATL", "render_national")
    _ensure_dir(out_dir)
    # function may not exist; if not, signal False so CLI path can be used
    try:
        _module_call(module, func, date, str(out_dir), METRICS)
    except RuntimeError:
        return False
    return True

# ---------- CLI MODE ----------

def _format_cmd(template: str, **kw) -> list[str]:
    # replace {city} {date} {out} tokens
    cmd = template.format(**kw)
    return shlex.split(cmd)

def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout}")

def _try_cli_city(city: str, date: str, out_dir: Path) -> bool:
    tmpl = os.getenv("CHARTS_CLI_CITY")
    if not tmpl:
        return False
    _ensure_dir(out_dir)
    cmd = _format_cmd(tmpl, city=city, date=date, out=str(out_dir))
    _run(cmd)
    return True

def _try_cli_national(date: str, out_dir: Path) -> bool:
    tmpl = os.getenv("CHARTS_CLI_NATL")
    if not tmpl:
        return False
    _ensure_dir(out_dir)
    cmd = _format_cmd(tmpl, date=date, out=str(out_dir))
    _run(cmd)
    return True

# ---------- PUBLIC API ----------

def render_city(city: str, date: str, out_root: Path) -> None:
    """
    Must produce files:
      out_root/YYYY-MM-DD/<city>/<city>_<metric>_mobile.png  for all METRICS
    """
    city_dir = out_root / date / city
    if _try_module_city(city, date, city_dir):
        return
    if _try_cli_city(city, date, city_dir):
        return
    # Default to our built-in module
    from scripts.generate_charts import render_city as builtin_render
    _ensure_dir(city_dir)
    builtin_render(city, date, str(city_dir), METRICS)

def render_national(date: str, out_root: Path) -> None:
    """
    Must produce files:
      out_root/YYYY-MM-DD/national/national_<metric>_mobile.png for all METRICS
    """
    nat_dir = out_root / date / "national"
    # Try module, then CLI; OK if neither is configured (national optional)
    if _try_module_national(date, nat_dir):
        return
    if _try_cli_national(date, nat_dir):
        return
    # If neither configured, do nothing (not an error).
    return