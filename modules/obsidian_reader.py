"""Obsidian reader — reads trading context from user's Obsidian vault.

Scans for notes tagged with trading-relevant frontmatter (market-thesis,
wolf, trading) and extracts watchlists, market theses, and risk preferences.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ObsidianContext:
    """Trading context extracted from Obsidian vault."""
    watchlist: List[str] = field(default_factory=list)
    market_theses: List[Dict[str, Any]] = field(default_factory=list)
    risk_preferences: Dict[str, Any] = field(default_factory=dict)
    raw_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "watchlist": self.watchlist,
            "market_theses": self.market_theses,
            "risk_preferences": self.risk_preferences,
            "raw_notes_count": len(self.raw_notes),
        }


class ObsidianReader:
    """Reads trading-relevant notes from an Obsidian vault."""

    # Frontmatter tags that indicate trading relevance
    TRADING_TAGS = {"trading", "market-thesis", "wolf", "watchlist", "risk"}

    def __init__(self, vault_path: str = "~/obsidian-vault"):
        self.vault_path = Path(vault_path).expanduser()

    @property
    def available(self) -> bool:
        return self.vault_path.exists()

    def read_trading_context(self) -> ObsidianContext:
        """Scan vault for trading-relevant notes and extract context."""
        if not self.available:
            return ObsidianContext()

        ctx = ObsidianContext()
        notes = self._find_trading_notes()

        for note_path, frontmatter, body in notes:
            tags = set(frontmatter.get("tags", []))
            ctx.raw_notes.append(body[:500])  # Keep first 500 chars

            if "watchlist" in tags or "watchlist" in str(note_path):
                ctx.watchlist.extend(self._parse_watchlist(body))

            if "market-thesis" in tags:
                thesis = self._parse_thesis(frontmatter, body)
                if thesis:
                    ctx.market_theses.append(thesis)

            if "risk" in tags:
                prefs = self._parse_risk_preferences(frontmatter, body)
                ctx.risk_preferences.update(prefs)

        # Deduplicate watchlist
        ctx.watchlist = list(dict.fromkeys(ctx.watchlist))
        return ctx

    def _find_trading_notes(self) -> List[tuple]:
        """Find all markdown notes with trading-relevant frontmatter."""
        results = []
        for md_file in self.vault_path.rglob("*.md"):
            # Skip hidden dirs and templates
            if any(p.startswith(".") for p in md_file.parts):
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            frontmatter = self._parse_frontmatter(content)
            if not frontmatter:
                continue

            tags = set(frontmatter.get("tags", []))
            if tags & self.TRADING_TAGS:
                body = self._strip_frontmatter(content)
                results.append((md_file, frontmatter, body))

        return results

    @staticmethod
    def _parse_frontmatter(content: str) -> Optional[Dict[str, Any]]:
        """Parse YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return None

        end = content.find("---", 3)
        if end == -1:
            return None

        fm_text = content[3:end].strip()
        # Simple YAML parsing (avoid full yaml dependency)
        result: Dict[str, Any] = {}
        for line in fm_text.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            # Handle list values like [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip("\"'") for v in value[1:-1].split(",")]
                result[key] = [i for i in items if i]
            elif value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.replace(".", "", 1).replace("-", "", 1).isdigit():
                result[key] = float(value) if "." in value else int(value)
            else:
                result[key] = value.strip("\"'")

        return result if result else None

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown."""
        if not content.startswith("---"):
            return content
        end = content.find("---", 3)
        return content[end + 3:].strip() if end != -1 else content

    @staticmethod
    def _parse_watchlist(body: str) -> List[str]:
        """Extract instrument tickers from note body."""
        # Match common perp formats: ETH-PERP, SOL-PERP, BTC-PERP, etc.
        pattern = r'\b([A-Z]{2,10}-(?:PERP|USDYP))\b'
        return re.findall(pattern, body)

    @staticmethod
    def _parse_thesis(frontmatter: Dict, body: str) -> Optional[Dict[str, Any]]:
        """Extract market thesis from frontmatter + body."""
        instrument = frontmatter.get("instrument", "")
        direction = frontmatter.get("direction", "")
        conviction = frontmatter.get("conviction", "medium")

        if not instrument or not direction:
            return None

        return {
            "instrument": instrument,
            "direction": direction,
            "conviction": conviction,
            "note": body[:200],
        }

    @staticmethod
    def _parse_risk_preferences(frontmatter: Dict, body: str) -> Dict[str, Any]:
        """Extract risk preferences from note."""
        prefs = {}
        for key in ("max_loss", "preferred_leverage", "max_slots", "daily_loss_limit"):
            if key in frontmatter:
                prefs[key] = frontmatter[key]
        return prefs
