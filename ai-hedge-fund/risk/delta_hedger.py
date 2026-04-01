"""
risk/delta_hedger.py
Dynamic delta-neutral rebalancer.
Monitors portfolio delta and submits SPY share orders to neutralize.
Works with Claude AI decisions — Claude approves or overrides hedge sizing.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from config.settings import (
    DELTA_THRESHOLD, HEDGE_INTERVAL_MIN, MAX_PORTFOLIO_DELTA,
    SYMBOL, SHARES_PER_CONTRACT
)
from core.alpaca_client import AlpacaClient
from core.portfolio import Portfolio
from core.quantlib_engine import QuantLibEngine
from ai.claude_bridge import ClaudeBridge
from news.alpaca_news import AlpacaNewsClient

logger = logging.getLogger(__name__)


class DeltaHedger:
    """
    Continuously monitors portfolio delta and rehedges via Claude's direction.
    """

    def __init__(
        self,
        alpaca:    AlpacaClient,
        portfolio: Portfolio,
        ql:        QuantLibEngine,
        claude:    ClaudeBridge,
        news:      AlpacaNewsClient,
    ):
        self.alpaca    = alpaca
        self.portfolio = portfolio
        self.ql        = ql
        self.claude    = claude
        self.news      = news
        self._last_hedge_time: Optional[datetime] = None

    # ── Main Hedge Check ───────────────────────────────────────────────────────

    def check_and_hedge(self) -> dict:
        """
        Main hedge loop entry point.
        1. Compute current portfolio delta
        2. Ask Claude if/how to hedge
        3. Execute hedge if approved

        Returns dict with hedge result.
        """
        if not self.portfolio.straddle:
            logger.debug("No open straddle — no hedge needed.")
            return {"action": "SKIP", "reason": "no_straddle"}

        spot       = self.alpaca.get_latest_price(SYMBOL)
        net_delta  = self.portfolio.net_delta
        net_gamma  = self.portfolio.straddle.net_gamma
        net_vega   = self.portfolio.straddle.net_vega
        pnl        = self.portfolio.unrealized_pnl

        minutes_since = self._minutes_since_last_hedge()
        news_summary  = self.news.get_summary_for_claude(hours=1)

        logger.info(
            f"🔍 Hedge check | net_Δ={net_delta:+.4f} | "
            f"threshold={DELTA_THRESHOLD} | spot=${spot:.2f} | "
            f"mins_since_hedge={minutes_since}"
        )

        # Hard stop: if delta way beyond threshold, force hedge without Claude
        if abs(net_delta) > MAX_PORTFOLIO_DELTA:
            logger.warning(
                f"⚠️ HARD DELTA LIMIT BREACHED ({net_delta:.4f}) — force hedging"
            )
            return self._execute_hedge(
                shares=self.ql.hedge_shares_needed(net_delta, spot),
                spot=spot,
                reason="hard_delta_limit",
            )

        # Minimum interval check (don't over-hedge)
        if minutes_since < HEDGE_INTERVAL_MIN and abs(net_delta) < DELTA_THRESHOLD:
            logger.debug(f"Hedge interval not met ({minutes_since} min) and delta within threshold.")
            return {"action": "HOLD", "reason": "interval_not_met"}

        # Ask Claude
        decision = self.claude.decide_hedge(
            net_delta=net_delta,
            net_gamma=net_gamma,
            net_vega=net_vega,
            spot=spot,
            hedge_shares=self.portfolio.hedge.shares,
            unrealized_pnl=pnl,
            news_summary=news_summary,
            minutes_since_last_hedge=minutes_since,
        )

        if decision.action == "HEDGE":
            shares = decision.params.get("shares", self.ql.hedge_shares_needed(net_delta, spot))
            return self._execute_hedge(shares=shares, spot=spot, reason=decision.reasoning)
        else:
            logger.info(f"Claude HOLD on hedge: {decision.reasoning}")
            return {"action": "HOLD", "reason": decision.reasoning}

    def _execute_hedge(self, shares: int, spot: float, reason: str = "") -> dict:
        """Submit SPY order to hedge delta."""
        if shares == 0:
            logger.info("Hedge shares = 0, no order needed.")
            return {"action": "SKIP", "reason": "zero_shares"}

        side = "buy" if shares > 0 else "sell"
        result = self.alpaca.submit_market_order(
            symbol=SYMBOL,
            qty=abs(shares),
            side=side,
            reason=f"DELTA HEDGE | {reason}",
        )

        if "error" not in result:
            self.portfolio.update_hedge(shares_traded=shares, price=spot)
            self._last_hedge_time = datetime.utcnow()
            logger.info(
                f"✅ Hedge executed: {'+' if shares > 0 else ''}{shares} SPY @ ${spot:.2f}"
            )

        return {
            "action": "HEDGE",
            "shares": shares,
            "side":   side,
            "spot":   spot,
            "order":  result,
            "reason": reason,
        }

    def _minutes_since_last_hedge(self) -> int:
        if not self._last_hedge_time:
            return 9999
        return int((datetime.utcnow() - self._last_hedge_time).total_seconds() / 60)

    # ── Greeks Refresh ─────────────────────────────────────────────────────────

    def refresh_portfolio_greeks(self, atm_iv: float) -> Optional[dict]:
        """
        Recompute straddle Greeks and update portfolio state.
        """
        if not self.portfolio.straddle:
            return None

        from datetime import date
        s = self.portfolio.straddle
        spot   = self.alpaca.get_latest_price(SYMBOL)
        expiry = date.fromisoformat(s.expiry)

        straddle_greeks = self.ql.price_straddle(
            spot=spot,
            strike=s.strike,
            expiry_date=expiry,
            call_vol=atm_iv,
            put_vol=atm_iv,
            n_contracts=s.n_contracts,
        )

        self.portfolio.update_straddle_prices(
            call_price=straddle_greeks.call.price,
            put_price=straddle_greeks.put.price,
            net_delta=straddle_greeks.net_delta,
            net_gamma=straddle_greeks.net_gamma,
            net_vega=straddle_greeks.net_vega,
            net_theta=straddle_greeks.net_theta,
        )

        logger.info(
            f"Greeks refreshed | {straddle_greeks}"
        )
        return straddle_greeks.to_dict()
