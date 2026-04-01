"""
scripts/run_hedge.py
Standalone delta hedge rebalancer.
Run this separately if you want to manage hedging independently
from entry/exit logic, e.g., after manually entering a straddle.
"""

import sys
import time
import logging
from datetime import datetime

sys.path.insert(0, ".")

from loguru import logger
from config.settings import SYMBOL, HEDGE_INTERVAL_MIN

logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True)

from core.alpaca_client import AlpacaClient
from core.quantlib_engine import QuantLibEngine
from core.portfolio import Portfolio
from ai.claude_bridge import ClaudeBridge
from news.alpaca_news import AlpacaNewsClient
from risk.delta_hedger import DeltaHedger


def main():
    logger.info("Starting standalone delta hedger...")

    alpaca    = AlpacaClient()
    ql        = QuantLibEngine()
    portfolio = Portfolio()
    claude    = ClaudeBridge()
    news      = AlpacaNewsClient()
    hedger    = DeltaHedger(alpaca, portfolio, ql, claude, news)

    logger.info(f"Hedge interval: {HEDGE_INTERVAL_MIN} min | Symbol: {SYMBOL}")
    logger.info("Running... Ctrl+C to stop.")

    while True:
        try:
            logger.info(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Checking delta...")
            result = hedger.check_and_hedge()
            logger.info(f"Hedge result: {result}")
            time.sleep(HEDGE_INTERVAL_MIN * 60)
        except KeyboardInterrupt:
            logger.info("Stopping hedge runner.")
            break
        except Exception as e:
            logger.error(f"Hedge error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
