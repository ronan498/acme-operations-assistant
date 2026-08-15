.PHONY: up down logs ps ready test lint seed eval db db-expose db-hide

db: ## interactive psql shell inside the postgres container
	docker compose exec postgres psql -U acme -d acme

db-expose: ## publish postgres on 127.0.0.1:5433 for GUI tools (TablePlus/DBeaver/etc.)
	docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d postgres
	@echo "postgres -> 127.0.0.1:5433 · db=acme · user=acme · password=acme_dev_password"

db-hide: ## remove the published port again (back to internal-only)
	docker compose up -d postgres
	@echo "postgres is internal-only again"

db-gui: ## browser DB GUI (Adminer) at http://localhost:8081, joined to the compose network
	docker run -d --rm --name acme-adminer --network acme-assistant_default -p 127.0.0.1:8081:8080 adminer:latest >/dev/null
	@echo "open http://localhost:8081 · System: PostgreSQL · Server: postgres · User: acme · Password: acme_dev_password · Database: acme"

db-gui-stop: ## stop the browser DB GUI
	docker stop acme-adminer >/dev/null && echo "adminer stopped"

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
	docker run --rm -v ./api/app:/srv/app:ro -v ./api/tests:/srv/tests:ro -v ./api/skills:/srv/skills:ro acme-api-test \
		sh -c "uv sync -q && uv run pytest tests -q"

lint: ## ruff over the api service
	docker build -q --target builder -t acme-api-test ./api >/dev/null
	docker run --rm -v ./api/app:/srv/app:ro acme-api-test sh -c "uv sync -q && uv run ruff check app"

seed: ## load schema + narrative seed data (idempotent, safe to re-run)
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/01-schema.sql
	docker compose exec -T postgres psql -q -U acme -d acme -f /docker-entrypoint-initdb.d/02-seed.sql
	@echo "seeded."

eval: ## run the eval harness (10 cases) -> EVAL_RESULTS.md + evals/results.json
	python3 scripts/run_eval.py evals/cases.json

diagrams: ## render ARCHITECTURE.md mermaid blocks to docs/*.png (needs network for image pull)
	python3 scripts/export_diagrams.py
