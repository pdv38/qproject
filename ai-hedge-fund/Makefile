# AI-Native Hedge Fund — Makefile

.PHONY: install test backtest live hedge lint clean

install:
	pip install -r requirements.txt

test:
	pytest tests/ -v --tb=short

backtest:
	python scripts/run_backtest.py --start 2023-01-01 --end 2024-12-31

live:
	python scripts/run_live.py

hedge:
	python scripts/run_hedge.py

lint:
	ruff check .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete 2>/dev/null; \
	echo "Cleaned."

logs:
	tail -f logs/hedge_fund.log

ai-log:
	cat logs/ai_decisions.jsonl | python -m json.tool | less
