"""Pulse collectors — shared data model for all sources."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_collectors_logger = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """Same logic as analysis.anthropic_spend — env override > Dropbox > repo."""
    env = os.environ.get("PULSE_DB")
    if env:
        return Path(env)
    canonical = Path("/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db")
    if canonical.exists():
        return canonical
    return Path(__file__).parent.parent.parent / "data" / "pulse.db"


def record_collector_error(source: str, exc: BaseException,
                           context: str = "") -> None:
    """Persist a collector exception so silent swallow becomes visible.

    Without this, an `except Exception: continue` block hides upstream
    failures (e.g. Algolia /search returning 400, substack 403s, RSS feed
    bozo errors). The pipeline-health probe reads this table to surface
    "N collector errors logged in 24h" alongside the item count.

    Fail-safe: any error inside this function is swallowed itself —
    accounting must never break collection.
    """
    try:
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collector_errors (
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL,
                context TEXT DEFAULT ''
            )
        """)
        conn.execute(
            "INSERT INTO collector_errors (ts, source, error_type, message, context) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                source,
                type(exc).__name__,
                str(exc)[:500],
                str(context)[:500],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        _collectors_logger.debug(f"record_collector_error swallowed: {e}")


@dataclass
class PulseItem:
    """A single item collected from any platform."""

    source: str  # reddit, bluesky, hackernews, google_news, rss, substack, twitter, gmail
    source_id: str  # platform-specific unique ID
    url: str
    title: str
    body: str = ""
    author: str = ""
    published_at: Optional[datetime] = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Engagement metrics (platform-dependent)
    score: int = 0  # upvotes, likes, retweets, etc.
    num_comments: int = 0
    engagement_raw: dict = field(default_factory=dict)  # platform-specific extras

    # Metadata
    subreddit: str = ""  # Reddit-specific
    platform_tags: list[str] = field(default_factory=list)
    feed_name: str = ""  # RSS feed title
    feed_priority: str = ""  # "high", "normal", "journal"

    # Classification (filled by Haiku later)
    topics: list[str] = field(default_factory=list)
    relevance_score: Optional[int] = None  # 0-100
    entities: list[str] = field(default_factory=list)
    extracted_stats: list[str] = field(default_factory=list)
    sentiment: str = ""  # bullish, bearish, neutral

    @property
    def content_hash(self) -> str:
        """Dedup hash based on normalized title."""
        normalized = self.title.lower().strip()
        # Also include first 200 chars of body for near-dupe detection
        body_prefix = self.body[:200].lower().strip() if self.body else ""
        raw = f"{normalized}|{body_prefix}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Serialize for SQLite storage."""
        d = asdict(self)
        # Convert datetimes to ISO strings
        for key in ("published_at", "collected_at"):
            val = d[key]
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        # Convert lists/dicts to JSON strings
        import json
        for key in ("engagement_raw", "platform_tags", "topics", "entities", "extracted_stats"):
            d[key] = json.dumps(d[key])
        return d

    @classmethod
    def from_row(cls, row: dict) -> PulseItem:
        """Deserialize from SQLite row."""
        import json
        d = dict(row)
        # Parse datetimes
        for key in ("published_at", "collected_at"):
            val = d.get(key)
            if val and isinstance(val, str):
                d[key] = datetime.fromisoformat(val)
        # Parse JSON fields
        for key in ("engagement_raw", "platform_tags", "topics", "entities", "extracted_stats"):
            val = d.get(key)
            if val and isinstance(val, str):
                d[key] = json.loads(val)
        # Drop any extra fields from SQLite (like id, content_hash)
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**d)
