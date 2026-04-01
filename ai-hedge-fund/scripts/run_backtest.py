"""
scripts/run_backtest.py
Runs the ATM straddle strategy backtest using Backtrader + Alpaca historical data.
"""

import sys
import logging
from datetime import datetime, timedelta

sys.path.insert(0, ".")

import backtrader as bt
import backtrader.feeds as btfeeds
import pandas as pd

from loguru import logger
from config.settings import SYMBOL, ALPACA_API_KEY, ALPACA_SECRET_KEY
from strategies.backtrader_strategy import ATMStraddleStrategy
from core.alpaca_client import AlpacaClient

logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True)


def run_backtest(
    symbol:      str = SYMBOL,
    start_date:  str = "2023-01-01",
    end_date:    str = "2024-12-31",
    initial_cash: float = 100_000.0,
):
    logger.info(f"Starting backtest | {symbol} | {start_date} → {end_date}")

    # ── Fetch historical data ──────────────────────────────────────────────────
    logger.info("Fetching historical OHLCV from Alpaca...")
    alpaca = AlpacaClient()
    bars   = alpaca.get_historical_bars(symbol, "1Day", days=730)

    if bars.empty:
        logger.error("No data returned from Alpaca!")
        return

    # Filter date range
    bars.index = pd.to_datetime(bars.index)
    bars = bars[start_date:end_date]
    logger.info(f"Loaded {len(bars)} daily bars for {symbol}")

    # ── Backtrader setup ───────────────────────────────────────────────────────
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=0.001)  # 0.1% commission

    # Add SPY data feed
    data_feed = bt.feeds.PandasData(
        dataname=bars,
        datetime=None,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=-1,
    )
    cerebro.adddata(data_feed, name=symbol)

    # Add strategy
    cerebro.addstrategy(ATMStraddleStrategy)

    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio,  _name="sharpe",  riskfreerate=0.05)
    cerebro.addanalyzer(bt.analyzers.DrawDown,      _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns,       _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    # ── Run ────────────────────────────────────────────────────────────────────
    logger.info(f"Running backtest | Initial cash: ${initial_cash:,.2f}")
    results = cerebro.run()
    strat   = results[0]

    final_value = cerebro.broker.getvalue()
    total_return = (final_value - initial_cash) / initial_cash

    # ── Results ────────────────────────────────────────────────────────────────
    sharpe   = strat.analyzers.sharpe.get_analysis().get("sharperatio", "N/A")
    drawdown = strat.analyzers.drawdown.get_analysis()
    trades   = strat.analyzers.trades.get_analysis()

    print("\n" + "=" * 60)
    print(f"  BACKTEST RESULTS — {symbol} | {start_date} → {end_date}")
    print("=" * 60)
    print(f"  Initial Portfolio:  ${initial_cash:>12,.2f}")
    print(f"  Final Portfolio:    ${final_value:>12,.2f}")
    print(f"  Total Return:       {total_return:>12.2%}")
    print(f"  Sharpe Ratio:       {sharpe!s:>12}")

    max_dd = drawdown.get("max", {})
    print(f"  Max Drawdown:       {max_dd.get('drawdown', 0):>11.2f}%")
    print(f"  Max DD Duration:    {max_dd.get('len', 0):>12} bars")

    total_t = trades.get("total", {}).get("total", 0)
    won_t   = trades.get("won", {}).get("total", 0)
    win_rt  = won_t / total_t if total_t else 0
    print(f"  Total Trades:       {total_t:>12}")
    print(f"  Win Rate:           {win_rt:>12.1%}")
    print("=" * 60 + "\n")

    return {
        "initial_cash":  initial_cash,
        "final_value":   final_value,
        "total_return":  total_return,
        "sharpe":        sharpe,
        "max_drawdown":  max_dd.get("drawdown", 0),
        "total_trades":  total_t,
        "win_rate":      win_rt,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ATM Straddle Backtest")
    parser.add_argument("--start",  default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",    default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--cash",   default=100000.0, type=float, help="Initial cash")
    parser.add_argument("--symbol", default=SYMBOL, help="Ticker symbol")
    args = parser.parse_args()

    run_backtest(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
    )
