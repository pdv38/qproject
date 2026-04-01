"""
core/quantlib_engine.py
QuantLib-powered options pricing engine.
Computes: IV surface, Greeks (delta/gamma/vega/theta/rho), SABR vol parametrization.
"""

import math
import logging
from datetime import date, datetime
from typing import Optional

import numpy as np
import QuantLib as ql

logger = logging.getLogger(__name__)

# ── QuantLib Calendar & Day Count Setup ────────────────────────────────────────
CALENDAR        = ql.UnitedStates(ql.UnitedStates.NYSE)
DAY_COUNT       = ql.Actual365Fixed()
SETTLEMENT_DAYS = 0


class OptionGreeks:
    """Container for computed Greeks."""
    def __init__(self):
        self.delta:        float = 0.0
        self.gamma:        float = 0.0
        self.vega:         float = 0.0
        self.theta:        float = 0.0
        self.rho:          float = 0.0
        self.iv:           float = 0.0
        self.price:        float = 0.0
        self.intrinsic:    float = 0.0
        self.time_value:   float = 0.0

    def __repr__(self):
        return (
            f"OptionGreeks(Δ={self.delta:.4f} Γ={self.gamma:.4f} "
            f"ν={self.vega:.4f} θ={self.theta:.4f} IV={self.iv:.2%})"
        )

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "vega":  round(self.vega, 6),
            "theta": round(self.theta, 6),
            "rho":   round(self.rho, 6),
            "iv":    round(self.iv, 6),
            "price": round(self.price, 4),
        }


class StraddleGreeks:
    """Aggregated Greeks for the full short straddle position."""
    def __init__(self, call: OptionGreeks, put: OptionGreeks, n_contracts: int = 1):
        self.call = call
        self.put  = put
        mult = n_contracts * 100  # options multiplier

        # For SHORT straddle: we sold call + put, so negate
        self.net_delta = -(call.delta + put.delta) * mult
        self.net_gamma = -(call.gamma + put.gamma) * mult
        self.net_vega  = -(call.vega  + put.vega)  * mult
        self.net_theta = -(call.theta + put.theta)  * mult
        self.net_price =  (call.price + put.price)  * mult  # credit received

    def __repr__(self):
        return (
            f"StraddleGreeks(net_Δ={self.net_delta:.4f} "
            f"net_Γ={self.net_gamma:.4f} "
            f"net_ν={self.net_vega:.4f} "
            f"net_θ={self.net_theta:.4f} "
            f"credit=${self.net_price:.2f})"
        )

    def to_dict(self) -> dict:
        return {
            "net_delta": round(self.net_delta, 4),
            "net_gamma": round(self.net_gamma, 4),
            "net_vega":  round(self.net_vega, 4),
            "net_theta": round(self.net_theta, 4),
            "net_price": round(self.net_price, 2),
            "call":      self.call.to_dict(),
            "put":       self.put.to_dict(),
        }


class QuantLibEngine:
    """
    Full QuantLib pricing and volatility surface engine.
    Supports BSM pricing, Greek computation, and SABR vol parametrization.
    """

    def __init__(self, risk_free_rate: float = 0.053, dividend_yield: float = 0.013):
        """
        Args:
            risk_free_rate:  Current risk-free rate (default: ~5.3% SOFR)
            dividend_yield:  SPY dividend yield (default: ~1.3%)
        """
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        logger.info(
            f"QuantLib engine initialized | r={risk_free_rate:.2%} | q={dividend_yield:.2%}"
        )

    def _setup_process(
        self, spot: float, vol: float, valuation_date: ql.Date
    ) -> ql.BlackScholesMertonProcess:
        """Build a BSM process for the given spot and volatility."""
        ql.Settings.instance().evaluationDate = valuation_date

        spot_handle   = ql.QuoteHandle(ql.SimpleQuote(spot))
        rate_curve    = ql.FlatForward(valuation_date, self.risk_free_rate, DAY_COUNT)
        div_curve     = ql.FlatForward(valuation_date, self.dividend_yield,  DAY_COUNT)
        vol_surface   = ql.BlackConstantVol(valuation_date, CALENDAR, vol, DAY_COUNT)

        return ql.BlackScholesMertonProcess(
            spot_handle,
            ql.YieldTermStructureHandle(div_curve),
            ql.YieldTermStructureHandle(rate_curve),
            ql.BlackVolTermStructureHandle(vol_surface),
        )

    def price_option(
        self,
        spot:           float,
        strike:         float,
        expiry_date:    date,
        option_type:    str,        # 'call' or 'put'
        vol:            float,      # implied volatility (annualized)
        valuation_date: Optional[date] = None,
    ) -> OptionGreeks:
        """
        Price an option and compute all Greeks using QuantLib BSM.

        Returns OptionGreeks object.
        """
        val_date   = valuation_date or date.today()
        ql_val     = ql.Date(val_date.day, val_date.month, val_date.year)
        ql_expiry  = ql.Date(expiry_date.day, expiry_date.month, expiry_date.year)

        opt_type   = ql.Option.Call if option_type.lower() == "call" else ql.Option.Put
        payoff     = ql.PlainVanillaPayoff(opt_type, strike)
        exercise   = ql.EuropeanExercise(ql_expiry)
        option     = ql.VanillaOption(payoff, exercise)

        process    = self._setup_process(spot, vol, ql_val)
        engine     = ql.AnalyticEuropeanEngine(process)
        option.setPricingEngine(engine)

        greeks = OptionGreeks()
        try:
            greeks.price     = option.NPV()
            greeks.delta     = option.delta()
            greeks.gamma     = option.gamma()
            greeks.vega      = option.vega()     / 100  # per 1% vol move
            greeks.theta     = option.theta()    / 365  # per calendar day
            greeks.rho       = option.rho()      / 100
            greeks.iv        = vol
            greeks.intrinsic = max(0, (spot - strike) if option_type == "call" else (strike - spot))
            greeks.time_value = greeks.price - greeks.intrinsic
        except Exception as e:
            logger.warning(f"Pricing error ({option_type} K={strike}): {e}")

        return greeks

    def compute_iv(
        self,
        market_price:   float,
        spot:           float,
        strike:         float,
        expiry_date:    date,
        option_type:    str,
        valuation_date: Optional[date] = None,
    ) -> float:
        """
        Back out implied volatility from a market price using Brent solver.
        Returns IV as a decimal (e.g. 0.18 = 18%).
        """
        val_date  = valuation_date or date.today()
        ql_val    = ql.Date(val_date.day, val_date.month, val_date.year)
        ql_expiry = ql.Date(expiry_date.day, expiry_date.month, expiry_date.year)
        ql.Settings.instance().evaluationDate = ql_val

        opt_type  = ql.Option.Call if option_type.lower() == "call" else ql.Option.Put
        payoff    = ql.PlainVanillaPayoff(opt_type, strike)
        exercise  = ql.EuropeanExercise(ql_expiry)
        option    = ql.VanillaOption(payoff, exercise)

        try:
            iv = option.impliedVolatility(
                targetValue   = market_price,
                process       = self._setup_process(spot, 0.20, ql_val),  # seed vol
                accuracy      = 1e-6,
                maxEvaluations= 200,
                minVol        = 0.01,
                maxVol        = 5.00,
            )
            return iv
        except Exception as e:
            logger.warning(f"IV solve failed (price={market_price}, K={strike}): {e}")
            return 0.20  # fallback to 20%

    def price_straddle(
        self,
        spot:        float,
        strike:      float,
        expiry_date: date,
        call_vol:    float,
        put_vol:     float,
        n_contracts: int = 1,
    ) -> StraddleGreeks:
        """
        Price a straddle and return aggregated Greeks.
        For ATM straddles, call_vol ≈ put_vol ≈ ATM IV.
        """
        call_greeks = self.price_option(spot, strike, expiry_date, "call", call_vol)
        put_greeks  = self.price_option(spot, strike, expiry_date, "put",  put_vol)
        return StraddleGreeks(call_greeks, put_greeks, n_contracts)

    # ── Volatility Surface ─────────────────────────────────────────────────────

    def compute_historical_vol(self, prices: list[float], window: int = 21) -> float:
        """
        Compute annualized historical (realized) volatility from a price series.
        Uses close-to-close log returns.
        """
        if len(prices) < window + 1:
            return 0.20
        log_returns = [
            math.log(prices[i] / prices[i - 1])
            for i in range(len(prices) - window, len(prices))
        ]
        std_daily = np.std(log_returns, ddof=1)
        return std_daily * math.sqrt(252)

    def compute_iv_rank(self, current_iv: float, iv_history: list[float]) -> float:
        """
        IV Rank = (current IV - 52wk low) / (52wk high - 52wk low) * 100
        Returns a value 0–100.
        """
        if not iv_history or len(iv_history) < 2:
            return 50.0
        low  = min(iv_history)
        high = max(iv_history)
        if high == low:
            return 50.0
        return ((current_iv - low) / (high - low)) * 100

    def sabr_vol(
        self,
        F: float,       # ATM forward
        K: float,       # strike
        T: float,       # time to expiry (years)
        alpha: float,   # SABR alpha (vol of vol scale)
        beta: float,    # SABR beta (0=normal, 1=lognormal)
        rho: float,     # correlation
        nu: float,      # vol of vol
    ) -> float:
        """
        Hagan et al. SABR approximation formula for implied vol.
        Used to parametrize the full vol smile.
        """
        if abs(F - K) < 1e-8:
            # ATM formula
            term1 = alpha / (F ** (1 - beta))
            term2 = (
                ((1 - beta) ** 2 / 24) * (alpha ** 2) / (F ** (2 - 2 * beta))
                + (rho * beta * nu * alpha) / (4 * F ** (1 - beta))
                + (2 - 3 * rho ** 2) / 24 * nu ** 2
            )
            return term1 * (1 + term2 * T)

        log_fk   = math.log(F / K)
        fk_beta  = (F * K) ** ((1 - beta) / 2)
        z        = (nu / alpha) * fk_beta * log_fk
        x_z      = math.log((math.sqrt(1 - 2 * rho * z + z ** 2) + z - rho) / (1 - rho))

        num = alpha
        den = fk_beta * (
            1
            + (1 - beta) ** 2 / 24 * log_fk ** 2
            + (1 - beta) ** 4 / 1920 * log_fk ** 4
        )
        bracket = (
            1
            + (
                (1 - beta) ** 2 / 24 * alpha ** 2 / fk_beta ** 2
                + rho * beta * nu * alpha / (4 * fk_beta)
                + (2 - 3 * rho ** 2) / 24 * nu ** 2
            ) * T
        )

        return (num / den) * (z / x_z) * bracket

    def build_sabr_smile(
        self,
        spot:   float,
        expiry: date,
        atm_iv: float,
        strikes: Optional[list[float]] = None,
    ) -> dict:
        """
        Build a SABR vol smile around the ATM strike.
        Uses typical SPY SABR parameters (alpha calibrated to ATM IV).
        Returns {strike: vol} dict.
        """
        T       = (expiry - date.today()).days / 365.0
        F       = spot  # approximate forward ≈ spot for short dated
        beta    = 0.5   # typical for equity
        rho     = -0.30 # typical negative skew for equity
        nu      = 0.40  # vol of vol
        # Back-solve alpha from ATM IV
        alpha   = atm_iv * (F ** (1 - beta))

        if strikes is None:
            deltas = [-0.05, -0.10, -0.15, -0.20, -0.25, 0.0, 0.25, 0.20, 0.15, 0.10, 0.05]
            strikes = sorted([round(spot * (1 + d * 0.1)) for d in deltas] + [round(spot)])

        smile = {}
        for K in strikes:
            try:
                smile[K] = self.sabr_vol(F, K, T, alpha, beta, rho, nu)
            except Exception:
                smile[K] = atm_iv

        return smile

    def hedge_shares_needed(
        self, net_portfolio_delta: float, spot: float
    ) -> int:
        """
        How many SPY shares to buy/sell to neutralize delta?
        delta of 1 share of SPY = +1.0

        Returns: positive = buy, negative = sell
        """
        shares = -round(net_portfolio_delta)
        logger.info(
            f"Delta hedge: portfolio_delta={net_portfolio_delta:.4f} "
            f"→ {'BUY' if shares > 0 else 'SELL'} {abs(shares)} shares"
        )
        return shares
