"""Daily Anthropic API spend tracking.

Records token usage from every messages.create() / messages.stream() call
into pulse.db so we can show actual cost in the daily briefing email
(alongside Apify spend).

Usage:
    from analysis.anthropic_spend import record_usage
    resp = client.messages.create(...)
    record_usage("claude-sonnet-4-5-20250929", resp.usage)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "pulse.db"

# Pricing per million tokens, in cents (so we store as integers)
# Source: anthropic.com/pricing as of 2026-05
PRICING_CENTS_PER_MTOK = {
    "claude-sonnet-4-5-20250929": {
        "input": 300, "cache_write": 375, "cache_read": 30, "output": 1500,
    },
    "claude-sonnet-4-6": {
        # Same per-token pricing as 4.5; 4.6 has native 1M context so no
        # extended-context surcharge (4.5's >200K beta cost 2×; 4.6 is flat).
        "input": 300, "cache_write": 375, "cache_read": 30, "output": 1500,
    },
    "claude-haiku-4-5-20251001": {
        "input": 100, "cache_write": 125, "cache_read": 10, "output": 500,
    },
    "claude-opus-4-7": {
        "input": 1500, "cache_write": 1875, "cache_read": 150, "output": 7500,
    },
}


def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anthropic_spend (
            date TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            calls INTEGER DEFAULT 0,
            spent_microcents INTEGER DEFAULT 0,
            PRIMARY KEY (date, model)
        )
    """)
    conn.commit()
    return conn


def _compute_microcents(model: str, input_tok: int, output_tok: int,
                        cache_write_tok: int, cache_read_tok: int) -> int:
    """Compute cost in 1/100 cents (microcents) for precision."""
    rates = PRICING_CENTS_PER_MTOK.get(model)
    if not rates:
        # Unknown model — log and skip pricing (still record token counts)
        logger.warning(f"No pricing for model {model}; cost will not be counted")
        return 0
    # cents_per_MTok × tokens / 1M = cents. Multiply by 100 → microcents.
    return int(
        (rates["input"] * input_tok
         + rates["output"] * output_tok
         + rates["cache_write"] * cache_write_tok
         + rates["cache_read"] * cache_read_tok)
        * 100 / 1_000_000
    )


def record_usage(model: str, usage: Any) -> None:
    """Record token usage from an Anthropic API response.

    `usage` is a `Usage` object from anthropic SDK with fields:
        input_tokens, output_tokens,
        cache_creation_input_tokens (optional),
        cache_read_input_tokens (optional)
    """
    if usage is None:
        return
    try:
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cw_tok = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr_tok = getattr(usage, "cache_read_input_tokens", 0) or 0
    except Exception as e:
        logger.warning(f"Could not parse usage: {e}")
        return

    today = date.today().isoformat()
    microcents = _compute_microcents(model, in_tok, out_tok, cw_tok, cr_tok)
    try:
        conn = _get_db()
        conn.execute("""
            INSERT INTO anthropic_spend (date, model, input_tokens, output_tokens,
                cache_write_tokens, cache_read_tokens, calls, spent_microcents)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(date, model) DO UPDATE SET
                input_tokens       = input_tokens       + excluded.input_tokens,
                output_tokens      = output_tokens      + excluded.output_tokens,
                cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
                cache_read_tokens  = cache_read_tokens  + excluded.cache_read_tokens,
                calls              = calls              + 1,
                spent_microcents   = spent_microcents   + excluded.spent_microcents
        """, (today, model, in_tok, out_tok, cw_tok, cr_tok, microcents))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to record anthropic usage: {e}")


def get_spend_cents(target_date: str | None = None) -> dict:
    """Return today's (or target_date's) Anthropic spend, broken down by model.

    Returns dict like:
        {
            "total_cents": 423,
            "by_model": {
                "claude-sonnet-4-5-20250929": {"cents": 65, "calls": 1, "input": 150_000, "output": 12_000},
                "claude-haiku-4-5-20251001": {"cents": 358, "calls": 175, "input": 260_000, "output": 700_000},
            }
        }
    """
    d = target_date or date.today().isoformat()
    try:
        conn = _get_db()
        rows = conn.execute("""
            SELECT model, input_tokens, output_tokens, cache_write_tokens,
                   cache_read_tokens, calls, spent_microcents
            FROM anthropic_spend WHERE date = ?
        """, (d,)).fetchall()
        conn.close()
    except Exception:
        return {"total_cents": 0, "by_model": {}}

    by_model = {}
    total_microcents = 0
    for row in rows:
        model, in_tok, out_tok, cw_tok, cr_tok, calls, mc = row
        by_model[model] = {
            "cents": round(mc / 100, 2),
            "calls": calls,
            "input": in_tok,
            "output": out_tok,
            "cache_write": cw_tok,
            "cache_read": cr_tok,
        }
        total_microcents += mc
    return {"total_cents": round(total_microcents / 100, 2), "by_model": by_model}
