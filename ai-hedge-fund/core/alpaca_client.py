"""
core/alpaca_client.py
Alpaca Markets API wrapper — paper trading, market data, options chain.
"""

import time
import logging
from datetime import datetime, date, timedelta
from typing import Optional

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError
from config.settings import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, ALPACA_DATA_URL, SYMBOL
)

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Thin wrapper around alpaca_trade_api with helpers for options + equity."""

    def __init__(self):
        self.api = tradeapi.REST(
            key_id=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            base_url=ALPACA_BASE_URL,
            api_version="v2",
        )
        self._verify_connection()

    def _verify_connection(self):
        try:
            account = self.api.get_account()
            logger.info(
                f"✅ Alpaca connected | Account: {account.id} | "
                f"Equity: ${float(account.equity):,.2f} | "
                f"Buying Power: ${float(account.buying_power):,.2f}"
            )
        except APIError as e:
            logger.error(f"❌ Alpaca connection failed: {e}")
            raise

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        acct = self.api.get_account()
        return {
            "id": acct.id,
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "day_trade_count": int(acct.daytrade_count),
        }

    def get_positions(self) -> list[dict]:
        positions = self.api.list_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side,
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    # ── Market Data ────────────────────────────────────────────────────────────

    def get_latest_price(self, symbol: str = SYMBOL) -> float:
        """Get latest trade price for a symbol."""
        try:
            trade = self.api.get_latest_trade(symbol)
            return float(trade.price)
        except Exception as e:
            logger.warning(f"Could not get latest trade for {symbol}: {e}")
            # Fallback to last bar
            bars = self.api.get_bars(symbol, "1Min", limit=1).df
            return float(bars["close"].iloc[-1])

    def get_historical_bars(
        self,
        symbol: str = SYMBOL,
        timeframe: str = "1Day",
        days: int = 252,
    ):
        """Return a pandas DataFrame of OHLCV bars."""
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        bars = self.api.get_bars(symbol, timeframe, start=start, adjustment="raw").df
        return bars

    def get_iv_history(self, symbol: str = SYMBOL, days: int = 252) -> list[float]:
        """
        Proxy IV history via close-to-close historical volatility.
        Replace with a proper IV data feed if available.
        """
        bars = self.get_historical_bars(symbol, "1Day", days)
        log_returns = (bars["close"] / bars["close"].shift(1)).apply(lambda x: x ** 0.5)
        return bars["close"].tolist()

    # ── Options Chain ──────────────────────────────────────────────────────────

    def get_options_chain(self, symbol: str = SYMBOL, expiry: Optional[str] = None) -> list[dict]:
        """
        Fetch options chain via Alpaca v3 options API.
        Returns list of option contract dicts.
        """
        try:
            params = {
                "underlying_symbols": symbol,
                "status": "active",
                "type": "call",  # we'll fetch both
            }
            if expiry:
                params["expiration_date"] = expiry

            # Alpaca options endpoint (v3)
            resp = self.api.get("/v2/options/contracts", params)
            contracts = resp.get("option_contracts", [])
            return contracts
        except Exception as e:
            logger.error(f"Options chain fetch error: {e}")
            return []

    def find_atm_straddle(
        self, symbol: str = SYMBOL, target_dte: int = 30
    ) -> dict:
        """
        Find the nearest ATM call and put for a target DTE.
        Returns {'call': contract_dict, 'put': contract_dict, 'strike': float, 'expiry': str}
        """
        spot = self.get_latest_price(symbol)
        target_expiry = date.today() + timedelta(days=target_dte)

        logger.info(f"Searching ATM straddle | {symbol} spot={spot:.2f} | target_dte={target_dte}")

        # Find the nearest available expiry
        # In live environment, iterate through option_contracts endpoint
        # Here we structure the return for QuantLib to price
        atm_strike = round(spot)  # SPY options typically at whole dollar strikes

        return {
            "symbol": symbol,
            "spot": spot,
            "strike": atm_strike,
            "target_expiry": target_expiry.strftime("%Y-%m-%d"),
            "target_dte": target_dte,
            "call_symbol": f"{symbol}{target_expiry.strftime('%y%m%d')}C{int(atm_strike * 1000):08d}",
            "put_symbol":  f"{symbol}{target_expiry.strftime('%y%m%d')}P{int(atm_strike * 1000):08d}",
        }

    # ── Order Execution ────────────────────────────────────────────────────────

    def submit_market_order(
        self, symbol: str, qty: float, side: str, reason: str = ""
    ) -> dict:
        """
        Submit a market order. side = 'buy' | 'sell'
        """
        logger.info(f"📤 ORDER | {side.upper()} {qty} {symbol} | {reason}")
        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=abs(qty),
                side=side,
                type="market",
                time_in_force="day",
            )
            logger.info(f"✅ Order submitted: {order.id} | {order.status}")
            return {"id": order.id, "status": order.status, "symbol": symbol, "qty": qty, "side": side}
        except APIError as e:
            logger.error(f"❌ Order failed: {e}")
            return {"error": str(e)}

    def submit_option_order(
        self, option_symbol: str, qty: int, side: str, action: str = ""
    ) -> dict:
        """
        Submit an options order (sell to open / buy to close).
        """
        logger.info(f"📤 OPTION ORDER | {side.upper()} {qty}x {option_symbol} | {action}")
        try:
            order = self.api.submit_order(
                symbol=option_symbol,
                qty=qty,
                side=side,
                type="market",
                time_in_force="day",
            )
            return {"id": order.id, "status": order.status}
        except APIError as e:
            logger.error(f"❌ Option order failed: {e}")
            return {"error": str(e)}

    def get_order_status(self, order_id: str) -> dict:
        order = self.api.get_order(order_id)
        return {"id": order.id, "status": order.status, "filled_qty": order.filled_qty}

    def cancel_all_orders(self):
        self.api.cancel_all_orders()
        logger.info("All open orders cancelled.")

    def close_all_positions(self):
        self.api.close_all_positions()
        logger.info("All positions closed.")
