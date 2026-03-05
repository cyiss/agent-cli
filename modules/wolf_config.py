"""WOLF strategy configuration — budget, slots, risk, and presets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WolfConfig:
    """Configuration for WOLF autonomous trading strategy."""

    # Budget & Position Management
    total_budget: float = 10_000.0
    max_slots: int = 3
    leverage: float = 10.0
    margin_per_slot: float = 0.0  # auto-computed

    # Entry thresholds
    scanner_score_threshold: int = 170
    movers_immediate_auto_entry: bool = True
    movers_confidence_threshold: float = 70.0

    # Exit parameters
    conviction_collapse_minutes: int = 30
    stagnation_minutes: int = 60
    stagnation_min_roe: float = 3.0
    max_negative_roe: float = -5.0

    # Risk
    daily_loss_limit: float = 500.0
    max_same_direction: int = 2

    # DSL preset for position guards
    dsl_preset: str = "tight"
    dsl_leverage_override: Optional[float] = None

    # Tick schedule
    tick_interval_s: float = 60.0
    scanner_interval_ticks: int = 15
    watchdog_interval_ticks: int = 5

    # HOWL self-improvement
    howl_interval_ticks: int = 240        # Run HOWL every 4 hours (at 60s ticks)
    howl_min_round_trips: int = 5         # Min trades before applying adjustments
    howl_auto_adjust: bool = True         # Auto-adjust params from HOWL findings

    # Scheduled tasks (UTC hours)
    daily_reset_hour: int = 0             # UTC hour for daily PnL reset
    howl_report_hour: int = 4             # UTC hour for comprehensive HOWL report

    # Nightly review
    nightly_review_hour: int = 2          # UTC hour for nightly review
    nightly_review_enabled: bool = True   # Enable/disable nightly review

    # Obsidian integration
    obsidian_vault_path: str = ""         # Path to Obsidian vault (empty = disabled)
    obsidian_scan_interval_ticks: int = 60  # Re-scan vault every hour

    # Instrument filters
    excluded_instruments: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.margin_per_slot == 0.0:
            self.margin_per_slot = self.total_budget / max(self.max_slots, 1)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WolfConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_yaml(cls, path: str) -> "WolfConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data.get("wolf", data))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


WOLF_PRESETS: Dict[str, WolfConfig] = {
    "default": WolfConfig(),
    "conservative": WolfConfig(
        max_slots=2,
        leverage=5.0,
        scanner_score_threshold=190,
        movers_confidence_threshold=80.0,
        daily_loss_limit=250.0,
    ),
    "aggressive": WolfConfig(
        max_slots=3,
        leverage=15.0,
        scanner_score_threshold=150,
        movers_confidence_threshold=60.0,
        daily_loss_limit=1000.0,
    ),
}
