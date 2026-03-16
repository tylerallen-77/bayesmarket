"""Tests for indicator scoring — verify score bounds and cascade logic."""

import time
from collections import deque

import pytest

from bayesmarket.data.state import (
    BookLevel,
    Candle,
    MarketState,
    SignalSnapshot,
    TimeframeState,
    TradeEvent,
)
from bayesmarket.indicators.order_flow import (
    compute_cvd_score,
    compute_depth_score,
    compute_obi_score,
)
from bayesmarket.indicators.structure import compute_ha_score, compute_poc, compute_vwap
from bayesmarket.indicators.momentum import compute_ema_score, compute_macd, compute_rsi


def _make_candles(n: int = 50, base_price: float = 100000.0) -> deque:
    """Create n synthetic candles for testing."""
    candles = deque(maxlen=200)
    for i in range(n):
        price = base_price + (i - n / 2) * 10
        candles.append(Candle(
            timestamp=time.time() - (n - i) * 60,
            open=price - 5,
            high=price + 20,
            low=price - 20,
            close=price + 5,
            volume=1.0 + i * 0.1,
            closed=True,
        ))
    return candles


def _make_state_with_book() -> MarketState:
    """Create a MarketState with populated bids/asks."""
    state = MarketState()
    state.mid_price = 100000.0
    state.bids = [
        BookLevel(price=99990, size=1.0),
        BookLevel(price=99980, size=2.0),
        BookLevel(price=99970, size=0.5),
    ]
    state.asks = [
        BookLevel(price=100010, size=0.8),
        BookLevel(price=100020, size=1.5),
        BookLevel(price=100030, size=0.3),
    ]
    return state


class TestCVDScore:
    def test_cvd_returns_bounded(self):
        state = _make_state_with_book()
        tf_state = TimeframeState(name="5m", role="trigger")
        now = time.time()
        for i in range(50):
            state.trades.append(TradeEvent(
                timestamp=now - 300 + i * 5,
                price=100000,
                size=0.01,
                is_buy=True,
                notional=1000,
            ))
        _, score = compute_cvd_score(state, tf_state)
        assert -2.0 <= score <= 2.0

    def test_cvd_empty_trades(self):
        state = _make_state_with_book()
        tf_state = TimeframeState(name="5m", role="trigger")
        _, score = compute_cvd_score(state, tf_state)
        assert score == 0.0


class TestOBIScore:
    def test_obi_balanced_book(self):
        state = _make_state_with_book()
        _, score = compute_obi_score(state, 0.5)
        assert -2.0 <= score <= 2.0

    def test_obi_empty_book(self):
        state = MarketState()
        state.mid_price = 100000.0
        _, score = compute_obi_score(state, 0.5)
        assert score == 0.0


class TestDepthScore:
    def test_depth_bounded(self):
        state = _make_state_with_book()
        _, score = compute_depth_score(state)
        assert -2.0 <= score <= 2.0


class TestVWAPScore:
    def test_vwap_bounded(self):
        candles = _make_candles()
        mid = 100000.0
        _, score = compute_vwap(candles, mid)
        assert -1.5 <= score <= 1.5

    def test_vwap_empty_candles(self):
        _, score = compute_vwap(deque(), 100000.0)
        assert score == 0.0


class TestPOCScore:
    def test_poc_bounded(self):
        candles = _make_candles()
        _, score = compute_poc(candles, 100000.0)
        assert -1.5 <= score <= 1.5

    def test_poc_empty_candles(self):
        _, score = compute_poc(deque(), 100000.0)
        assert score == 0.0


class TestHAScore:
    def test_ha_bounded(self):
        candles = _make_candles()
        _, score = compute_ha_score(candles)
        assert -1.5 <= score <= 1.5

    def test_ha_no_candles(self):
        _, score = compute_ha_score(deque())
        assert score == 0.0


class TestRSIScore:
    def test_rsi_bounded(self):
        candles = _make_candles()
        _, score = compute_rsi(candles)
        assert -1.0 <= score <= 1.0

    def test_rsi_insufficient_data(self):
        candles = _make_candles(5)
        _, score = compute_rsi(candles)
        assert score == 0.0


class TestMACDScore:
    def test_macd_bounded(self):
        candles = _make_candles(30)
        atr = 100.0
        _, score = compute_macd(candles, atr)
        assert -1.0 <= score <= 1.0


class TestEMAScore:
    def test_ema_bounded(self):
        candles = _make_candles(25)
        _, _, score = compute_ema_score(candles)
        assert -1.0 <= score <= 1.0

    def test_ema_insufficient_data(self):
        candles = _make_candles(5)
        _, _, score = compute_ema_score(candles)
        assert score == 0.0
