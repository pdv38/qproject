"""
config/settings.py
Central configuration for the AI-Native Hedge Fund.
All parameters can be overridden via .env
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Credentials ────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL   = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Strategy Parameters ────────────────────────────────────────────────────────
SYMBOL            = os.getenv("SYMBOL", "SPY")
DTE_TARGET        = int(os.getenv("DTE_TARGET", "30"))       # Target days to expiry
DTE_MIN           = 21                                         # Min DTE to enter
DTE_EXIT          = 7                                          # Exit if DTE falls below

# ── Straddle Parameters ────────────────────────────────────────────────────────
NUM_CONTRACTS     = 1                                          # Number of straddles
IV_RANK_MIN       = float(os.getenv("IV_RANK_MIN", "40"))    # Min IV rank to enter
IV_RANK_MAX       = 85.0                                       # Max IV rank (avoid earnings)
PROFIT_TARGET_PCT = 0.50                                       # Close at 50% max profit
MAX_LOSS_MULT     = 2.0                                        # Close at 2x credit received

# ── Delta Hedging ──────────────────────────────────────────────────────────────
DELTA_THRESHOLD       = float(os.getenv("DELTA_THRESHOLD", "0.10"))
HEDGE_INTERVAL_MIN    = int(os.getenv("HEDGE_INTERVAL_MIN", "15"))
MAX_GAMMA_EXPOSURE    = float(os.getenv("MAX_GAMMA_EXPOSURE", "50"))
SHARES_PER_CONTRACT   = 100                                    # Options multiplier

# ── Risk Management ────────────────────────────────────────────────────────────
MAX_LOSS_USD          = float(os.getenv("MAX_LOSS_USD", "5000"))
MAX_VEGA_EXPOSURE     = 200.0
MAX_PORTFOLIO_DELTA   = 5.0                                    # Hard delta cap

# ── Claude AI ─────────────────────────────────────────────────────────────────
CLAUDE_MODEL          = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS     = 1024
AI_DECISION_LOG       = "logs/ai_decisions.jsonl"

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL             = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE              = os.getenv("LOG_FILE", "logs/hedge_fund.log")

# ── Market Hours (ET) ──────────────────────────────────────────────────────────
MARKET_OPEN_HOUR      = 9
MARKET_OPEN_MIN       = 30
MARKET_CLOSE_HOUR     = 16
MARKET_CLOSE_MIN      = 0
HEDGE_START_HOUR      = 9                                      # Start hedging after 9:45
HEDGE_START_MIN       = 45
HEDGE_END_HOUR        = 15                                     # Stop hedging at 3:45
HEDGE_END_MIN         = 45
