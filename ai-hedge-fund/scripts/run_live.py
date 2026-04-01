"""
scripts/run_live.py
Main live paper trading loop.
Orchestrates: news → Greeks → Claude AI → hedge → straddle management.
"""

import sys
import time
import logging
import threading
from datetime import datetime, time as dtime

sys.path.insert(0, ".")

from rich.console import Console
from rich.table import Table
from rich.live import Live
from loguru import logger

from config.settings import (
    SYMBOL, HEDGE_INTERVAL_MIN, LOG_FILE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
    HEDGE_START_HOUR, HEDGE_START_MIN,
    HEDGE_END_HOUR, HEDGE_END_MIN,
    MAX_LOSS_USD,
)
from core.alpaca_client import AlpacaClient
from core.quantlib_engine import QuantLibEngine
from core.portfolio import Portfolio
from ai.claude_bridge import ClaudeBridge
from news.alpaca_news import AlpacaNewsClient
from risk.delta_hedger import DeltaHedger
from risk.risk_manager import RiskManager
from strategies.straddle import StraddleStrategy

# ── Logging Setup ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True)
logger.add(LOG_FILE, level="DEBUG", rotation="1 day")

console = Console()

# ── Global state for news streaming ───────────────────────────────────────────
_breaking_news_buffer: list[str] = []
_news_lock = threading.Lock()


def on_news_article(article):
    """Callback for streaming news. Buffers extreme-risk articles."""
    if article.is_extreme_risk:
        with _news_lock:
            _breaking_news_buffer.append(article.headline)
        logger.warning(f"🔴 BREAKING: {article.headline}")


def is_market_hours() -> bool:
    now = datetime.now()
    t   = now.time()
    open_t  = dtime(MARKET_OPEN_HOUR, MARKET_OPEN_MIN)
    close_t = dtime(MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
    if now.weekday() >= 5:  # Weekend
        return False
    return open_t <= t <= close_t


def is_hedge_hours() -> bool:
    t       = datetime.now().time()
    start_t = dtime(HEDGE_START_HOUR, HEDGE_START_MIN)
    end_t   = dtime(HEDGE_END_HOUR, HEDGE_END_MIN)
    return start_t <= t <= end_t


def build_status_table(portfolio: Portfolio, alpaca: AlpacaClient) -> Table:
    """Build a rich table for live status display."""
    table = Table(title="🤖 AI Hedge Fund — Live Status", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="dim", width=28)
    table.add_column("Value", justify="right")

    acct = alpaca.get_account()
    s    = portfolio.straddle

    table.add_row("Time (UTC)", datetime.utcnow().strftime("%H:%M:%S"))
    table.add_row("Account Equity", f"${acct['equity']:,.2f}")
    table.add_row("Buying Power",   f"${acct['buying_power']:,.2f}")
    table.add_row("Straddle Open",  "✅ YES" if s else "⬜ NO")

    if s:
        table.add_row("Strike",         f"${s.strike:.2f}")
        table.add_row("Expiry",         s.expiry)
        table.add_row("Entry Credit",   f"${s.entry_credit:.2f}")
        table.add_row("Unrealized P&L", f"${s.unrealized_pnl:+.2f}")
        table.add_row("P&L %",          f"{s.pnl_pct:+.1%}")
        table.add_row("Net Delta",      f"{portfolio.net_delta:+.4f}")
        table.add_row("Net Gamma",      f"{s.net_gamma:.4f}")
        table.add_row("Net Vega",       f"{s.net_vega:.4f}")
        table.add_row("Net Theta",      f"{s.net_theta:.4f}")

    table.add_row("Hedge Shares",   f"{portfolio.hedge.shares:+.0f}")
    table.add_row("Total P&L",      f"${portfolio.total_pnl:+.2f}")
    table.add_row("Realized P&L",   f"${portfolio.realized_pnl:+.2f}")

    return table


def main():
    console.print("[bold green]🚀 AI-Native Hedge Fund Starting...[/bold green]")

    # ── Initialize all components ──────────────────────────────────────────────
    alpaca    = AlpacaClient()
    ql        = QuantLibEngine()
    portfolio = Portfolio()
    claude    = ClaudeBridge()
    news      = AlpacaNewsClient()
    risk_mgr  = RiskManager(portfolio)
    hedger    = DeltaHedger(alpaca, portfolio, ql, claude, news)
    strategy  = StraddleStrategy(alpaca, portfolio, ql, claude, news, risk_mgr)

    # ── Start news WebSocket in background thread ──────────────────────────────
    news_thread = threading.Thread(
        target=news.stream_news_ws,
        args=(on_news_article,),
        daemon=True,
    )
    news_thread.start()
    console.print("📡 News WebSocket started.")

    # ── Verify market hours ────────────────────────────────────────────────────
    if not is_market_hours():
        console.print("[yellow]⚠️  Market is CLOSED. Running in monitoring mode.[/yellow]")

    console.print(f"[bold]Strategy:[/bold] ATM Short Straddle on {SYMBOL}")
    console.print(f"[bold]Mode:[/bold] PAPER TRADING (Alpaca)")
    console.print(f"[bold]AI:[/bold] Claude Autonomous Decision Layer ACTIVE")
    console.print("")

    loop_count   = 0
    last_scan    = 0    # timestamp of last entry scan
    SCAN_INTERVAL = 300  # scan every 5 minutes

    # ── Main Loop ──────────────────────────────────────────────────────────────
    while True:
        try:
            loop_count += 1
            now = time.time()

            if not is_market_hours():
                console.print(f"[dim]Market closed. Sleeping 60s... (loop {loop_count})[/dim]")
                time.sleep(60)
                continue

            # ── 1. Check for breaking news (circuit breaker) ───────────────────
            with _news_lock:
                breaking = _breaking_news_buffer.copy()
                _breaking_news_buffer.clear()

            if breaking and portfolio.straddle:
                breaking_text = "\n".join(breaking)
                logger.warning(f"🔴 OVERRIDE CHECK | Breaking news: {breaking_text[:200]}")
                spot = alpaca.get_latest_price(SYMBOL)
                override = claude.decide_override(
                    breaking_news=breaking_text,
                    portfolio_summary=portfolio.summary(),
                    straddle_greeks=portfolio.straddle.__dict__,
                    spot=spot,
                )
                if override.action in ("FLATTEN", "REDUCE"):
                    logger.warning(f"🛑 CLAUDE OVERRIDE: {override.action} | {override.reasoning}")
                    strategy._execute_exit(reason=f"AI_OVERRIDE: {override.action}")
                    console.print(f"[red]🛑 EMERGENCY EXIT: {override.action}[/red]")

            # ── 2. Hard risk check ─────────────────────────────────────────────
            if portfolio.straddle:
                from datetime import date
                dte = (date.fromisoformat(portfolio.straddle.expiry) - date.today()).days
                if risk_mgr.has_hard_violations(dte):
                    logger.error("Hard risk violation — emergency exit!")
                    strategy._execute_exit(reason="hard_risk_violation")

            # ── 3. Refresh Greeks ──────────────────────────────────────────────
            if portfolio.straddle:
                prices = alpaca.get_iv_history(SYMBOL, days=60)
                hv     = ql.compute_historical_vol(prices, 21)
                atm_iv = hv * 1.15
                hedger.refresh_portfolio_greeks(atm_iv)

            # ── 4. Delta Hedge check ───────────────────────────────────────────
            if is_hedge_hours() and portfolio.straddle:
                hedge_result = hedger.check_and_hedge()
                if hedge_result.get("action") == "HEDGE":
                    console.print(
                        f"[cyan]🔄 Hedged: {hedge_result.get('shares', 0):+.0f} shares[/cyan]"
                    )

            # ── 5. Exit check ──────────────────────────────────────────────────
            if portfolio.straddle:
                prices = alpaca.get_iv_history(SYMBOL, days=60)
                hv     = ql.compute_historical_vol(prices, 21)
                atm_iv = hv * 1.15
                exit_result = strategy.check_and_exit(atm_iv=atm_iv)
                if exit_result.get("action") == "EXITED":
                    console.print(
                        f"[green]✅ Straddle closed | P&L: ${exit_result.get('pnl', 0):+.2f}[/green]"
                    )

            # ── 6. Entry scan (every 5 min) ────────────────────────────────────
            if now - last_scan > SCAN_INTERVAL and not portfolio.straddle:
                last_scan = now
                entry_result = strategy.scan_and_enter()
                if entry_result.get("action") == "ENTERED":
                    console.print(
                        f"[bold green]🎯 Straddle entered! Credit: ${entry_result.get('credit', 0):.2f}[/bold green]"
                    )

            # ── 7. Display status ──────────────────────────────────────────────
            if loop_count % 4 == 0:  # Every ~60s
                table = build_status_table(portfolio, alpaca)
                console.print(table)

            time.sleep(15)  # Main loop runs every 15 seconds

        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️  Shutting down gracefully...[/yellow]")
            if portfolio.straddle:
                console.print("Closing open straddle on shutdown...")
                strategy._execute_exit(reason="manual_shutdown")
            alpaca.cancel_all_orders()
            break

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(30)  # Back off on errors


if __name__ == "__main__":
    main()
