.PHONY: install lint format test build run clean

install:
	pip install -r requirements.txt
	pip install black ruff mypy pytest pytest-asyncio

lint:
	ruff check app/ config.py
	black --check app/ config.py
	mypy app/ --ignore-missing-imports

format:
	black app/ config.py
	ruff check --fix app/ config.py

test:
	pytest tests/ -v --tb=short

build:
	docker compose build

run:
	docker compose up -d
	docker compose ps

logs:
	docker compose logs -f halyk

clean:
	docker compose down -v
	rm -rf __pycache__ .pytest_cache .mypy_cache

health:
	curl -s http://127.0.0.1:18080/health | jq .
	curl -s http://127.0.0.1:18080/metrics | head -n 20
