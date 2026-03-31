"""StrategyGuard — bridge between BaseStrategy instances and the APEX engine.

Wraps any BaseStrategy into the Guard pattern so APEX can consume its signals
alongside Pulse and Radar. Constructs MarketSnapshot from APEX market data,
runs on_tick(), and converts StrategyDecision outputs into signal dicts.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision, asset_to_instrument
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

log = logging.getLogger("strategy_guard")


class StrategyGuard:
    """Owns one or more BaseStrategy instances and bridges them to APEX."""

    def __init__(
        self,
        strategy_names: Optional[List[str]] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.strategies: List[BaseStrategy] = []

        for name in (strategy_names or []):
            strat = self._load_strategy(name)
            if strat:
                self.strategies.append(strat)

    @staticmethod
    def _load_strategy(name: str) -> Optional[BaseStrategy]:
        """Load a strategy by registry name or module:class path."""
        try:
            from cli.strategy_registry import resolve_strategy_path
            path = resolve_strategy_path(name)
            module_path, class_name = path.rsplit(":", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls()
        except Exception as e:
            log.error("Failed to load strategy '%s': %s", name, e)
            return None

    def scan(
        self,
        all_markets: list,
        slot_prices: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """Run all strategies against current market data.

        Constructs a MarketSnapshot for each asset from HL market data,
        runs each strategy's on_tick(), and collects entry signals.

        Returns list of signal dicts compatible with ApexEngine._evaluate_entries:
            {"asset": str, "direction": str, "confidence": float, "source": str}
        """
        if not self.enabled or not self.strategies:
            return []

        # Build per-asset snapshots from all_markets
        snapshots = self._build_snapshots(all_markets)
        if not snapshots:
            return []

        signals: List[Dict[str, Any]] = []

        for strat in self.strategies:
            for coin, snap in snapshots.items():
                try:
                    # No position context — APEX manages positions at slot level
                    decisions = strat.on_tick(snap, StrategyContext())
                except Exception as e:
                    log.debug("Strategy %s failed on %s: %s", strat.strategy_id, coin, e)
                    continue

                for dec in decisions:
                    if dec.action != "place_order" or not dec.side:
                        continue

                    direction = "long" if dec.side == "buy" else "short"
                    confidence = dec.meta.get("confidence", 75.0)

                    signals.append({
                        "asset": coin,
                        "direction": direction,
                        "confidence": confidence,
                        "source": f"strategy:{strat.strategy_id}",
                        "signal": dec.meta.get("signal", strat.strategy_id),
                        "meta": dec.meta,
                    })

        if signals:
            log.info(
                "Strategy guard: %d strategies × %d assets → %d signals",
                len(self.strategies), len(snapshots), len(signals),
            )
            for sig in signals[:5]:
                log.info(
                    "  %s %s %s (conf=%.0f, via %s)",
                    sig["signal"], sig["direction"], sig["asset"],
                    sig["confidence"], sig["source"],
                )

        return signals

    @staticmethod
    def _build_snapshots(all_markets: list) -> Dict[str, MarketSnapshot]:
        """Build MarketSnapshot for each tradeable asset from HL market data."""
        snapshots: Dict[str, MarketSnapshot] = {}

        if len(all_markets) < 2:
            return snapshots

        universe = all_markets[0].get("universe", [])
        ctxs = all_markets[1]

        for i, ctx in enumerate(ctxs):
            if i >= len(universe):
                break

            try:
                name = universe[i].get("name", "")
            except (IndexError, AttributeError):
                continue

            if not name:
                continue

            try:
                mid = float(ctx.get("midPx", 0) or 0)
                if mid <= 0:
                    continue

                # HL provides markPx, midPx; estimate bid/ask from mark
                mark = float(ctx.get("markPx", mid) or mid)
                # Use a small spread estimate (2 bps)
                half_spread = mid * 0.0001
                bid = mid - half_spread
                ask = mid + half_spread
                spread_bps = (ask - bid) / mid * 10_000 if mid > 0 else 0

                vol_24h = float(ctx.get("dayNtlVlm", 0) or 0)
                funding = float(ctx.get("funding", 0) or 0)
                oi = float(ctx.get("openInterest", 0) or 0)

                import time
                snapshots[name] = MarketSnapshot(
                    instrument=asset_to_instrument(name),
                    mid_price=mid,
                    bid=bid,
                    ask=ask,
                    spread_bps=spread_bps,
                    timestamp_ms=int(time.time() * 1000),
                    volume_24h=vol_24h,
                    funding_rate=funding,
                    open_interest=oi,
                )
            except (ValueError, TypeError):
                continue

        return snapshots
