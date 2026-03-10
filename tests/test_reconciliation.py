"""Tests for clearinghouse reconciliation engine."""
import pytest
from modules.reconciliation import ReconciliationEngine, Discrepancy


@pytest.fixture
def engine():
    return ReconciliationEngine()


def _slot(slot_id, instrument, size=1.0, direction="long", status="active"):
    return {
        "slot_id": slot_id,
        "status": status,
        "instrument": instrument,
        "entry_size": size,
        "direction": direction,
    }


def _exchange_pos(coin, szi):
    return {"position": {"coin": coin, "szi": str(szi)}}


class TestReconciliation:
    def test_no_discrepancies_when_matched(self, engine):
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [_exchange_pos("ETH", 1.0)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 0

    def test_orphan_exchange_detected(self, engine):
        """Exchange has a position but no slot tracks it."""
        slots = []
        exchange = [_exchange_pos("ETH", 2.5)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 1
        assert result[0].type == "orphan_exchange"
        assert result[0].severity == "critical"
        assert result[0].instrument == "ETH-PERP"
        assert result[0].exchange_size == 2.5
        assert result[0].internal_size == 0.0

    def test_orphan_slot_detected(self, engine):
        """Slot is active but exchange has no matching position."""
        slots = [_slot(0, "BTC-PERP", 0.5)]
        exchange = []
        result = engine.reconcile(slots, exchange)
        assert len(result) == 1
        assert result[0].type == "orphan_slot"
        assert result[0].severity == "warning"
        assert result[0].slot_id == 0

    def test_size_mismatch_warning(self, engine):
        """Small size mismatch (<10%) is warning."""
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [_exchange_pos("ETH", 0.95)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 1
        assert result[0].type == "size_mismatch"
        assert result[0].severity == "warning"

    def test_size_mismatch_critical(self, engine):
        """Large size mismatch (>10%) is critical."""
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [_exchange_pos("ETH", 0.5)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 1
        assert result[0].type == "size_mismatch"
        assert result[0].severity == "critical"

    def test_multiple_discrepancies_sorted(self, engine):
        """Critical discrepancies sort before warnings."""
        slots = [_slot(0, "BTC-PERP", 1.0)]  # orphan_slot (no exchange)
        exchange = [_exchange_pos("SOL", 100.0)]  # orphan_exchange
        result = engine.reconcile(slots, exchange)
        assert len(result) == 2
        assert result[0].severity == "critical"  # orphan_exchange first
        assert result[1].severity == "warning"    # orphan_slot second

    def test_empty_slots_and_exchange(self, engine):
        result = engine.reconcile([], [])
        assert len(result) == 0

    def test_ignores_non_active_slots(self, engine):
        slots = [_slot(0, "ETH-PERP", 1.0, status="empty")]
        exchange = [_exchange_pos("ETH", 1.0)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 1
        assert result[0].type == "orphan_exchange"

    def test_ignores_zero_exchange_positions(self, engine):
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [_exchange_pos("ETH", 1.0), _exchange_pos("BTC", 0.0)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 0

    def test_short_position_direction(self, engine):
        slots = [_slot(0, "ETH-PERP", 1.0, direction="short")]
        exchange = [_exchange_pos("ETH", -1.0)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 0  # sizes match (abs)

    def test_flat_exchange_format(self, engine):
        """Handle flat position dicts (no nested 'position' key)."""
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [{"coin": "ETH", "szi": "1.0"}]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 0

    def test_small_mismatch_ignored(self, engine):
        """<1% mismatch is ignored (rounding noise)."""
        slots = [_slot(0, "ETH-PERP", 1.0)]
        exchange = [_exchange_pos("ETH", 0.998)]
        result = engine.reconcile(slots, exchange)
        assert len(result) == 0

    def test_to_dict(self, engine):
        slots = []
        exchange = [_exchange_pos("ETH", 1.0)]
        result = engine.reconcile(slots, exchange)
        d = result[0].to_dict()
        assert d["type"] == "orphan_exchange"
        assert "instrument" in d
