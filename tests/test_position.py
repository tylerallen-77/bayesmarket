"""Tests for position tracking — PnL calculation, SL/TP checks."""

import pytest

from bayesmarket.data.state import Position
from bayesmarket.engine.position import (
    calculate_pnl,
    calculate_unrealized_pnl,
    check_sl,
    check_tp1,
    check_tp2,
    create_position,
)


def _make_long_position(entry: float = 100000.0) -> Position:
    return create_position(
        side="long",
        entry_price=entry,
        size=0.01,
        source_tfs=["5m"],
        sl_price=entry - 500,
        sl_basis="atr",
        sl_wall_info=None,
        tp1_price=entry + 300,
        tp2_price=entry + 600,
        score_5m=8.0,
        score_15m=None,
    )


def _make_short_position(entry: float = 100000.0) -> Position:
    return create_position(
        side="short",
        entry_price=entry,
        size=0.01,
        source_tfs=["5m"],
        sl_price=entry + 500,
        sl_basis="atr",
        sl_wall_info=None,
        tp1_price=entry - 300,
        tp2_price=entry - 600,
        score_5m=-8.0,
        score_15m=None,
    )


class TestPnLCalculation:
    def test_long_profit(self):
        pnl = calculate_pnl("long", 100000, 100500, 0.01)
        assert pnl == pytest.approx(5.0)

    def test_long_loss(self):
        pnl = calculate_pnl("long", 100000, 99500, 0.01)
        assert pnl == pytest.approx(-5.0)

    def test_short_profit(self):
        pnl = calculate_pnl("short", 100000, 99500, 0.01)
        assert pnl == pytest.approx(5.0)

    def test_short_loss(self):
        pnl = calculate_pnl("short", 100000, 100500, 0.01)
        assert pnl == pytest.approx(-5.0)

    def test_zero_size(self):
        pnl = calculate_pnl("long", 100000, 100500, 0.0)
        assert pnl == 0.0


class TestUnrealizedPnL:
    def test_long_unrealized(self):
        pos = _make_long_position()
        pnl = calculate_unrealized_pnl(pos, 100200)
        assert pnl > 0

    def test_short_unrealized(self):
        pos = _make_short_position()
        pnl = calculate_unrealized_pnl(pos, 99800)
        assert pnl > 0


class TestSLCheck:
    def test_long_sl_hit(self):
        pos = _make_long_position()
        assert check_sl(pos, 99400) is True

    def test_long_sl_not_hit(self):
        pos = _make_long_position()
        assert check_sl(pos, 100100) is False

    def test_short_sl_hit(self):
        pos = _make_short_position()
        assert check_sl(pos, 100600) is True

    def test_short_sl_not_hit(self):
        pos = _make_short_position()
        assert check_sl(pos, 99900) is False


class TestTP1Check:
    def test_long_tp1_hit(self):
        pos = _make_long_position()
        assert check_tp1(pos, 100400) is True

    def test_long_tp1_already_hit(self):
        pos = _make_long_position()
        pos.tp1_hit = True
        assert check_tp1(pos, 100400) is False

    def test_short_tp1_hit(self):
        pos = _make_short_position()
        assert check_tp1(pos, 99600) is True


class TestTP2Check:
    def test_tp2_requires_tp1_first(self):
        pos = _make_long_position()
        assert check_tp2(pos, 100700) is False

    def test_long_tp2_after_tp1(self):
        pos = _make_long_position()
        pos.tp1_hit = True
        assert check_tp2(pos, 100700) is True

    def test_short_tp2_after_tp1(self):
        pos = _make_short_position()
        pos.tp1_hit = True
        assert check_tp2(pos, 99300) is True


class TestCreatePosition:
    def test_tp_sizes(self):
        pos = _make_long_position()
        assert pos.tp1_size == pytest.approx(0.01 * 0.60)
        assert pos.tp2_size == pytest.approx(0.01 * 0.40)

    def test_initial_state(self):
        pos = _make_long_position()
        assert pos.remaining_size == 0.01
        assert pos.tp1_hit is False
        assert pos.tp2_hit is False
        assert pos.pnl_realized == 0.0
        assert pos.trailing_active is False
