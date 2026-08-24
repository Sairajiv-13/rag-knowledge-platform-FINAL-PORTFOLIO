up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

lint:
	ruff check src

type:
	mypy

dev:
	uvicorn rag_platform.main:app --reload --app-dir src

migrate:
	docker compose run --rm api alembic upgrade head

migrate-local:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

test:
	pytest tests

test-unit:
	pytest tests/unit

test-integration:
	pytest tests/integration

test-cov:
	pytest tests --cov=rag_platform --cov-report=term-missing

ci:
	ruff check src tests migrations && mypy && pytest tests -q

eval:
	python evals/run_eval.py

benchmark:
	python -m benchmarks.latency --sizes 1000 10000 50000 --concurrency 1 8 32 \
		--requests 200 --out benchmarks/results/latency.json
	python -m benchmarks.ingestion --docs 50 --paragraphs 8

benchmark-latency:
	python -m benchmarks.latency --out benchmarks/results/latency.json

benchmark-ingestion:
	python -m benchmarks.ingestion --docs 50
