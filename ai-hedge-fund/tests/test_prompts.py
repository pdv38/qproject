"""
tests/test_prompts.py
Tests that all Claude prompts build without errors and contain required fields.
"""
import sys
sys.path.insert(0, ".")

import pytest
from ai.prompts import (
    SYSTEM_PROMPT,
    build_entry_prompt,
    build_hedge_prompt,
    build_exit_prompt,
    build_override_prompt,
)


def test_system_prompt_not_empty():
    assert len(SYSTEM_PROMPT) > 100


def test_entry_prompt_builds():
    prompt = build_entry_prompt(
        spot=500.0, strike=500, expiry="2025-06-20",
        atm_iv=0.18, iv_rank=65.0, hist_vol=0.15,
        straddle_greeks={"net_delta": 0.01, "net_gamma": -0.5},
        news_summary="No significant news.",
        account={"equity": 100000, "buying_power": 50000, "positions": 0},
    )
    assert "ENTRY" in prompt
    assert "500" in prompt
    assert "IV Rank" in prompt


def test_hedge_prompt_builds():
    prompt = build_hedge_prompt(
        net_delta=0.25, net_gamma=-1.2, net_vega=-150,
        spot=500.0, hedge_shares=10, unrealized_pnl=200.0,
        news_summary="Calm markets.", minutes_since_last_hedge=20,
    )
    assert "HEDGE" in prompt
    assert "net_delta" in prompt.lower() or "Delta" in prompt


def test_exit_prompt_builds():
    prompt = build_exit_prompt(
        portfolio_summary={"net_delta": 0.01, "total_pnl": 300},
        straddle_greeks={"net_theta": 5.0},
        dte_remaining=15,
        news_summary="No news.",
        entry_credit=1000.0,
        current_value=500.0,
        max_loss_usd=5000.0,
    )
    assert "EXIT" in prompt
    assert "1000" in prompt


def test_override_prompt_builds():
    prompt = build_override_prompt(
        breaking_news="Fed emergency rate cut announced.",
        portfolio_summary={"net_delta": 0.5},
        straddle_greeks={"net_gamma": -2.0},
        spot=495.0,
    )
    assert "FLATTEN" in prompt
    assert "OVERRIDE" in prompt or "BREAKING" in prompt
