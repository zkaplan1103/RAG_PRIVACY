.PHONY: install smoke lint typecheck test

install:
	uv sync

smoke:
	uv run python tests/smoke.py

smoke-real:
	SMOKE_REAL=1 uv run python tests/smoke.py

lint:
	uv run ruff check .

typecheck:
	uv run pyright

test:
	uv run pytest tests/ -v

index:
	uv run python -m policylens.index build

app:
	uv run streamlit run app.py
