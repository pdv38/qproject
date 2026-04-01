"""
news/alpaca_news.py
Live news ingestion from Alpaca Markets News API.
Preprocesses headlines for Claude's decision context.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from config.settings import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_DATA_URL, SYMBOL
)

logger = logging.getLogger(__name__)

NEWS_ENDPOINT = f"{ALPACA_DATA_URL}/v1beta1/news"

# Keywords that trigger an emergency override check
EXTREME_RISK_KEYWORDS = [
    "flash crash", "circuit breaker", "trading halt", "market closed",
    "federal reserve emergency", "fed cuts rates", "fed raises rates",
    "financial crisis", "bank failure", "bank run", "lehman",
    "nuclear", "terrorist", "attack", "explosion",
    "pandemic", "lockdown", "war declared",
    "sec halts", "nasdaq halts", "nyse halts",
    "black monday", "black swan",
]

# Keywords that suggest elevated volatility risk
ELEVATED_RISK_KEYWORDS = [
    "fomc", "cpi", "ppi", "jobs report", "nfp", "gdp",
    "inflation", "recession", "tariff", "sanctions",
    "earnings", "guidance", "downgrade", "upgrade",
    "geopolitical", "conflict", "tension",
]


class NewsArticle:
    def __init__(self, raw: dict):
        self.id        = raw.get("id", "")
        self.headline  = raw.get("headline", "")
        self.summary   = raw.get("summary", "")
        self.source    = raw.get("source", "")
        self.timestamp = raw.get("created_at", "")
        self.symbols   = raw.get("symbols", [])
        self.url       = raw.get("url", "")

    @property
    def is_market_moving(self) -> bool:
        text = (self.headline + " " + self.summary).lower()
        return any(kw in text for kw in ELEVATED_RISK_KEYWORDS + EXTREME_RISK_KEYWORDS)

    @property
    def is_extreme_risk(self) -> bool:
        text = (self.headline + " " + self.summary).lower()
        return any(kw in text for kw in EXTREME_RISK_KEYWORDS)

    def __repr__(self):
        return f"[{self.source}] {self.headline[:80]}..."


class AlpacaNewsClient:
    """
    Fetches and caches news from Alpaca.
    Provides formatted summaries for Claude's context window.
    """

    def __init__(self):
        self.headers = {
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        }
        self._cache: list[NewsArticle] = []
        self._last_fetch: Optional[datetime] = None
        logger.info("Alpaca News client initialized.")

    def fetch_recent_news(
        self,
        symbols:  Optional[list[str]] = None,
        hours:    int = 2,
        limit:    int = 20,
        force:    bool = False,
    ) -> list[NewsArticle]:
        """
        Fetch recent news for given symbols (defaults to SPY + broad market).
        Caches results and refreshes every 5 minutes unless forced.
        """
        now = datetime.utcnow()

        # Cache refresh logic (every 5 min)
        if not force and self._last_fetch and (now - self._last_fetch).seconds < 300:
            return self._cache

        symbols = symbols or [SYMBOL, "SPY", "QQQ", "VIX", "UVXY"]
        start   = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "symbols": ",".join(symbols),
            "start":   start,
            "limit":   limit,
            "sort":    "DESC",
        }

        try:
            resp = requests.get(NEWS_ENDPOINT, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            articles = [NewsArticle(a) for a in data.get("news", [])]
            self._cache    = articles
            self._last_fetch = now
            logger.info(f"📰 Fetched {len(articles)} news articles (last {hours}h)")
            return articles

        except Exception as e:
            logger.warning(f"News fetch failed: {e}")
            return self._cache  # Return cached on error

    def get_summary_for_claude(
        self,
        hours: int = 2,
        max_articles: int = 8,
    ) -> str:
        """
        Build a concise news summary string for Claude's context.
        """
        articles = self.fetch_recent_news(hours=hours, limit=max_articles * 2)

        if not articles:
            return "No recent news available."

        # Prioritize market-moving articles
        sorted_articles = sorted(articles, key=lambda a: a.is_market_moving, reverse=True)
        top = sorted_articles[:max_articles]

        lines = [f"NEWS SUMMARY (last {hours}h, {len(articles)} total articles):"]
        for i, art in enumerate(top, 1):
            risk_tag = "🔴 HIGH RISK" if art.is_extreme_risk else ("⚠️ ELEVATED" if art.is_market_moving else "")
            lines.append(f"{i}. [{art.source}] {art.headline} {risk_tag}")
            if art.summary:
                lines.append(f"   → {art.summary[:120]}")

        return "\n".join(lines)

    def get_breaking_news(self, minutes: int = 15) -> Optional[str]:
        """
        Check for breaking extreme-risk news in the last N minutes.
        Returns a formatted string if found, None otherwise.
        Triggers emergency override check in Claude.
        """
        articles = self.fetch_recent_news(hours=1, force=True)
        cutoff   = datetime.utcnow() - timedelta(minutes=minutes)

        extreme = []
        for art in articles:
            if art.is_extreme_risk:
                try:
                    pub = datetime.strptime(art.timestamp[:19], "%Y-%m-%dT%H:%M:%S")
                    if pub >= cutoff:
                        extreme.append(art)
                except Exception:
                    extreme.append(art)

        if not extreme:
            return None

        lines = ["⚠️ BREAKING / EXTREME RISK NEWS DETECTED:"]
        for art in extreme:
            lines.append(f"• [{art.source}] {art.headline}")
            if art.summary:
                lines.append(f"  {art.summary[:200]}")

        breaking = "\n".join(lines)
        logger.warning(f"BREAKING NEWS DETECTED:\n{breaking}")
        return breaking

    def stream_news_ws(self, on_article_callback):
        """
        WebSocket stream for real-time news.
        Calls on_article_callback(NewsArticle) for each incoming article.
        Run in a separate thread.
        """
        import websocket
        import json

        ws_url = "wss://stream.data.alpaca.markets/v1beta1/news"

        def on_message(ws, message):
            try:
                data = json.loads(message)
                for item in data:
                    if item.get("T") == "n":  # news message type
                        art = NewsArticle({
                            "id":         item.get("id", ""),
                            "headline":   item.get("headline", ""),
                            "summary":    item.get("summary", ""),
                            "source":     item.get("source", ""),
                            "created_at": item.get("created_at", ""),
                            "symbols":    item.get("symbols", []),
                            "url":        item.get("url", ""),
                        })
                        logger.info(f"📡 Streaming news: {art.headline[:60]}")
                        on_article_callback(art)
            except Exception as e:
                logger.warning(f"WS message parse error: {e}")

        def on_open(ws):
            auth_msg = json.dumps({
                "action": "auth",
                "key":    ALPACA_API_KEY,
                "secret": ALPACA_SECRET_KEY,
            })
            ws.send(auth_msg)
            sub_msg = json.dumps({
                "action":  "subscribe",
                "news":    ["*"],  # all news
            })
            ws.send(sub_msg)
            logger.info("📡 News WebSocket connected and subscribed.")

        def on_error(ws, error):
            logger.error(f"News WS error: {error}")

        def on_close(ws, *args):
            logger.warning("News WS closed. Will reconnect...")

        while True:
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever()
            time.sleep(5)  # Reconnect after 5s
