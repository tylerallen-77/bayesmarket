"""Tests for risk state machine — cooldown, daily limit, full stop."""

import time

import pytest

from bayesmarket.data.state import RiskState
from bayesmarket.risk.limits import can_trade, update_after_trade, check_daily_reset
from bayesmarket import config


class TestCanTrade:
    def test_normal_state_allowed(self):
        risk = RiskState()
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is True
        assert reason == "ok"

    def test_daily_paused_blocks(self):
        risk = RiskState(daily_paused=True, daily_pause_until=time.time() + 3600)
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is False
        assert "daily_paused" in reason

    def test_daily_pause_expires(self):
        risk = RiskState(daily_paused=True, daily_pause_until=time.time() - 1)
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is True
        assert risk.daily_paused is False

    def test_full_stop_blocks(self):
        risk = RiskState(full_stop_active=True, full_stop_until=time.time() + 3600)
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is False
        assert "full_stop" in reason

    def test_full_stop_expires(self):
        risk = RiskState(full_stop_active=True, full_stop_until=time.time() - 1)
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is True
        assert risk.full_stop_active is False
        # Should produce an alert about full_stop expiring
        assert any("full_stop" in a[0] for a in alerts)

    def test_daily_loss_limit_triggers(self):
        risk = RiskState(daily_pnl=-75.0)  # -7.5% of $1000
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is False
        assert reason == "daily_loss_limit"
        assert risk.daily_paused is True

    def test_cooldown_timeout_reset(self):
        risk = RiskState(
            cooldown_active=True,
            cooldown_start_time=time.time() - config.COOLDOWN_RESET_SECONDS - 1,
            consecutive_losses=3,
        )
        allowed, reason, alerts = can_trade(risk, 1000.0)
        assert allowed is True
        assert risk.cooldown_active is False
        assert risk.consecutive_losses == 0


class TestUpdateAfterTrade:
    def test_loss_increments_counter(self):
        risk = RiskState()
        alerts = update_after_trade(risk, -10.0, 1000.0)
        assert risk.consecutive_losses == 1
        assert risk.consecutive_wins == 0

    def test_win_resets_loss_counter(self):
        risk = RiskState(consecutive_losses=2)
        alerts = update_after_trade(risk, 10.0, 1000.0)
        assert risk.consecutive_losses == 0
        assert risk.consecutive_wins == 1

    def test_cooldown_triggers_at_threshold(self):
        risk = RiskState(consecutive_losses=config.COOLDOWN_TRIGGER_LOSSES - 1)
        alerts = update_after_trade(risk, -10.0, 1000.0)
        assert risk.cooldown_active is True
        assert any("cooldown" in a[0] for a in alerts)

    def test_full_stop_on_double_cooldown(self):
        risk = RiskState(
            cooldown_active=True,
            consecutive_losses=config.COOLDOWN_TRIGGER_LOSSES - 1,
        )
        alerts = update_after_trade(risk, -10.0, 1000.0)
        assert risk.full_stop_active is True
        assert risk.cooldown_active is False
        assert any("full_stop" in a[0] for a in alerts)

    def test_cooldown_win_reset(self):
        risk = RiskState(
            cooldown_active=True,
            consecutive_wins=config.COOLDOWN_RESET_WINS - 1,
        )
        alerts = update_after_trade(risk, 10.0, 1000.0)
        assert risk.cooldown_active is False
        assert any("cooldown_reset" in a[0] for a in alerts)

    def test_daily_pnl_accumulates(self):
        risk = RiskState()
        update_after_trade(risk, -5.0, 1000.0)
        update_after_trade(risk, 10.0, 1000.0)
        update_after_trade(risk, -3.0, 1000.0)
        assert risk.daily_pnl == pytest.approx(2.0)
        assert risk.trades_today == 3


class TestDailyReset:
    def test_resets_counters(self):
        risk = RiskState(daily_pnl=-50.0, trades_today=10, daily_paused=True)
        # Force a reset by ensuring _last_reset_date is old
        import bayesmarket.risk.limits as limits_mod
        from datetime import date
        limits_mod._last_reset_date = date.min
        check_daily_reset(risk)
        assert risk.daily_pnl == 0.0
        assert risk.trades_today == 0
        assert risk.daily_paused is False

    def test_no_double_reset(self):
        risk = RiskState()
        import bayesmarket.risk.limits as limits_mod
        from datetime import date
        limits_mod._last_reset_date = date.min
        check_daily_reset(risk)
        risk.daily_pnl = -20.0
        check_daily_reset(risk)  # same day — should NOT reset
        assert risk.daily_pnl == -20.0
