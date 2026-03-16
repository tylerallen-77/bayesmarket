"""Tests for position sizing — edge cases and leverage cap."""

import pytest

from bayesmarket.risk.sizing import calculate_position_size
from bayesmarket import config


class TestPositionSizing:
    def test_basic_sizing(self):
        size = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99000,
        )
        assert size is not None
        assert size > 0

    def test_zero_capital(self):
        size = calculate_position_size(
            capital=0,
            entry_price=100000,
            sl_price=99000,
        )
        assert size is None

    def test_zero_entry_price(self):
        size = calculate_position_size(
            capital=1000,
            entry_price=0,
            sl_price=99000,
        )
        assert size is None

    def test_zero_sl_distance(self):
        size = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=100000,
        )
        assert size is None

    def test_leverage_cap(self):
        """Position should be capped at MAX_LEVERAGE * capital."""
        size = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99999,  # very tight SL → huge risk-based size
        )
        assert size is not None
        notional = size * 100000
        assert notional <= 1000 * config.MAX_LEVERAGE + 0.01  # floating point tolerance

    def test_cooldown_reduces_size(self):
        size_normal = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99000,
            cooldown_active=False,
        )
        size_cooldown = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99000,
            cooldown_active=True,
        )
        assert size_normal is not None
        assert size_cooldown is not None
        assert size_cooldown < size_normal

    def test_funding_caution_reduces_size(self):
        size_safe = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99000,
            funding_tier="safe",
        )
        size_caution = calculate_position_size(
            capital=1000,
            entry_price=100000,
            sl_price=99000,
            funding_tier="caution",
        )
        assert size_safe is not None
        assert size_caution is not None
        assert size_caution < size_safe

    def test_minimum_order_value(self):
        """Very small capital should return None (below MIN_ORDER_VALUE_USD)."""
        size = calculate_position_size(
            capital=1,  # $1 capital
            entry_price=100000,
            sl_price=99000,
        )
        # With $1 capital and 2% risk = $0.02 risk amount
        # $0.02 / $1000 SL distance = 0.00002 BTC = $2 notional
        # $2 < MIN_ORDER_VALUE_USD ($10) → should be None
        assert size is None
