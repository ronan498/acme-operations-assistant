.PHONY: up down logs ps ready test lint seed eval

up: ## build and start all six services
	docker compose up -d --build

down: ## stop everything, drop volumes (cold-boot test starts here)
	docker compose down -v

logs: ## follow logs from every service
	docker compose logs -f

ps: ## container status incl. health
	docker compose ps

ready: ## readiness report from the api
	@curl -s http://localhost:8000/ready | python3 -m json.tool

test: ## run api tests inside the image
	docker compose run --rm --no-deps api sh -c "pip install -q pytest pytest-asyncio && python -m pytest tests -q" 2>/dev/null || \
	cd api && python3 -m pytest tests -q

lint: ## ruff over both services
	cd api && python3 -m ruff check . 2>/dev/null || docker compose run --rm --no-deps api python -m ruff check .

seed: ## load schema + narrative seed data (idempotent, safe to re-run)
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/01-schema.sql
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/02-seed.sql
	@echo "seeded."

eval: ## (Phase 7) run the eval harness
	@echo "eval arrives in Phase 7"
