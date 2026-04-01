#!/usr/bin/env bash
# =============================================================
#  setup_github.sh
#  Initializes local git repo and pushes to GitHub.
#  Run ONCE after cloning or first setup.
# =============================================================

set -e

echo ""
echo "🤖 AI-Native Hedge Fund — GitHub Setup"
echo "======================================="
echo ""

# ── 1. Check for git ──────────────────────────────────────────
if ! command -v git &> /dev/null; then
    echo "❌ git not found. Please install git first."
    exit 1
fi

# ── 2. Check for gh (GitHub CLI) ─────────────────────────────
if ! command -v gh &> /dev/null; then
    echo "⚠️  GitHub CLI (gh) not found."
    echo "   Install it: https://cli.github.com/"
    echo "   OR manually create the repo on github.com and run:"
    echo "     git remote add origin https://github.com/YOUR_USERNAME/ai-hedge-fund.git"
    echo "     git push -u origin main"
    echo ""
    USE_GH=false
else
    USE_GH=true
fi

# ── 3. Git init ───────────────────────────────────────────────
if [ ! -d ".git" ]; then
    echo "📁 Initializing git repository..."
    git init
    git branch -M main
else
    echo "✅ Git repo already initialized."
fi

# ── 4. Copy env template ──────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "📝 Created .env from template."
    echo "   ⚠️  IMPORTANT: Edit .env and add your API keys before running!"
    echo ""
fi

# ── 5. Initial commit ─────────────────────────────────────────
git add -A
git diff --cached --quiet || git commit -m "🚀 Initial commit: AI-native hedge fund"

# ── 6. Create GitHub repo ─────────────────────────────────────
if [ "$USE_GH" = true ]; then
    echo ""
    echo "🐙 Creating GitHub repository..."
    gh repo create ai-hedge-fund \
        --private \
        --description "AI-native delta-neutral straddle hedge fund | Claude + QuantLib + Alpaca" \
        --source=. \
        --remote=origin \
        --push \
        || echo "⚠️  Repo may already exist — trying push..."
    git push -u origin main 2>/dev/null || true
fi

echo ""
echo "✅ Done! Next steps:"
echo ""
echo "  1. Edit .env with your keys:"
echo "     ALPACA_API_KEY     → https://app.alpaca.markets (Paper Trading)"
echo "     ALPACA_SECRET_KEY  → same page"
echo "     ANTHROPIC_API_KEY  → https://console.anthropic.com"
echo ""
echo "  2. Install dependencies:"
echo "     pip install -r requirements.txt"
echo ""
echo "  3. Run backtest first:"
echo "     python scripts/run_backtest.py"
echo ""
echo "  4. Run live paper trading:"
echo "     python scripts/run_live.py"
echo ""
echo "  ⚠️  This is PAPER TRADING only by default."
echo "  ⚠️  Review config/settings.py before going live."
echo ""
