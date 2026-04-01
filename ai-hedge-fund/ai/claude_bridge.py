"""
ai/claude_bridge.py
Claude API autonomous decision layer.
Claude receives full portfolio state + news + Greeks and makes ALL decisions.
Decisions: ENTRY, HEDGE, EXIT, OVERRIDE (black swan), SIZE.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import anthropic

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, AI_DECISION_LOG
from ai.prompts import (
    SYSTEM_PROMPT,
    build_entry_prompt,
    build_hedge_prompt,
    build_exit_prompt,
    build_override_prompt,
)

logger = logging.getLogger(__name__)


class Decision:
    """Structured decision returned by Claude."""
    def __init__(self, action: str, confidence: float, reasoning: str, params: dict):
        self.action     = action       # e.g. "ENTER", "SKIP", "HEDGE", "EXIT", "OVERRIDE"
        self.confidence = confidence   # 0.0 - 1.0
        self.reasoning  = reasoning
        self.params     = params       # action-specific params
        self.timestamp  = datetime.utcnow().isoformat()

    def __repr__(self):
        return f"Decision({self.action} conf={self.confidence:.2f} | {self.reasoning[:60]}...)"

    def to_dict(self) -> dict:
        return {
            "action":     self.action,
            "confidence": self.confidence,
            "reasoning":  self.reasoning,
            "params":     self.params,
            "timestamp":  self.timestamp,
        }


class ClaudeBridge:
    """
    Autonomous AI decision layer using Claude.
    All decisions flow through here — Claude has full context and acts autonomously.
    """

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        logger.info(f"Claude bridge initialized | model={CLAUDE_MODEL}")

    # ── Core Decision Method ───────────────────────────────────────────────────

    def _call_claude(self, user_prompt: str, decision_type: str) -> Decision:
        """
        Send a structured prompt to Claude and parse the JSON decision response.
        """
        logger.info(f"🤖 Querying Claude | decision_type={decision_type}")

        try:
            response = self.client.messages.create(
                model      = CLAUDE_MODEL,
                max_tokens = CLAUDE_MAX_TOKENS,
                system     = SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": user_prompt}],
            )

            raw = response.content[0].text.strip()
            logger.debug(f"Claude raw response: {raw}")

            # Parse JSON response
            # Claude is instructed to respond ONLY with JSON
            decision_data = json.loads(raw)

            decision = Decision(
                action     = decision_data.get("action", "SKIP"),
                confidence = float(decision_data.get("confidence", 0.5)),
                reasoning  = decision_data.get("reasoning", ""),
                params     = decision_data.get("params", {}),
            )

            logger.info(f"Claude decision: {decision}")
            self._log_decision(decision_type, user_prompt, decision)
            return decision

        except json.JSONDecodeError as e:
            logger.error(f"Claude JSON parse error: {e} | Raw: {raw[:200]}")
            return Decision("SKIP", 0.0, f"Parse error: {e}", {})
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return Decision("SKIP", 0.0, f"API error: {e}", {})

    # ── Decision Types ─────────────────────────────────────────────────────────

    def decide_entry(
        self,
        spot:           float,
        strike:         float,
        expiry:         str,
        atm_iv:         float,
        iv_rank:        float,
        hist_vol:       float,
        straddle_greeks: dict,
        news_summary:   str,
        account:        dict,
    ) -> Decision:
        """
        Should we enter a new short straddle?
        Returns Decision with action = 'ENTER' | 'SKIP'
        """
        prompt = build_entry_prompt(
            spot=spot,
            strike=strike,
            expiry=expiry,
            atm_iv=atm_iv,
            iv_rank=iv_rank,
            hist_vol=hist_vol,
            straddle_greeks=straddle_greeks,
            news_summary=news_summary,
            account=account,
        )
        return self._call_claude(prompt, "ENTRY")

    def decide_hedge(
        self,
        net_delta:       float,
        net_gamma:       float,
        net_vega:        float,
        spot:            float,
        hedge_shares:    float,
        unrealized_pnl:  float,
        news_summary:    str,
        minutes_since_last_hedge: int,
    ) -> Decision:
        """
        Should we rehedge? How many shares?
        Returns Decision with action = 'HEDGE' | 'HOLD'
        params: {'shares': int, 'side': 'buy'|'sell'}
        """
        prompt = build_hedge_prompt(
            net_delta=net_delta,
            net_gamma=net_gamma,
            net_vega=net_vega,
            spot=spot,
            hedge_shares=hedge_shares,
            unrealized_pnl=unrealized_pnl,
            news_summary=news_summary,
            minutes_since_last_hedge=minutes_since_last_hedge,
        )
        return self._call_claude(prompt, "HEDGE")

    def decide_exit(
        self,
        portfolio_summary: dict,
        straddle_greeks:   dict,
        dte_remaining:     int,
        news_summary:      str,
        entry_credit:      float,
        current_value:     float,
        max_loss_usd:      float,
    ) -> Decision:
        """
        Should we close the straddle?
        Returns Decision with action = 'EXIT' | 'HOLD'
        params: {'reason': str}
        """
        prompt = build_exit_prompt(
            portfolio_summary=portfolio_summary,
            straddle_greeks=straddle_greeks,
            dte_remaining=dte_remaining,
            news_summary=news_summary,
            entry_credit=entry_credit,
            current_value=current_value,
            max_loss_usd=max_loss_usd,
        )
        return self._call_claude(prompt, "EXIT")

    def decide_override(
        self,
        breaking_news:   str,
        portfolio_summary: dict,
        straddle_greeks: dict,
        spot:            float,
    ) -> Decision:
        """
        Black swan / circuit breaker — triggered on extreme news.
        Returns Decision with action = 'FLATTEN' | 'REDUCE' | 'HOLD'
        """
        prompt = build_override_prompt(
            breaking_news=breaking_news,
            portfolio_summary=portfolio_summary,
            straddle_greeks=straddle_greeks,
            spot=spot,
        )
        return self._call_claude(prompt, "OVERRIDE")

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_decision(self, decision_type: str, prompt: str, decision: Decision):
        """Append decisions to JSONL log for audit trail."""
        try:
            with open(AI_DECISION_LOG, "a") as f:
                log_entry = {
                    "type":     decision_type,
                    "decision": decision.to_dict(),
                    "prompt_preview": prompt[:300],
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.warning(f"Decision log write failed: {e}")
