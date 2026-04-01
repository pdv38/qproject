"""
strategies/backtrader_strategy.py
Backtrader strategy for backtesting the ATM short straddle + delta hedging.
Uses QuantLib for Greeks computation in the backtest loop.
Note: Options simulation in backtrader uses synthetic pricing via QuantLib BSM.
"""

import logging
import math
from datetime import date, timedelta
from typing import Optional

import backtrader as bt
import backtrader.indicators as btind

from core.quantlib_engine import QuantLibEngine
from config.settings import (
    DELTA_THRESHOLD, DTE_TARGET, DTE_EXIT, IV_RANK_MIN,
    PROFIT_TARGET_PCT, MAX_LOSS_MULT, NUM_CONTRACTS, SHARES_PER_CONTRACT
)

logger = logging.getLogger(__name__)


class ATMStraddleStrategy(bt.Strategy):
    """
    Backtrader strategy that simulates a short ATM straddle + dynamic delta hedge.
    Uses QuantLib BSM for synthetic options pricing.

    This is a SIMULATION — real options slippage/spread not modeled.
    Use for directional signal validation only.
    """

    params = dict(
        delta_threshold   = DELTA_THRESHOLD,
        dte_target        = DTE_TARGET,
        dte_exit          = DTE_EXIT,
        iv_rank_min       = IV_RANK_MIN,
        profit_target_pct = PROFIT_TARGET_PCT,
        max_loss_mult     = MAX_LOSS_MULT,
        n_contracts       = NUM_CONTRACTS,
        hv_window         = 21,          # Historical vol lookback
        iv_premium        = 1.15,        # IV = HV * premium (vol risk premium proxy)
        rehedge_bars      = 6,           # Re-evaluate hedge every N bars (1-min bars)
        risk_free_rate    = 0.053,
        dividend_yield    = 0.013,
        printlog          = True,
    )

    def __init__(self):
        self.ql           = QuantLibEngine(self.p.risk_free_rate, self.p.dividend_yield)
        self.data_close   = self.data.close

        # Track synthetic position state
        self.straddle_open      = False
        self.straddle_strike    = 0.0
        self.straddle_expiry    = None
        self.call_entry_price   = 0.0
        self.put_entry_price    = 0.0
        self.entry_credit       = 0.0
        self.hedge_shares       = 0.0
        self.bars_since_hedge   = 0
        self.straddle_dte_enter = 0

        # Track P&L
        self.total_trades  = 0
        self.winning_trades = 0
        self.pnl_list: list[float] = []

        # Volatility indicators
        self.hv = btind.StandardDeviation(self.data_close, period=self.p.hv_window)
        self.bar_count = 0

    def log(self, txt, dt=None):
        if self.p.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            logger.info(f"[BT] {dt} | {txt}")

    def next(self):
        self.bar_count += 1
        self.bars_since_hedge += 1

        current_price = self.data_close[0]
        current_date  = self.datas[0].datetime.date(0)

        # Compute HV and synthetic IV
        if self.hv[0] is None or self.hv[0] == 0:
            return

        daily_vol   = self.hv[0]
        annual_hv   = daily_vol * math.sqrt(252) / current_price  # rough annualization
        atm_iv      = annual_hv * self.p.iv_premium

        # ── Position Management ─────────────────────────────────────────────
        if self.straddle_open:
            dte_remaining = (self.straddle_expiry - current_date).days

            # Price current straddle
            straddle = self.ql.price_straddle(
                spot        = current_price,
                strike      = self.straddle_strike,
                expiry_date = self.straddle_expiry,
                call_vol    = atm_iv,
                put_vol     = atm_iv,
                n_contracts = self.p.n_contracts,
            )
            current_value = straddle.net_price * -1  # cost to close (we're short)

            unrealized_pnl = self.entry_credit - abs(current_value)
            pnl_pct        = unrealized_pnl / self.entry_credit if self.entry_credit else 0

            # Exit checks
            exit_reason = None

            if pnl_pct >= self.p.profit_target_pct:
                exit_reason = f"profit_target ({pnl_pct:.1%})"
            elif abs(current_value) >= self.entry_credit * self.p.max_loss_mult:
                exit_reason = f"loss_limit ({pnl_pct:.1%})"
            elif dte_remaining <= self.p.dte_exit:
                exit_reason = f"dte_exit ({dte_remaining} days)"

            if exit_reason:
                self._close_straddle(current_price, unrealized_pnl, exit_reason)
                return

            # Delta hedge check
            net_delta = straddle.net_delta + self.hedge_shares
            if (abs(net_delta) > self.p.delta_threshold and
                    self.bars_since_hedge >= self.p.rehedge_bars):
                self._rehedge(net_delta, current_price, straddle)

        # ── Entry Logic ─────────────────────────────────────────────────────
        else:
            # Simple IV rank proxy: use position of current IV vs recent range
            # In backtest we approximate with recent HV range
            iv_rank = self._compute_iv_rank_proxy(atm_iv, annual_hv)

            if iv_rank >= self.p.iv_rank_min:
                self._enter_straddle(current_price, current_date, atm_iv)

    def _enter_straddle(self, spot: float, today: date, iv: float):
        """Open a synthetic short straddle."""
        strike = round(spot)
        expiry = today + timedelta(days=self.p.dte_target)

        straddle = self.ql.price_straddle(
            spot        = spot,
            strike      = strike,
            expiry_date = expiry,
            call_vol    = iv,
            put_vol     = iv,
            n_contracts = self.p.n_contracts,
        )

        self.straddle_open    = True
        self.straddle_strike  = strike
        self.straddle_expiry  = expiry
        self.call_entry_price = straddle.call.price
        self.put_entry_price  = straddle.put.price
        self.entry_credit     = straddle.net_price * -1  # invert: we receive credit
        self.hedge_shares     = 0.0
        self.bars_since_hedge = 0
        self.straddle_dte_enter = self.p.dte_target

        self.log(
            f"ENTER STRADDLE | K={strike} exp={expiry} "
            f"credit=${self.entry_credit:.2f} IV={iv:.2%}"
        )

    def _close_straddle(self, spot: float, pnl: float, reason: str):
        """Close the synthetic straddle."""
        # Close hedge shares (buy back if short, sell if long)
        if self.hedge_shares != 0:
            pnl += self.hedge_shares * spot  # simplified PnL on hedge

        self.straddle_open    = False
        self.straddle_strike  = 0.0
        self.straddle_expiry  = None
        self.hedge_shares     = 0.0
        self.total_trades    += 1

        if pnl > 0:
            self.winning_trades += 1
        self.pnl_list.append(pnl)

        self.log(f"EXIT STRADDLE | P&L=${pnl:+.2f} | {reason}")

    def _rehedge(self, net_delta: float, spot: float, straddle):
        """Adjust hedge shares."""
        shares_to_trade = -round(net_delta)
        self.hedge_shares += shares_to_trade
        self.bars_since_hedge = 0

        side = "BUY" if shares_to_trade > 0 else "SELL"
        self.log(
            f"HEDGE | {side} {abs(shares_to_trade)} SPY | "
            f"net_Δ={net_delta:.4f} → new_Δ≈{net_delta + shares_to_trade:.4f}"
        )

    def _compute_iv_rank_proxy(self, current_iv: float, current_hv: float) -> float:
        """Proxy IV rank from recent vol data. Returns 0-100."""
        # Simplified: use vol premium as a proxy
        # Higher vol premium = higher IV rank (elevated IV vs realized)
        premium_ratio = current_iv / max(current_hv, 0.001)
        rank = min(100, max(0, (premium_ratio - 1.0) * 200))
        return rank

    def stop(self):
        """Called at end of backtest."""
        win_rate = self.winning_trades / max(self.total_trades, 1)
        total_pnl = sum(self.pnl_list)
        avg_pnl   = total_pnl / max(len(self.pnl_list), 1)

        self.log("=" * 60)
        self.log(f"BACKTEST COMPLETE")
        self.log(f"Total Trades:  {self.total_trades}")
        self.log(f"Winning Trades: {self.winning_trades} ({win_rate:.1%})")
        self.log(f"Total P&L:     ${total_pnl:+.2f}")
        self.log(f"Avg P&L/Trade: ${avg_pnl:+.2f}")
        self.log("=" * 60)
