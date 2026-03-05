"""Trade journal guard — I/O bridge for journal system.

Handles persistence of structured journal entries to JSONL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from modules.journal_engine import JournalEntry
from parent.store import JSONLStore


class JournalGuard:
    """Persists trade journal entries to disk."""

    def __init__(self, data_dir: str = "data/wolf"):
        self._store = JSONLStore(path=f"{data_dir}/journal.jsonl")

    def log_entry(self, entry: JournalEntry) -> None:
        """Append a journal entry."""
        self._store.append(entry.to_dict())

    def read_entries(
        self,
        date: Optional[str] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        """Read journal entries, optionally filtered by date (YYYY-MM-DD)."""
        raw = self._store.read_all()
        entries = [JournalEntry.from_dict(r) for r in raw]

        if date:
            # Filter by close date
            entries = [
                e for e in entries
                if datetime.fromtimestamp(
                    e.close_ts / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d") == date
            ]

        # Most recent first
        entries.sort(key=lambda e: e.close_ts, reverse=True)
        return entries[:limit]

    def get_entry(self, entry_id: str) -> Optional[JournalEntry]:
        """Get a single journal entry by ID."""
        raw = self._store.read_all()
        for r in raw:
            if r.get("entry_id") == entry_id:
                return JournalEntry.from_dict(r)
        return None
