"""Clearinghouse reconciliation — bidirectional position sync.

Pure engine: takes APEX slots + exchange positions, returns discrepancies.
No I/O — all data passed in, results returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Discrepancy:
    """A mismatch between internal state and exchange state."""
    type: str           # orphan_exchange, orphan_slot, size_mismatch
    severity: str       # critical, warning
    instrument: str
    slot_id: Optional[int]
    exchange_size: float
    internal_size: float
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "instrument": self.instrument,
            "slot_id": self.slot_id,
            "exchange_size": self.exchange_size,
            "internal_size": self.internal_size,
            "detail": self.detail,
        }


class ReconciliationEngine:
    """Bidirectional reconciliation between APEX slots and exchange positions."""

    def reconcile(
        self,
        slots: List[Dict[str, Any]],
        exchange_positions: List[Dict[str, Any]],
    ) -> List[Discrepancy]:
        """Compare internal slots against exchange positions.

        Args:
            slots: List of slot dicts with keys: slot_id, status, instrument,
                   entry_size, direction
            exchange_positions: List of HL assetPositions entries, each with
                   nested "position" dict containing "coin" and "szi"

        Returns:
            Sorted list of Discrepancy (critical first).
        """
        discrepancies: List[Discrepancy] = []

        # Build maps
        # instrument -> (slot_id, size, direction)
        slot_map: Dict[str, Dict[str, Any]] = {}
        for s in slots:
            if s.get("status") == "active" and s.get("instrument"):
                slot_map[s["instrument"]] = {
                    "slot_id": s.get("slot_id"),
                    "size": abs(float(s.get("entry_size", 0))),
                    "direction": s.get("direction", ""),
                }

        # coin -> (szi, exchange_instrument)
        exchange_map: Dict[str, Dict[str, Any]] = {}
        for pos in exchange_positions:
            p = pos.get("position", pos)  # handle nested or flat
            szi = float(p.get("szi", "0"))
            if szi == 0:
                continue
            coin = p.get("coin", "")
            if not coin:
                continue
            instrument = f"{coin}-PERP"
            exchange_map[instrument] = {
                "size": abs(szi),
                "szi": szi,
                "direction": "long" if szi > 0 else "short",
            }

        # Check 1: Each active slot has a matching exchange position
        for instrument, slot_info in slot_map.items():
            if instrument not in exchange_map:
                discrepancies.append(Discrepancy(
                    type="orphan_slot",
                    severity="warning",
                    instrument=instrument,
                    slot_id=slot_info["slot_id"],
                    exchange_size=0.0,
                    internal_size=slot_info["size"],
                    detail=f"Slot {slot_info['slot_id']} tracks {instrument} "
                           f"but exchange has no position",
                ))
            else:
                # Check size mismatch
                ex = exchange_map[instrument]
                size_delta = abs(ex["size"] - slot_info["size"])
                if slot_info["size"] > 0:
                    pct_diff = (size_delta / slot_info["size"]) * 100
                else:
                    pct_diff = 100.0 if ex["size"] > 0 else 0.0

                if pct_diff > 1.0:  # >1% mismatch
                    severity = "critical" if pct_diff > 10.0 else "warning"
                    discrepancies.append(Discrepancy(
                        type="size_mismatch",
                        severity=severity,
                        instrument=instrument,
                        slot_id=slot_info["slot_id"],
                        exchange_size=ex["size"],
                        internal_size=slot_info["size"],
                        detail=f"Slot {slot_info['slot_id']} {instrument}: "
                               f"internal={slot_info['size']:.4f} vs "
                               f"exchange={ex['size']:.4f} ({pct_diff:.1f}% diff)",
                    ))

        # Check 2: Each exchange position has a matching slot
        for instrument, ex_info in exchange_map.items():
            if instrument not in slot_map:
                discrepancies.append(Discrepancy(
                    type="orphan_exchange",
                    severity="critical",
                    instrument=instrument,
                    slot_id=None,
                    exchange_size=ex_info["size"],
                    internal_size=0.0,
                    detail=f"Exchange has {ex_info['direction']} "
                           f"{ex_info['size']:.4f} {instrument} "
                           f"but no APEX slot tracks it",
                ))

        # Sort: critical first, then by type
        severity_order = {"critical": 0, "warning": 1}
        discrepancies.sort(key=lambda d: (severity_order.get(d.severity, 2), d.type))

        return discrepancies
