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

chat: ## make chat U=sara Q="your question" [S=session-id]
	@./scripts/chat.sh "$(U)" "$(Q)" "$(or $(S),default)"

test: ## run api tests in the builder stage (has uv + dev deps)
	docker build -q --target builder -t acme-api-test ./api >/dev/null
	docker run --rm -v ./api/app:/srv/app:ro -v ./api/tests:/srv/tests:ro acme-api-test \
		sh -c "uv sync -q && uv run pytest tests -q"

lint: ## ruff over the api service
	docker build -q --target builder -t acme-api-test ./api >/dev/null
	docker run --rm -v ./api/app:/srv/app:ro acme-api-test sh -c "uv sync -q && uv run ruff check app"

seed: ## load schema + narrative seed data (idempotent, safe to re-run)
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/01-schema.sql
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/02-seed.sql
	@echo "seeded."

eval: ## (Phase 7) run the eval harness
	@echo "eval arrives in Phase 7"
