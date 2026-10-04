.PHONY: help keys up tunnel seed test logs down rebuild

help:
	@echo "Available commands:"
	@echo "  make keys    - Generate cryptographic keys & admin hash"
	@echo "  make up      - Start app, n8n, and import workflows"
	@echo "  make tunnel  - Start Cloudflare Tunnel for public HTTPS"
	@echo "  make seed    - Seed 90 days of realistic demo data"
	@echo "  make test    - Run pytest test suite inside container"
	@echo "  make logs    - Follow application logs"
	@echo "  make down    - Stop all containers (n8n data is preserved)"
	@echo "  make rebuild - Rebuild images and restart"

keys:
	docker compose run --rm -it tools python scripts/generate_keys.py

up:
	docker compose up -d --build

tunnel:
	docker compose --profile tunnel up -d

seed:
	docker compose run --rm tools python scripts/seed_demo.py --reset

test:
	docker compose --profile test run --rm tests

logs:
	docker compose logs -f app

down:
	docker compose down

rebuild:
	docker compose build --no-cache
	docker compose up -d
