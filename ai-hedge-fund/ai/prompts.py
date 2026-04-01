"""
ai/prompts.py
Structured prompts for each Claude decision type.
Claude responds ONLY in JSON — never prose.
"""

import json


# ── System Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the autonomous AI portfolio manager of an options hedge fund.
You specialize in delta-neutral short straddle strategies on SPY.

Your role is to make REAL trading decisions with real capital (paper trading).
You have full authority over: entries, exits, hedge sizing, and risk overrides.

STRATEGY CONTEXT:
- You sell ATM straddles on SPY (short call + short put, same strike, same expiry)
- You earn theta (time decay) as premium sellers
- You dynamically delta-hedge using SPY shares to stay neutral
- You close positions at 50% max profit or 2x loss of credit received
- Target DTE: 21-45 days. Never hold through expiry.

DECISION FRAMEWORK:
1. IV Rank > 40 is generally favorable for straddle entry (elevated premium)
2. Avoid entry before major known events (FOMC, CPI, earnings)
3. Re-hedge when |net_delta| > 0.10 (threshold) or on extreme news
4. News sentiment matters: bearish/volatile news → wider hedges, consider exit
5. Gamma risk spikes near expiry — reduce exposure < 7 DTE

RESPONSE FORMAT:
You MUST respond with ONLY a valid JSON object. No prose, no markdown, no explanation outside JSON.
Always include: action, confidence (0.0-1.0), reasoning (concise), params (action-specific dict).

Example:
{"action": "ENTER", "confidence": 0.82, "reasoning": "IV rank at 67 is elevated. News is benign. Risk/reward favorable.", "params": {"n_contracts": 1}}
"""


# ── Entry Decision ─────────────────────────────────────────────────────────────

def build_entry_prompt(
    spot, strike, expiry, atm_iv, iv_rank, hist_vol,
    straddle_greeks, news_summary, account
) -> str:
    return f"""DECISION REQUEST: STRADDLE ENTRY

MARKET DATA:
- SPY Spot: ${spot:.2f}
- ATM Strike: ${strike:.2f}
- Expiry: {expiry}
- ATM Implied Volatility: {atm_iv:.2%}
- IV Rank (0-100): {iv_rank:.1f}
- Historical 21-day Realized Vol: {hist_vol:.2%}
- Vol Premium (IV - HV): {(atm_iv - hist_vol):.2%}

STRADDLE GREEKS (short position, 1 contract):
{json.dumps(straddle_greeks, indent=2)}

NEWS SENTIMENT (last 2 hours):
{news_summary}

ACCOUNT:
- Equity: ${account.get('equity', 0):,.2f}
- Buying Power: ${account.get('buying_power', 0):,.2f}
- Current Positions: {account.get('positions', 0)}

YOUR TASK:
Decide whether to ENTER a short ATM straddle.
- action: "ENTER" or "SKIP"
- If ENTER: include n_contracts (1 recommended) in params
- Consider: IV rank, vol premium, news risk, gamma exposure

Respond ONLY with JSON.
"""


# ── Hedge Decision ─────────────────────────────────────────────────────────────

def build_hedge_prompt(
    net_delta, net_gamma, net_vega, spot,
    hedge_shares, unrealized_pnl, news_summary,
    minutes_since_last_hedge
) -> str:
    direction = "LONG biased" if net_delta > 0 else "SHORT biased"
    return f"""DECISION REQUEST: DELTA HEDGE

CURRENT GREEKS (full portfolio):
- Net Delta: {net_delta:.4f} ({direction})
- Net Gamma: {net_gamma:.4f}
- Net Vega: {net_vega:.4f}
- SPY Spot: ${spot:.2f}
- Current Hedge (shares): {hedge_shares:+.0f}
- Unrealized P&L: ${unrealized_pnl:+.2f}
- Minutes since last hedge: {minutes_since_last_hedge}

DELTA THRESHOLD: 0.10 (re-hedge if |net_delta| > threshold)
CURRENT |NET DELTA|: {abs(net_delta):.4f} → {'⚠️ ABOVE threshold' if abs(net_delta) > 0.10 else '✅ Within threshold'}

NEWS (last 30 min):
{news_summary}

YOUR TASK:
Decide whether to hedge delta exposure now.
- action: "HEDGE" or "HOLD"
- If HEDGE: params must include "shares" (integer, positive=buy, negative=sell) and "side" ("buy" or "sell")
- Shares needed to neutralize: {-round(net_delta * 100)} (approx, 1 contract = 100 shares delta)
- Consider: news risk (hedge more aggressively if volatile news), transaction cost (avoid micro-hedges < 5 shares)

Respond ONLY with JSON.
"""


# ── Exit Decision ──────────────────────────────────────────────────────────────

def build_exit_prompt(
    portfolio_summary, straddle_greeks, dte_remaining,
    news_summary, entry_credit, current_value, max_loss_usd
) -> str:
    pnl         = entry_credit - current_value
    pnl_pct     = pnl / entry_credit if entry_credit else 0
    profit_tgt  = entry_credit * 0.50
    loss_limit  = entry_credit * 2.0

    return f"""DECISION REQUEST: STRADDLE EXIT

POSITION STATUS:
- Entry Credit: ${entry_credit:.2f}
- Current Value (cost to close): ${current_value:.2f}
- Unrealized P&L: ${pnl:+.2f} ({pnl_pct:+.1%} of credit)
- DTE Remaining: {dte_remaining} days
- Profit Target (50%): ${profit_tgt:.2f} → {'✅ REACHED' if pnl >= profit_tgt else f'${profit_tgt - pnl:.2f} away'}
- Max Loss Limit (2x): ${loss_limit:.2f} → {'🛑 BREACHED' if current_value >= loss_limit else 'OK'}
- Hard Stop Loss: ${max_loss_usd:.2f}

GREEKS:
{json.dumps(straddle_greeks, indent=2)}

PORTFOLIO:
{json.dumps(portfolio_summary, indent=2)}

NEWS:
{news_summary}

EXIT RULES (consider all):
1. P&L >= 50% of entry credit → STRONG EXIT signal
2. Cost to close >= 2x entry credit → EXIT (stop loss)
3. DTE <= 7 → EXIT (gamma risk, avoid pin risk)
4. Extreme negative news → EXIT or REDUCE
5. Net gamma very high → consider EXIT to reduce risk

YOUR TASK:
- action: "EXIT" or "HOLD"
- If EXIT: params include "reason" string
- Be willing to take profits early (50% rule is golden)
- Never hold through extreme tail events

Respond ONLY with JSON.
"""


# ── Override / Circuit Breaker ─────────────────────────────────────────────────

def build_override_prompt(
    breaking_news, portfolio_summary, straddle_greeks, spot
) -> str:
    return f"""DECISION REQUEST: EMERGENCY OVERRIDE (CIRCUIT BREAKER)

⚠️ BREAKING NEWS DETECTED — IMMEDIATE ASSESSMENT REQUIRED ⚠️

BREAKING NEWS:
{breaking_news}

PORTFOLIO EXPOSURE:
{json.dumps(portfolio_summary, indent=2)}

GREEKS:
{json.dumps(straddle_greeks, indent=2)}

SPY SPOT: ${spot:.2f}

OVERRIDE OPTIONS:
- "FLATTEN": Close ALL positions immediately (straddle + hedge). Maximum safety.
- "REDUCE": Close straddle but keep hedge shares. Partial risk reduction.
- "HOLD": News does not warrant action. Continue normal operation.

YOUR TASK:
Assess if this news creates a tail risk scenario that warrants emergency action.
Consider: Fed announcements, geopolitical events, financial contagion, flash crashes.
Short straddles are most at risk from large directional moves.

- action: "FLATTEN" | "REDUCE" | "HOLD"
- confidence: how certain you are of your assessment
- reasoning: brief explanation of the risk assessment
- params: {} for all options

This is a CRITICAL decision. When in doubt, flatten. Capital preservation first.

Respond ONLY with JSON.
"""
