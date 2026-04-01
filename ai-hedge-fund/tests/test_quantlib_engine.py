"""
tests/test_quantlib_engine.py
Unit tests for QuantLib pricing engine — no API keys required.
"""

import math
import pytest
from datetime import date, timedelta

import sys
sys.path.insert(0, ".")

from core.quantlib_engine import QuantLibEngine, OptionGreeks, StraddleGreeks


@pytest.fixture
def ql():
    return QuantLibEngine(risk_free_rate=0.053, dividend_yield=0.013)


@pytest.fixture
def expiry():
    return date.today() + timedelta(days=30)


class TestOptionPricing:
    def test_call_price_positive(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert g.price > 0

    def test_put_price_positive(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "put", 0.18)
        assert g.price > 0

    def test_call_delta_between_0_and_1(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert 0 < g.delta < 1

    def test_put_delta_between_minus1_and_0(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "put", 0.18)
        assert -1 < g.delta < 0

    def test_atm_call_delta_near_half(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert 0.45 < g.delta < 0.60, f"ATM call delta should be ~0.50, got {g.delta}"

    def test_atm_put_delta_near_minus_half(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "put", 0.18)
        assert -0.60 < g.delta < -0.40, f"ATM put delta should be ~-0.50, got {g.delta}"

    def test_gamma_positive(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert g.gamma > 0

    def test_vega_positive(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert g.vega > 0

    def test_theta_negative(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        assert g.theta < 0, "Theta should be negative (time decay)"

    def test_call_put_parity(self, ql, expiry):
        """Put-Call parity: C - P = S - K*e^(-rT)"""
        S, K, iv = 500.0, 500.0, 0.18
        T = (expiry - date.today()).days / 365.0
        r = ql.risk_free_rate
        q = ql.dividend_yield

        call = ql.price_option(S, K, expiry, "call", iv)
        put  = ql.price_option(S, K, expiry, "put",  iv)

        lhs = call.price - put.price
        rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
        assert abs(lhs - rhs) < 0.05, f"Put-call parity violated: {lhs:.4f} vs {rhs:.4f}"

    def test_itm_call_higher_than_otm(self, ql, expiry):
        itm = ql.price_option(510, 500, expiry, "call", 0.18)
        otm = ql.price_option(490, 500, expiry, "call", 0.18)
        assert itm.price > otm.price

    def test_higher_vol_higher_price(self, ql, expiry):
        low_vol  = ql.price_option(500, 500, expiry, "call", 0.10)
        high_vol = ql.price_option(500, 500, expiry, "call", 0.30)
        assert high_vol.price > low_vol.price

    def test_greeks_to_dict(self, ql, expiry):
        g = ql.price_option(500, 500, expiry, "call", 0.18)
        d = g.to_dict()
        assert "delta" in d and "gamma" in d and "vega" in d
        assert "theta" in d and "iv" in d and "price" in d


class TestStraddlePricing:
    def test_straddle_credit_positive(self, ql, expiry):
        s = ql.price_straddle(500, 500, expiry, 0.18, 0.18, n_contracts=1)
        assert s.net_price > 0

    def test_atm_straddle_net_delta_near_zero(self, ql, expiry):
        s = ql.price_straddle(500, 500, expiry, 0.18, 0.18, n_contracts=1)
        # Short straddle: net delta should be near 0 for ATM
        assert abs(s.net_delta) < 10, f"Net delta too large: {s.net_delta}"

    def test_straddle_net_gamma_negative(self, ql, expiry):
        s = ql.price_straddle(500, 500, expiry, 0.18, 0.18, n_contracts=1)
        assert s.net_gamma < 0, "Short straddle should have negative gamma"

    def test_straddle_net_theta_positive(self, ql, expiry):
        s = ql.price_straddle(500, 500, expiry, 0.18, 0.18, n_contracts=1)
        assert s.net_theta > 0, "Short straddle earns theta (positive)"

    def test_straddle_to_dict_keys(self, ql, expiry):
        s = ql.price_straddle(500, 500, expiry, 0.18, 0.18)
        d = s.to_dict()
        for key in ["net_delta", "net_gamma", "net_vega", "net_theta", "net_price"]:
            assert key in d, f"Missing key: {key}"


class TestVolatility:
    def test_hist_vol_positive(self, ql):
        prices = [100 + i * 0.5 + (i % 3) * -0.3 for i in range(50)]
        hv = ql.compute_historical_vol(prices, window=21)
        assert hv > 0

    def test_hist_vol_returns_fallback_on_short_series(self, ql):
        prices = [100, 101, 102]
        hv = ql.compute_historical_vol(prices, window=21)
        assert hv == 0.20  # fallback

    def test_iv_rank_zero_when_at_min(self, ql):
        history = [0.10, 0.15, 0.12, 0.11, 0.13]
        rank = ql.compute_iv_rank(0.10, history)
        assert rank == 0.0

    def test_iv_rank_100_when_at_max(self, ql):
        history = [0.10, 0.15, 0.12, 0.11, 0.13]
        rank = ql.compute_iv_rank(0.15, history)
        assert rank == 100.0

    def test_iv_rank_midpoint(self, ql):
        history = [0.10, 0.20]
        rank = ql.compute_iv_rank(0.15, history)
        assert abs(rank - 50.0) < 0.01

    def test_sabr_atm_returns_float(self, ql):
        vol = ql.sabr_vol(F=500, K=500, T=0.25, alpha=0.3, beta=0.5, rho=-0.3, nu=0.4)
        assert isinstance(vol, float) and vol > 0

    def test_sabr_smile_has_multiple_strikes(self, ql):
        expiry = date.today() + timedelta(days=30)
        smile  = ql.build_sabr_smile(spot=500, expiry=expiry, atm_iv=0.18)
        assert len(smile) > 3

    def test_hedge_shares_buys_when_delta_negative(self, ql):
        # Short straddle moved down → negative delta → need to BUY shares
        shares = ql.hedge_shares_needed(net_portfolio_delta=-5.0, spot=500)
        assert shares > 0

    def test_hedge_shares_sells_when_delta_positive(self, ql):
        shares = ql.hedge_shares_needed(net_portfolio_delta=5.0, spot=500)
        assert shares < 0


class TestPortfolio:
    def test_open_straddle_sets_credit(self):
        from core.portfolio import Portfolio
        p = Portfolio()
        p.open_straddle(
            symbol="SPY", strike=500, expiry="2025-06-20",
            call_symbol="SPY250620C00500000",
            put_symbol="SPY250620P00500000",
            call_entry_price=5.0, put_entry_price=5.0, n_contracts=1
        )
        assert p.straddle is not None
        assert p.straddle.entry_credit == 1000.0  # (5+5)*100*1

    def test_unrealized_pnl_positive_when_value_falls(self):
        from core.portfolio import Portfolio
        p = Portfolio()
        p.open_straddle(
            symbol="SPY", strike=500, expiry="2025-06-20",
            call_symbol="SPY250620C00500000",
            put_symbol="SPY250620P00500000",
            call_entry_price=5.0, put_entry_price=5.0, n_contracts=1
        )
        # Value falls (we sold, so this is profit)
        p.update_straddle_prices(3.0, 3.0, 0.0, 0.0, 0.0, 0.0)
        assert p.straddle.unrealized_pnl == pytest.approx(400.0)

    def test_close_straddle_updates_realized_pnl(self):
        from core.portfolio import Portfolio
        p = Portfolio()
        p.open_straddle(
            symbol="SPY", strike=500, expiry="2025-06-20",
            call_symbol="SPY250620C00500000",
            put_symbol="SPY250620P00500000",
            call_entry_price=5.0, put_entry_price=5.0, n_contracts=1
        )
        p.update_straddle_prices(3.0, 3.0, 0.0, 0.0, 0.0, 0.0)
        p.close_straddle(reason="test")
        assert p.realized_pnl == pytest.approx(400.0)
        assert p.straddle is None

    def test_net_delta_includes_hedge(self):
        from core.portfolio import Portfolio
        p = Portfolio()
        p.open_straddle(
            symbol="SPY", strike=500, expiry="2025-06-20",
            call_symbol="SPY250620C00500000",
            put_symbol="SPY250620P00500000",
            call_entry_price=5.0, put_entry_price=5.0, n_contracts=1
        )
        p.update_straddle_prices(5.0, 5.0, net_delta=-3.0, net_gamma=0, net_vega=0, net_theta=0)
        p.update_hedge(shares_traded=3, price=500)
        assert p.net_delta == pytest.approx(0.0)
