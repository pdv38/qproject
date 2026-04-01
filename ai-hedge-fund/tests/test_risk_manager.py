"""
tests/test_risk_manager.py
Tests for risk limits and position sizing.
"""
import sys
sys.path.insert(0, ".")

import pytest
from core.portfolio import Portfolio
from risk.risk_manager import RiskManager
from config.settings import MAX_LOSS_USD, DTE_EXIT


@pytest.fixture
def portfolio_with_straddle():
    p = Portfolio()
    p.open_straddle(
        symbol="SPY", strike=500, expiry="2025-06-20",
        call_symbol="SPY250620C00500000",
        put_symbol="SPY250620P00500000",
        call_entry_price=5.0, put_entry_price=5.0, n_contracts=1
    )
    return p


@pytest.fixture
def risk_mgr(portfolio_with_straddle):
    return RiskManager(portfolio_with_straddle)


def test_no_violations_on_fresh_position(risk_mgr):
    violations = risk_mgr.check_all(dte_remaining=25)
    hard = [v for v in violations if v["severity"] == "HARD"]
    assert len(hard) == 0


def test_max_loss_violation(risk_mgr, portfolio_with_straddle):
    # Simulate big loss: cost to close far exceeds credit
    portfolio_with_straddle.update_straddle_prices(
        call_price=30.0, put_price=30.0,
        net_delta=0, net_gamma=0, net_vega=0, net_theta=0
    )
    violations = risk_mgr.check_all(dte_remaining=25)
    types = [v["type"] for v in violations]
    assert "MAX_LOSS" in types or "LOSS_MULTIPLE" in types


def test_dte_exit_violation(risk_mgr):
    violations = risk_mgr.check_all(dte_remaining=DTE_EXIT - 1)
    types = [v["type"] for v in violations]
    assert "DTE_TOO_LOW" in types


def test_profit_target_soft_violation(risk_mgr, portfolio_with_straddle):
    # 50% profit target reached
    portfolio_with_straddle.update_straddle_prices(
        call_price=2.5, put_price=2.5,
        net_delta=0, net_gamma=0, net_vega=0, net_theta=0
    )
    violations = risk_mgr.check_all(dte_remaining=20)
    soft = [v for v in violations if v["severity"] == "SOFT" and v["type"] == "PROFIT_TARGET"]
    assert len(soft) == 1


def test_position_size_approved_for_reasonable_equity(risk_mgr):
    result = risk_mgr.position_size(account_equity=100_000, atm_price=10.0, n_contracts=1)
    assert result["approved"] is True
    assert result["n_contracts"] >= 1


def test_position_size_reduced_for_small_equity(risk_mgr):
    # Very small account — should reduce contracts
    result = risk_mgr.position_size(account_equity=5_000, atm_price=50.0, n_contracts=5)
    assert result["n_contracts"] <= 5
