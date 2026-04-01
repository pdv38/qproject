# 🤖 AI-Native Hedge Fund — Dynamic Delta-Neutral Straddle Engine

> Powered by Backtrader · QuantLib · Alpaca Markets · Claude AI (Autonomous Decision Layer)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AI-Native Hedge Fund                     │
├─────────────┬──────────────┬─────────────┬─────────────────┤
│  Backtrader │   QuantLib   │   Alpaca    │   Claude AI     │
│  Strategy   │   Greeks &   │   Paper     │   Autonomous    │
│  Engine     │   Vol Surface│   Trading   │   Decision      │
│             │   (SVI/SABR) │   + News    │   Layer         │
└─────────────┴──────────────┴─────────────┴─────────────────┘
         │              │             │              │
         └──────────────┴─────────────┴──────────────┘
                              │
                    Delta-Neutral Loop
                    ATM Straddle on SPY
```

## Strategy: ATM Short Straddle + Dynamic Delta Hedging

1. **Sell ATM Call + Put** on $SPY (same expiry, same strike = current price)
2. **QuantLib** computes live Greeks (delta, gamma, vega, theta) and IV surface
3. **Claude AI** autonomously decides:
   - Entry approval based on IV rank, news sentiment, macro regime
   - Hedge sizing and frequency
   - Risk overrides (stop-loss, gamma squeeze protection)
   - Position exits
4. **Alpaca News** feeds real-time headlines into Claude for sentiment analysis
5. **Delta hedging script** runs every N minutes to rebalance SPY shares

## Quickstart

### 1. Clone & Install
```bash
git clone https://github.com/YOUR_USERNAME/ai-hedge-fund.git
cd ai-hedge-fund
pip install -r requirements.txt
```

### 2. Get Alpaca Paper Trading Credentials
1. Go to https://app.alpaca.markets
2. Sign up (free) → Dashboard → Paper Trading
3. Generate API Key + Secret
4. Copy `.env.example` → `.env` and fill in

### 3. Get Anthropic API Key
1. Go to https://console.anthropic.com
2. API Keys → Create Key
3. Add to `.env`

### 4. Configure
```bash
cp .env.example .env
# Edit .env with your keys
```

### 5. Run
```bash
# Run live paper trading loop
python scripts/run_live.py

# Run backtest first (recommended)
python scripts/run_backtest.py

# Run delta hedge only
python scripts/run_hedge.py
```

## File Structure

```
ai-hedge-fund/
├── config/
│   └── settings.py          # All strategy parameters
├── core/
│   ├── alpaca_client.py     # Alpaca API wrapper
│   ├── quantlib_engine.py   # Greeks, IV surface, SABR/SVI
│   └── portfolio.py         # Portfolio state tracker
├── strategies/
│   ├── straddle.py          # ATM straddle entry/exit logic
│   └── backtrader_strategy.py # Backtrader integration
├── risk/
│   ├── delta_hedger.py      # Dynamic delta-neutral rebalancer
│   └── risk_manager.py      # Greeks limits, stop-losses
├── ai/
│   ├── claude_bridge.py     # Claude API autonomous decision layer
│   └── prompts.py           # Structured prompts for each decision type
├── news/
│   └── alpaca_news.py       # Live news ingestion + preprocessing
├── scripts/
│   ├── run_live.py          # Main live trading loop
│   ├── run_backtest.py      # Backtrader backtest runner
│   └── run_hedge.py         # Standalone hedge rebalancer
├── .env.example
├── requirements.txt
└── README.md
```

## Risk Warnings

- This is **paper trading only** by default
- Short straddles have **unlimited risk** on large moves
- Always set `MAX_LOSS_USD` in config before running
- Claude AI decisions are logged but **not guaranteed to be profitable**
- Past performance of any strategy does not guarantee future results

## Parameters (config/settings.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SYMBOL` | SPY | Underlying ticker |
| `DTE_TARGET` | 30 | Target days to expiry |
| `DELTA_THRESHOLD` | 0.10 | Rehedge when |delta| > this |
| `HEDGE_INTERVAL_MIN` | 15 | Minutes between hedge checks |
| `MAX_LOSS_USD` | 5000 | Hard stop loss |
| `IV_RANK_MIN` | 40 | Min IV rank to enter straddle |
| `MAX_GAMMA_EXPOSURE` | 50 | Max gamma before reducing size |

## Claude AI Decision Types

| Decision | Trigger | Claude evaluates |
|----------|---------|-----------------|
| `ENTRY` | New straddle opportunity | IV rank, news sentiment, term structure |
| `HEDGE` | Delta breach | Gamma risk, news regime, cost of hedge |
| `EXIT` | P&L or Greeks limit | Profit target, tail risk, upcoming events |
| `OVERRIDE` | Extreme news event | Black swan detection, circuit breaker |
