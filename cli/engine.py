"""TradingEngine — autonomous tick loop for direct HL trading."""
from __future__ import annotations

import logging
import signal
import sys
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from common.models import MarketSnapshot
from parent.position_tracker import Position, PositionTracker
from parent.risk_manager import RiskLimits, RiskManager
from parent.store import JSONLStore, StateDB
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

from cli.display import shutdown_summary, tick_line
from cli.order_manager import OrderManager

log = logging.getLogger("engine")
ZERO = Decimal("0")


class TradingEngine:
    """Autonomous trading loop: fetch -> risk check -> strategy -> execute -> track."""

    def __init__(
        self,
        hl,  # DirectHLProxy | DirectMockProxy
        strategy: BaseStrategy,
        instrument: str = "ETH-PERP",
        tick_interval: float = 10.0,
        dry_run: bool = False,
        data_dir: str = "data/cli",
        risk_limits: Optional[RiskLimits] = None,
    ):
        self.hl = hl
        self.strategy = strategy
        self.instrument = instrument
        self.tick_interval = tick_interval
        self.dry_run = dry_run

        # Reuse existing components (no modifications to core)
        self.position_tracker = PositionTracker()
        self.risk_manager = RiskManager(limits=risk_limits)
        self.order_manager = OrderManager(hl, instrument=instrument, dry_run=dry_run)

        # Persistence
        self.state_db = StateDB(path=f"{data_dir}/state.db")
        self.trade_log = JSONLStore(path=f"{data_dir}/trades.jsonl")

        # Runtime state
        self.tick_count = 0
        self.start_time_ms = 0
        self._running = False

        # Optional DSL guard (composable mode — set via dsl_config)
        self.dsl_guard = None   # type: ignore[assignment]
        self.dsl_config = None  # type: ignore[assignment]

    def run(self, max_ticks: int = 0, resume: bool = True) -> None:
        """Main loop. Blocks until max_ticks reached or SIGINT/SIGTERM."""
        self._running = True
        self.start_time_ms = int(time.time() * 1000)

        if resume:
            self._restore_state()

        # Graceful shutdown handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        mode = "DRY RUN" if self.dry_run else "LIVE"
        log.info("Engine started: strategy=%s instrument=%s tick=%.1fs mode=%s",
                 self.strategy.strategy_id, self.instrument,
                 self.tick_interval, mode)

        while self._running:
            if max_ticks > 0 and self.tick_count >= max_ticks:
                log.info("Reached max ticks (%d), stopping", max_ticks)
                break

            try:
                self._tick()
            except Exception as e:
                log.error("Tick %d failed: %s", self.tick_count, e, exc_info=True)

            if self._running and self.tick_interval > 0:
                time.sleep(self.tick_interval)

        self._shutdown()

    def _tick(self) -> None:
        """Single tick: fetch -> risk -> strategy -> execute -> track."""
        self.tick_count += 1

        # 1. Fetch market data
        snapshot = self.hl.get_snapshot(self.instrument)
        if snapshot.mid_price <= 0:
            log.warning("T%d: no market data, skipping", self.tick_count)
            return

        # 2. Pre-tick risk check
        mark_prices = {self.instrument: Decimal(str(snapshot.mid_price))}
        ok, reason = self.risk_manager.pre_round_check(
            self.position_tracker, mark_prices
        )

        if not ok:
            log.warning("T%d: risk block: %s", self.tick_count, reason)
            self.order_manager.cancel_all()
            self._log_tick(snapshot, [], [], ok=False)
            return

        # 3. Build strategy context
        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        mid_dec = Decimal(str(snapshot.mid_price))

        context = StrategyContext(
            snapshot=snapshot,
            position_qty=float(pos.net_qty),
            position_notional=float(pos.notional),
            unrealized_pnl=float(pos.unrealized_pnl(mid_dec)),
            realized_pnl=float(pos.realized_pnl),
            reduce_only=self.risk_manager.state.reduce_only,
            safe_mode=self.risk_manager.state.safe_mode,
            round_number=self.tick_count,
            meta={
                "drawdown_pct": (
                    float(self.risk_manager.state.daily_drawdown / self.risk_manager.limits.tvl)
                    if self.risk_manager.limits.tvl > 0 else 0.0
                ),
            },
        )

        # 4. Run strategy
        decisions = self.strategy.on_tick(snapshot, context=context)

        # 5. Filter through risk manager
        order_dicts = [
            {"side": d.side, "size": d.size, "quantity": d.size, "limit_price": d.limit_price}
            for d in decisions if d.action == "place_order"
        ]
        valid_dicts = self.risk_manager.validate_orders(
            order_dicts, self.instrument, self.position_tracker,
        )
        # Rebuild filtered decisions list
        valid_set = set()
        for vd in valid_dicts:
            valid_set.add((vd["side"], vd["size"], vd["limit_price"]))
        valid_decisions = [
            d for d in decisions
            if d.action == "place_order"
            and (d.side, d.size, d.limit_price) in valid_set
        ]

        # 6. Execute orders
        fills = self.order_manager.update(valid_decisions, snapshot)

        # 7. Apply fills to position tracker
        for fill in fills:
            self.position_tracker.apply_fill(
                agent_id, self.instrument, fill.side,
                fill.quantity, fill.price,
            )
            self.trade_log.append({
                "tick": self.tick_count,
                "oid": fill.oid,
                "instrument": fill.instrument,
                "side": fill.side,
                "price": str(fill.price),
                "quantity": str(fill.quantity),
                "timestamp_ms": fill.timestamp_ms,
            })

        # 7b. Lazy DSL guard init (after first fill establishes a position)
        if self.dsl_config is not None and self.dsl_guard is None and fills:
            pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
            if pos.net_qty != ZERO:
                self._init_dsl_guard(pos)

        # 7c. Sync DSL position size with tracker (handles partial closes / add-ons)
        if self.dsl_guard is not None and self.dsl_guard.is_active and fills:
            pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
            if pos.net_qty == ZERO:
                # Position fully closed by strategy — deactivate DSL
                self.dsl_guard.mark_closed(snapshot.mid_price, "Position closed by strategy")
            else:
                self.dsl_guard.state.position_size = float(abs(pos.net_qty))

        # 8. Post-fill risk update
        self.risk_manager.post_fill_update(self.position_tracker, mark_prices)

        # 9. Persist state
        self._persist_state()

        # 10. Log tick
        self._log_tick(snapshot, valid_decisions, fills, ok=True)

        # 11. DSL guard check (composable mode)
        if self.dsl_guard is not None and self.dsl_guard.is_active:
            from modules.trailing_stop import DSLAction
            result = self.dsl_guard.check(snapshot.mid_price)
            if result.action == DSLAction.CLOSE:
                log.warning("DSL CLOSE: %s", result.reason)
                self._dsl_close_position(snapshot)
                self.dsl_guard.mark_closed(snapshot.mid_price, result.reason)
                self._running = False

    def _dsl_close_position(self, snapshot: MarketSnapshot) -> None:
        """Close position when DSL trailing stop triggers."""
        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        if pos.net_qty == ZERO:
            return

        close_side = "sell" if pos.net_qty > ZERO else "buy"
        size = float(abs(pos.net_qty))
        if close_side == "sell":
            price = round(float(snapshot.bid) * 0.995, 6)
        else:
            price = round(float(snapshot.ask) * 1.005, 6)

        if self.dry_run:
            log.info("[DRY RUN] DSL close: %s %.6f @ %.4f", close_side, size, price)
            return

        fill = self.hl.place_order(
            instrument=self.instrument,
            side=close_side,
            size=size,
            price=price,
            tif="Ioc",
        )
        if fill:
            self.position_tracker.apply_fill(
                agent_id, self.instrument, fill.side,
                fill.quantity, fill.price,
            )
            self.trade_log.append({
                "tick": self.tick_count,
                "oid": fill.oid,
                "instrument": fill.instrument,
                "side": fill.side,
                "price": str(fill.price),
                "quantity": str(fill.quantity),
                "timestamp_ms": fill.timestamp_ms,
                "meta": "dsl_close",
            })
            log.info("DSL closed position: %s %s @ %s", fill.side, fill.quantity, fill.price)
        else:
            log.warning("DSL close order did not fill — will retry next tick")
            self._running = True  # Keep running to retry

    def _init_dsl_guard(self, pos) -> None:
        """Initialize DSL guard from dsl_config after first position is established."""
        from modules.dsl_config import DSLConfig
        from modules.dsl_guard import DSLGuard
        from modules.dsl_state import DSLState

        direction = "long" if pos.net_qty > ZERO else "short"
        self.dsl_config.direction = direction

        # Auto-compute absolute floor if not set
        entry = float(pos.avg_entry_price)
        if self.dsl_config.phase1_absolute_floor == 0.0:
            lev = self.dsl_config.leverage
            if direction == "long":
                self.dsl_config.phase1_absolute_floor = entry * (1 - 0.03 / lev)
            else:
                self.dsl_config.phase1_absolute_floor = entry * (1 + 0.03 / lev)

        dsl_state = DSLState.new(
            instrument=self.instrument,
            entry_price=entry,
            position_size=float(abs(pos.net_qty)),
            direction=direction,
        )
        self.dsl_guard = DSLGuard(config=self.dsl_config, state=dsl_state)
        log.info("DSL guard activated: entry=%.4f size=%.6f dir=%s",
                 entry, float(abs(pos.net_qty)), direction)

    def _log_tick(self, snapshot, decisions, fills, ok: bool) -> None:
        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        mid_dec = Decimal(str(snapshot.mid_price))
        line = tick_line(
            tick=self.tick_count,
            instrument=self.instrument,
            mid=snapshot.mid_price,
            pos_qty=float(pos.net_qty),
            avg_entry=float(pos.avg_entry_price),
            upnl=float(pos.unrealized_pnl(mid_dec)),
            rpnl=float(pos.realized_pnl),
            orders_sent=len(decisions),
            orders_filled=len(fills),
            risk_ok=ok,
            reduce_only=self.risk_manager.state.reduce_only,
        )
        print(line, file=sys.stderr)

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received")
        self._running = False

    def _shutdown(self):
        log.info("Shutting down engine...")
        self.order_manager.cancel_all()
        self._persist_state()

        # Print summary
        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        elapsed = (time.time() * 1000 - self.start_time_ms) / 1000

        try:
            snap = self.hl.get_snapshot(self.instrument)
            mid = Decimal(str(snap.mid_price)) if snap.mid_price > 0 else pos.avg_entry_price
        except Exception:
            mid = pos.avg_entry_price

        total_pnl = float(pos.total_pnl(mid))
        stats = self.order_manager.stats
        summary = shutdown_summary(
            self.tick_count, stats["total_placed"], stats["total_filled"],
            total_pnl, elapsed,
        )
        print(summary, file=sys.stderr)
        self.state_db.close()

    def _persist_state(self):
        self.state_db.put("tick_count", self.tick_count)
        self.state_db.put("positions", self.position_tracker.to_dict())
        self.state_db.put("risk", self.risk_manager.to_dict())
        self.state_db.put("start_time_ms", self.start_time_ms)
        self.state_db.put("strategy_id", self.strategy.strategy_id)
        self.state_db.put("instrument", self.instrument)
        self.state_db.put("order_stats", self.order_manager.stats)

    def _restore_state(self):
        saved_tick = self.state_db.get("tick_count")
        if saved_tick is None:
            log.info("No saved state, starting fresh")
            return

        saved_strategy = self.state_db.get("strategy_id")
        saved_instrument = self.state_db.get("instrument")
        if saved_strategy != self.strategy.strategy_id or saved_instrument != self.instrument:
            log.warning(
                "Saved state mismatch (strategy=%s/%s, instrument=%s/%s), starting fresh",
                saved_strategy, self.strategy.strategy_id,
                saved_instrument, self.instrument,
            )
            return

        self.tick_count = saved_tick
        positions = self.state_db.get("positions")
        if positions:
            self.position_tracker = PositionTracker.from_dict(positions)
        risk = self.state_db.get("risk")
        if risk:
            self.risk_manager = RiskManager.from_dict(risk)
        self.start_time_ms = self.state_db.get("start_time_ms") or self.start_time_ms
        log.info("Restored state from tick %d", self.tick_count)
