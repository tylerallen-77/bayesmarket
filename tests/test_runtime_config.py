"""Tests for runtime-configurable risk parameters and regime-adaptive TP."""

from bayesmarket.runtime import RuntimeConfig
from bayesmarket.data.state import MarketState, RiskState, TimeframeState, SignalSnapshot
from bayesmarket.engine.position import create_position
from bayesmarket.engine.executor import _get_current_regime
from bayesmarket.risk.sizing import calculate_position_size
from bayesmarket.risk.limits import can_trade


class TestRuntimeConfigDefaults:
    def test_defaults(self):
        rt = RuntimeConfig()
        assert rt.max_risk_per_trade == 0.02
        assert rt.max_leverage == 5.0
        assert rt.daily_loss_limit == 0.07
        assert rt.tp1_size_pct == 0.60
        assert rt.trailing_stop_enabled is True
        assert rt.trailing_stop_distance_atr == 0.75
        assert rt.tp_regime_adaptive is True


class TestCreatePositionWithRuntime:
    def test_tp1_size_30pct(self):
        rt = RuntimeConfig(tp1_size_pct=0.3)
        pos = create_position("long", 80000, 0.01, ["5m"], 79000, "atr", None, 81000, 82000, 7.5, None, runtime=rt)
        assert abs(pos.tp1_size - 0.003) < 1e-9
        assert abs(pos.tp2_size - 0.007) < 1e-9

    def test_tp1_size_100pct(self):
        rt = RuntimeConfig(tp1_size_pct=1.0)
        pos = create_position("long", 80000, 0.01, ["5m"], 79000, "atr", None, 81000, 82000, 7.5, None, runtime=rt)
        assert abs(pos.tp1_size - 0.01) < 1e-9
        assert abs(pos.tp2_size - 0.0) < 1e-9

    def test_tp1_size_default_without_runtime(self):
        pos = create_position("long", 80000, 0.01, ["5m"], 79000, "atr", None, 81000, 82000, 7.5, None)
        assert abs(pos.tp1_size - 0.006) < 1e-9
        assert abs(pos.tp2_size - 0.004) < 1e-9


class TestSizingWithRuntime:
    def test_custom_risk_and_leverage(self):
        rt = RuntimeConfig(max_risk_per_trade=0.01, max_leverage=2.0)
        size = calculate_position_size(1000, 80000, 79000, runtime=rt)
        assert size is not None
        notional = size * 80000
        assert notional <= 1000 * 2.0 + 0.01

    def test_fallback_to_config_without_runtime(self):
        size = calculate_position_size(1000, 80000, 79000)
        assert size is not None

    def test_higher_risk_pct(self):
        rt = RuntimeConfig(max_risk_per_trade=0.05, max_leverage=10.0)
        size = calculate_position_size(1000, 80000, 79000, runtime=rt)
        assert size is not None
        # 5% risk on $1000 = $50 risk, SL dist = $1000, risk_size = 50/1000 = 0.05
        assert abs(size - 0.05) < 1e-6


class TestCanTradeWithRuntime:
    def test_tighter_daily_limit_blocks(self):
        rt = RuntimeConfig(daily_loss_limit=0.03)
        risk = RiskState(daily_pnl=-35)
        allowed, reason, _ = can_trade(risk, 1000, runtime=rt)
        assert not allowed
        assert reason == "daily_loss_limit"

    def test_default_limit_allows_same_loss(self):
        risk = RiskState(daily_pnl=-35)
        allowed, _, _ = can_trade(risk, 1000)
        assert allowed

    def test_runtime_none_uses_config(self):
        risk = RiskState(daily_pnl=-75)
        allowed, _, _ = can_trade(risk, 1000, runtime=None)
        assert not allowed


class TestGetCurrentRegime:
    def test_default_trending(self):
        state = MarketState()
        assert _get_current_regime(state) == "trending"

    def test_ranging_from_signal(self):
        state = MarketState()
        tf = TimeframeState(name="5m", role="trigger")
        tf.signal = SignalSnapshot(timestamp=0, timeframe="5m", regime="ranging")
        state.tf_states["5m"] = tf
        assert _get_current_regime(state) == "ranging"

    def test_trending_from_signal(self):
        state = MarketState()
        tf = TimeframeState(name="5m", role="trigger")
        tf.signal = SignalSnapshot(timestamp=0, timeframe="5m", regime="trending")
        state.tf_states["5m"] = tf
        assert _get_current_regime(state) == "trending"

    def test_falls_back_to_15m(self):
        state = MarketState()
        tf15 = TimeframeState(name="15m", role="timing")
        tf15.signal = SignalSnapshot(timestamp=0, timeframe="15m", regime="ranging")
        state.tf_states["15m"] = tf15
        assert _get_current_regime(state) == "ranging"
