"""
core/portfolio.py
Live portfolio state — tracks straddle positions, hedge shares, P&L, Greeks.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StraddlePosition:
    """Represents an open short straddle position."""
    symbol:          str
    strike:          float
    expiry:          str
    call_symbol:     str
    put_symbol:      str
    n_contracts:     int

    # Entry prices
    call_entry_price: float = 0.0
    put_entry_price:  float = 0.0
    entry_credit:     float = 0.0     # total credit received (call + put) * 100 * n

    # Current
    call_price:      float = 0.0
    put_price:       float = 0.0
    current_value:   float = 0.0      # current cost to close

    # Greeks (per straddle, scaled)
    net_delta:       float = 0.0
    net_gamma:       float = 0.0
    net_vega:        float = 0.0
    net_theta:       float = 0.0

    entry_time:      datetime = field(default_factory=datetime.utcnow)
    is_open:         bool = True

    @property
    def unrealized_pnl(self) -> float:
        """Positive = profit for short straddle (credit - current cost to close)."""
        return self.entry_credit - self.current_value

    @property
    def pnl_pct(self) -> float:
        if self.entry_credit == 0:
            return 0.0
        return self.unrealized_pnl / self.entry_credit

    def __repr__(self):
        return (
            f"StraddlePosition({self.symbol} K={self.strike} "
            f"exp={self.expiry} P&L=${self.unrealized_pnl:.2f} "
            f"[{self.pnl_pct:.1%}])"
        )


@dataclass
class HedgePosition:
    """SPY share position used for delta hedging."""
    shares:     float = 0.0      # positive = long, negative = short
    avg_price:  float = 0.0
    last_hedge: Optional[datetime] = None

    @property
    def delta(self) -> float:
        return self.shares  # 1 share = delta of 1.0


class Portfolio:
    """
    Live portfolio state manager.
    Tracks straddle, hedge shares, realized/unrealized P&L.
    """

    def __init__(self):
        self.straddle:     Optional[StraddlePosition] = None
        self.hedge:        HedgePosition = HedgePosition()
        self.realized_pnl: float = 0.0
        self.trades:       list[dict] = []
        logger.info("Portfolio initialized.")

    # ── Straddle Management ────────────────────────────────────────────────────

    def open_straddle(
        self,
        symbol:           str,
        strike:           float,
        expiry:           str,
        call_symbol:      str,
        put_symbol:       str,
        call_entry_price: float,
        put_entry_price:  float,
        n_contracts:      int = 1,
    ):
        credit = (call_entry_price + put_entry_price) * 100 * n_contracts
        self.straddle = StraddlePosition(
            symbol=symbol,
            strike=strike,
            expiry=expiry,
            call_symbol=call_symbol,
            put_symbol=put_symbol,
            n_contracts=n_contracts,
            call_entry_price=call_entry_price,
            put_entry_price=put_entry_price,
            entry_credit=credit,
            call_price=call_entry_price,
            put_price=put_entry_price,
            current_value=credit,
        )
        logger.info(f"📥 Opened straddle: {self.straddle} | Credit=${credit:.2f}")
        self._log_trade("open_straddle", credit=credit, details=str(self.straddle))

    def update_straddle_prices(
        self,
        call_price:  float,
        put_price:   float,
        net_delta:   float,
        net_gamma:   float,
        net_vega:    float,
        net_theta:   float,
    ):
        if not self.straddle:
            return
        self.straddle.call_price   = call_price
        self.straddle.put_price    = put_price
        self.straddle.current_value = (call_price + put_price) * 100 * self.straddle.n_contracts
        self.straddle.net_delta    = net_delta
        self.straddle.net_gamma    = net_gamma
        self.straddle.net_vega     = net_vega
        self.straddle.net_theta    = net_theta

    def close_straddle(self, reason: str = ""):
        if not self.straddle:
            return 0.0
        pnl = self.straddle.unrealized_pnl
        self.realized_pnl += pnl
        self.straddle.is_open = False
        logger.info(f"📤 Closed straddle | P&L=${pnl:.2f} | Reason: {reason}")
        self._log_trade("close_straddle", pnl=pnl, reason=reason)
        self.straddle = None
        return pnl

    # ── Hedge Management ───────────────────────────────────────────────────────

    def update_hedge(self, shares_traded: float, price: float):
        """
        Update hedge position after trading shares.
        Positive shares_traded = bought, negative = sold.
        """
        if self.hedge.shares == 0:
            self.hedge.avg_price = price
        else:
            # Update weighted average price
            total_shares = self.hedge.shares + shares_traded
            if total_shares != 0:
                self.hedge.avg_price = (
                    (self.hedge.shares * self.hedge.avg_price + shares_traded * price)
                    / total_shares
                )
        self.hedge.shares += shares_traded
        self.hedge.last_hedge = datetime.utcnow()
        logger.info(
            f"🔄 Hedge updated | Net shares: {self.hedge.shares:+.0f} @ ${price:.2f}"
        )

    def close_hedge(self, price: float):
        """Close all hedge shares and realize P&L."""
        if self.hedge.shares == 0:
            return 0.0
        pnl = self.hedge.shares * (price - self.hedge.avg_price)
        self.realized_pnl += pnl
        logger.info(f"Hedge closed | P&L=${pnl:.2f}")
        self._log_trade("close_hedge", pnl=pnl)
        self.hedge.shares    = 0.0
        self.hedge.avg_price = 0.0
        return pnl

    # ── Portfolio Aggregates ───────────────────────────────────────────────────

    @property
    def net_delta(self) -> float:
        """Net portfolio delta = straddle delta + hedge delta."""
        straddle_delta = self.straddle.net_delta if self.straddle else 0.0
        return straddle_delta + self.hedge.delta

    @property
    def unrealized_pnl(self) -> float:
        return self.straddle.unrealized_pnl if self.straddle else 0.0

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def summary(self) -> dict:
        return {
            "has_straddle":    self.straddle is not None,
            "straddle":        str(self.straddle) if self.straddle else None,
            "hedge_shares":    self.hedge.shares,
            "net_delta":       round(self.net_delta, 4),
            "unrealized_pnl":  round(self.unrealized_pnl, 2),
            "realized_pnl":    round(self.realized_pnl, 2),
            "total_pnl":       round(self.total_pnl, 2),
        }

    def _log_trade(self, action: str, **kwargs):
        self.trades.append({
            "time":   datetime.utcnow().isoformat(),
            "action": action,
            **kwargs
        })
