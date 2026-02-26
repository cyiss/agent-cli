"""DSL Guard — bridges the pure DSL engine with I/O (persistence, logging).

Can be used:
  1. Standalone (with StandaloneDSLRunner providing the tick loop)
  2. Composed into TradingEngine (called after each engine tick)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from modules.dsl_config import DSLConfig
from modules.dsl_state import DSLState, DSLStateStore
from modules.trailing_stop import DSLResult, TrailingStopEngine

log = logging.getLogger("dsl_guard")


class DSLGuard:
    """Manages one DSL guard for one position.

    Owns: DSL engine instance, state, persistence.
    Does NOT own: price fetching or order placement (injected by caller).
    """

    def __init__(
        self,
        config: DSLConfig,
        state: DSLState,
        store: Optional[DSLStateStore] = None,
    ):
        self.engine = TrailingStopEngine(config)
        self.config = config
        self.state = state
        self.store = store or DSLStateStore()

    def check(self, price: float) -> DSLResult:
        """Run one DSL evaluation cycle. Persists state automatically."""
        result = self.engine.evaluate(price, self.state)
        self.state = result.state
        self.state.last_check_ts = int(time.time() * 1000)

        self.store.save(self.state, self.config.to_dict())

        log.info(
            "DSL [%s] price=%.4f ROE=%.1f%% tier=%d floor=%.4f -> %s: %s",
            self.state.position_id,
            price,
            result.roe_pct,
            self.state.current_tier_index,
            result.effective_floor,
            result.action.value,
            result.reason,
        )

        return result

    def mark_closed(self, price: float, reason: str) -> None:
        """Mark the position as closed in state and persist."""
        self.state.closed = True
        self.state.close_reason = reason
        self.state.close_price = price
        self.state.close_ts = int(time.time() * 1000)
        self.store.save(self.state, self.config.to_dict())
        log.info("DSL [%s] marked closed: %s", self.state.position_id, reason)

    @property
    def is_active(self) -> bool:
        return not self.state.closed

    @classmethod
    def from_store(
        cls,
        position_id: str,
        store: Optional[DSLStateStore] = None,
    ) -> Optional[DSLGuard]:
        """Restore a guard from persisted state file."""
        store = store or DSLStateStore()
        data = store.load(position_id)
        if data is None:
            return None
        state = DSLState.from_dict(data["state"])
        config = DSLConfig.from_dict(data.get("config", {}))
        return cls(config=config, state=state, store=store)
