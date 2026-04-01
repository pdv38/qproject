"""
strategies/straddle.py
ATM short straddle strategy logic.
Handles entry scanning, expiry selection, and exit coordination.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from config.settings import (
    SYMBOL, DTE_TARGET, DTE_MIN, NUM_CONTRACTS,
    IV_RANK_MIN, IV_RANK_MAX
)
from core.alpaca_client import AlpacaClient
from core.quantlib_engine import QuantLibEngine
from core.portfolio import Portfolio
from ai.claude_bridge import ClaudeBridge
from news.alpaca_news import AlpacaNewsClient
from risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class StraddleStrategy:
    """
    Manages the lifecycle of an ATM short straddle position.
    Entry/exit logic coordinated with Claude and QuantLib.
    """

    def __init__(
        self,
        alpaca:       AlpacaClient,
        portfolio:    Portfolio,
        ql:           QuantLibEngine,
        claude:       ClaudeBridge,
        news:         AlpacaNewsClient,
        risk_manager: RiskManager,
    ):
        self.alpaca        = alpaca
        self.portfolio     = portfolio
        self.ql            = ql
        self.claude        = claude
        self.news          = news
        self.risk_manager  = risk_manager

    # ── Entry ──────────────────────────────────────────────────────────────────

    def scan_and_enter(self) -> dict:
        """
        Scan for ATM straddle opportunity and enter if Claude approves.
        Returns result dict.
        """
        if self.portfolio.straddle is not None:
            logger.info("Already have an open straddle — skipping scan.")
            return {"action": "SKIP", "reason": "already_in_position"}

        spot = self.alpaca.get_latest_price(SYMBOL)

        # Find ATM straddle details
        straddle_info = self.alpaca.find_atm_straddle(SYMBOL, DTE_TARGET)
        strike = straddle_info["strike"]
        expiry = date.fromisoformat(straddle_info["target_expiry"])

        # Compute ATM IV via historical vol proxy (replace with live options data)
        price_history = self.alpaca.get_iv_history(SYMBOL, days=60)
        hist_vol      = self.ql.compute_historical_vol(price_history, window=21)

        # Use HV + vol risk premium as proxy for ATM IV
        # In production, pull real ATM IV from options chain
        atm_iv    = hist_vol * 1.15   # typical IV > HV premium
        iv_rank   = self.ql.compute_iv_rank(atm_iv, price_history[-252:] if len(price_history) >= 252 else price_history)

        logger.info(
            f"Straddle scan | SPY=${spot:.2f} K={strike} "
            f"exp={expiry} IV={atm_iv:.2%} IVR={iv_rank:.1f}"
        )

        # Pre-screen: IV rank too low → don't bother asking Claude
        if iv_rank < IV_RANK_MIN:
            logger.info(f"IV rank {iv_rank:.1f} < min {IV_RANK_MIN} — skipping")
            return {"action": "SKIP", "reason": f"iv_rank_too_low ({iv_rank:.1f})"}

        if iv_rank > IV_RANK_MAX:
            logger.warning(f"IV rank {iv_rank:.1f} > max {IV_RANK_MAX} — too much event risk")
            return {"action": "SKIP", "reason": f"iv_rank_too_high ({iv_rank:.1f})"}

        # Price the straddle with QuantLib
        straddle_greeks = self.ql.price_straddle(
            spot=spot,
            strike=strike,
            expiry_date=expiry,
            call_vol=atm_iv,
            put_vol=atm_iv,
            n_contracts=NUM_CONTRACTS,
        )

        # Risk check: position sizing
        account = self.alpaca.get_account()
        size_check = self.risk_manager.position_size(
            account_equity=account["equity"],
            atm_price=straddle_greeks.call.price + straddle_greeks.put.price,
            n_contracts=NUM_CONTRACTS,
        )

        if not size_check["approved"]:
            return {"action": "SKIP", "reason": "position_size_not_approved"}

        # Get news summary
        news_summary = self.news.get_summary_for_claude(hours=4)

        # Ask Claude
        decision = self.claude.decide_entry(
            spot=spot,
            strike=strike,
            expiry=straddle_info["target_expiry"],
            atm_iv=atm_iv,
            iv_rank=iv_rank,
            hist_vol=hist_vol,
            straddle_greeks=straddle_greeks.to_dict(),
            news_summary=news_summary,
            account={**account, "positions": len(self.alpaca.get_positions())},
        )

        if decision.action != "ENTER":
            logger.info(f"Claude SKIP entry: {decision.reasoning}")
            return {"action": "SKIP", "reason": decision.reasoning}

        if decision.confidence < 0.60:
            logger.info(f"Claude low confidence ({decision.confidence:.2f}) — skipping")
            return {"action": "SKIP", "reason": "low_confidence"}

        # Execute
        return self._execute_entry(straddle_info, straddle_greeks, n_contracts=size_check["n_contracts"])

    def _execute_entry(self, straddle_info: dict, straddle_greeks, n_contracts: int) -> dict:
        """Submit call and put sell orders."""
        logger.info(f"🚀 Entering straddle: {straddle_info['call_symbol']} + {straddle_info['put_symbol']}")

        call_order = self.alpaca.submit_option_order(
            option_symbol=straddle_info["call_symbol"],
            qty=n_contracts,
            side="sell",
            action="SELL TO OPEN ATM CALL",
        )
        put_order = self.alpaca.submit_option_order(
            option_symbol=straddle_info["put_symbol"],
            qty=n_contracts,
            side="sell",
            action="SELL TO OPEN ATM PUT",
        )

        # Register in portfolio
        self.portfolio.open_straddle(
            symbol=straddle_info["symbol"],
            strike=straddle_info["strike"],
            expiry=straddle_info["target_expiry"],
            call_symbol=straddle_info["call_symbol"],
            put_symbol=straddle_info["put_symbol"],
            call_entry_price=straddle_greeks.call.price,
            put_entry_price=straddle_greeks.put.price,
            n_contracts=n_contracts,
        )

        return {
            "action":     "ENTERED",
            "straddle":   str(self.portfolio.straddle),
            "call_order": call_order,
            "put_order":  put_order,
            "credit":     self.portfolio.straddle.entry_credit,
        }

    # ── Exit ───────────────────────────────────────────────────────────────────

    def check_and_exit(self, atm_iv: float) -> dict:
        """
        Check if straddle should be exited.
        Combines hard risk limits + Claude's judgment.
        """
        if not self.portfolio.straddle:
            return {"action": "SKIP", "reason": "no_position"}

        s = self.portfolio.straddle
        expiry = date.fromisoformat(s.expiry)
        dte    = (expiry - date.today()).days

        # Hard risk violations override Claude
        if self.risk_manager.has_hard_violations(dte):
            logger.warning("⚠️ Hard risk limit breached — forcing exit")
            return self._execute_exit(reason="hard_risk_limit")

        # Refresh Greeks
        straddle_greeks = self.ql.price_straddle(
            spot=self.alpaca.get_latest_price(SYMBOL),
            strike=s.strike,
            expiry_date=expiry,
            call_vol=atm_iv,
            put_vol=atm_iv,
            n_contracts=s.n_contracts,
        )

        news_summary = self.news.get_summary_for_claude(hours=1)

        decision = self.claude.decide_exit(
            portfolio_summary=self.portfolio.summary(),
            straddle_greeks=straddle_greeks.to_dict(),
            dte_remaining=dte,
            news_summary=news_summary,
            entry_credit=s.entry_credit,
            current_value=s.current_value,
            max_loss_usd=self.risk_manager.portfolio._max_loss,
        )

        if decision.action == "EXIT":
            return self._execute_exit(reason=decision.reasoning)

        logger.info(f"Claude HOLD position: {decision.reasoning}")
        return {"action": "HOLD", "reason": decision.reasoning}

    def _execute_exit(self, reason: str = "") -> dict:
        """Submit buy-to-close orders for straddle."""
        if not self.portfolio.straddle:
            return {"action": "SKIP"}

        s = self.portfolio.straddle
        logger.info(f"📤 Closing straddle: {s} | Reason: {reason}")

        call_order = self.alpaca.submit_option_order(
            option_symbol=s.call_symbol,
            qty=s.n_contracts,
            side="buy",
            action="BUY TO CLOSE CALL",
        )
        put_order = self.alpaca.submit_option_order(
            option_symbol=s.put_symbol,
            qty=s.n_contracts,
            side="buy",
            action="BUY TO CLOSE PUT",
        )

        pnl = self.portfolio.close_straddle(reason=reason)

        # Also close hedge
        spot = self.alpaca.get_latest_price(SYMBOL)
        if abs(self.portfolio.hedge.shares) > 0:
            hedge_side = "sell" if self.portfolio.hedge.shares > 0 else "buy"
            self.alpaca.submit_market_order(
                symbol=SYMBOL,
                qty=abs(self.portfolio.hedge.shares),
                side=hedge_side,
                reason="CLOSE HEDGE — straddle exited",
            )
            self.portfolio.close_hedge(price=spot)

        return {
            "action":     "EXITED",
            "pnl":        pnl,
            "reason":     reason,
            "call_order": call_order,
            "put_order":  put_order,
        }
