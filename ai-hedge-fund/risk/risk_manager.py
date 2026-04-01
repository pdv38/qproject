"""
risk/risk_manager.py
Portfolio-level risk controls: stop-losses, Greeks limits, position sizing.
Acts as a hard-limit layer ABOVE Claude's decisions.
"""

import logging
from config.settings import (
    MAX_LOSS_USD, MAX_GAMMA_EXPOSURE, MAX_VEGA_EXPOSURE,
    MAX_PORTFOLIO_DELTA, PROFIT_TARGET_PCT, MAX_LOSS_MULT, DTE_EXIT
)
from core.portfolio import Portfolio

logger = logging.getLogger(__name__)


class RiskViolation(Exception):
    """Raised when a hard risk limit is breached."""
    pass


class RiskManager:
    """
    Hard-coded risk limits that override ALL decisions including Claude.
    These are absolute circuit breakers.
    """

    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio

    def check_all(self, dte_remaining: int) -> list[dict]:
        """
        Run all risk checks. Returns list of violations.
        Each violation: {'type': str, 'severity': 'HARD'|'SOFT', 'message': str}
        """
        violations = []

        if self.portfolio.straddle is None:
            return violations

        s   = self.portfolio.straddle
        pnl = s.unrealized_pnl

        # ── Hard limits (require immediate exit) ──────────────────────────────

        if pnl <= -MAX_LOSS_USD:
            violations.append({
                "type":     "MAX_LOSS",
                "severity": "HARD",
                "message":  f"P&L ${pnl:.2f} breached hard stop ${-MAX_LOSS_USD:.2f}",
            })

        if s.current_value >= s.entry_credit * MAX_LOSS_MULT:
            violations.append({
                "type":     "LOSS_MULTIPLE",
                "severity": "HARD",
                "message":  (
                    f"Position cost to close ${s.current_value:.2f} "
                    f"exceeds {MAX_LOSS_MULT}x entry credit ${s.entry_credit:.2f}"
                ),
            })

        if dte_remaining <= DTE_EXIT:
            violations.append({
                "type":     "DTE_TOO_LOW",
                "severity": "HARD",
                "message":  f"DTE {dte_remaining} <= exit threshold {DTE_EXIT}",
            })

        if abs(self.portfolio.net_delta) > MAX_PORTFOLIO_DELTA:
            violations.append({
                "type":     "DELTA_LIMIT",
                "severity": "HARD",
                "message":  f"Net delta {self.portfolio.net_delta:.4f} > limit {MAX_PORTFOLIO_DELTA}",
            })

        # ── Soft limits (inform Claude, allow discretion) ─────────────────────

        if abs(s.net_gamma) > MAX_GAMMA_EXPOSURE:
            violations.append({
                "type":     "GAMMA_ELEVATED",
                "severity": "SOFT",
                "message":  f"Net gamma {s.net_gamma:.2f} > {MAX_GAMMA_EXPOSURE} (consider reducing)",
            })

        if abs(s.net_vega) > MAX_VEGA_EXPOSURE:
            violations.append({
                "type":     "VEGA_ELEVATED",
                "severity": "SOFT",
                "message":  f"Net vega {s.net_vega:.2f} > {MAX_VEGA_EXPOSURE} (vol risk elevated)",
            })

        if s.pnl_pct >= PROFIT_TARGET_PCT:
            violations.append({
                "type":     "PROFIT_TARGET",
                "severity": "SOFT",
                "message":  f"P&L at {s.pnl_pct:.1%} of credit — profit target reached (50% rule)",
            })

        for v in violations:
            level = logging.ERROR if v["severity"] == "HARD" else logging.WARNING
            logger.log(level, f"[{v['severity']}] {v['type']}: {v['message']}")

        return violations

    def has_hard_violations(self, dte_remaining: int) -> bool:
        violations = self.check_all(dte_remaining)
        return any(v["severity"] == "HARD" for v in violations)

    def position_size(self, account_equity: float, atm_price: float, n_contracts: int = 1) -> dict:
        """
        Validate and compute safe position size.
        Short straddle max loss is theoretically unlimited — cap by buying power.

        Returns {'approved': bool, 'n_contracts': int, 'max_loss_est': float}
        """
        # Estimated max loss for short straddle = 3x premium (rough heuristic)
        estimated_max_loss = atm_price * 100 * n_contracts * 3
        buying_power_pct   = estimated_max_loss / account_equity

        if buying_power_pct > 0.20:  # Don't risk > 20% equity
            n_contracts = max(1, int(account_equity * 0.20 / (atm_price * 100 * 3)))
            logger.warning(
                f"Position size reduced to {n_contracts} contract(s) "
                f"(20% equity limit)"
            )

        return {
            "approved":      n_contracts > 0,
            "n_contracts":   n_contracts,
            "max_loss_est":  atm_price * 100 * n_contracts * 3,
            "equity_at_risk_pct": (atm_price * 100 * n_contracts * 3) / account_equity,
        }
