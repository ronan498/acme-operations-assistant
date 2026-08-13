# Decisions Log

Append-only. Each entry: what was decided, why, and what it trades away.

---

**2026-08-13 23:08 — mcp pinned to `>=1.29,<2`**
mcp 2.0.0 released 2026-07-28 — sixteen days old at scaffold time, same day as the final 1.x (1.29.0). A demo that must not break favours the mature major. Trade-off: migration to 2.x noted as follow-up work; the seam is two files (`api/app/readiness.py` client, `mcp-server/server.py`).

**2026-08-13 23:08 — postgres:17-alpine over 18-alpine**
18 is current but 17 has a year of production hardening and the widest extension support (pgvector, if the RAG extension ever lands). Boring tech for the durable store. Verified both tags live on Docker Hub.

**2026-08-13 23:08 — image pins verified against live registries, not memory**
postgres:17-alpine, redis:8-alpine, quay.io/keycloak/keycloak:26.7.1, arizephoenix/phoenix:version-20.2.0, uv==0.12.4 (PyPI), all checked 2026-08-13. Model IDs (`gpt-5.6-sol`, `gpt-5.5`, `gpt-5.4`) verified against OpenAI `/v1/models` the same day.

**2026-08-13 23:08 — data plane publishes no host ports**
postgres, redis, mcp-server have no `ports:` mapping — reachable only on the Compose network. Only api (8000), keycloak (8080), phoenix (6006) are exposed. Trade-off: inspecting Postgres from the host requires `docker compose exec`, which is fine.

**2026-08-13 23:08 — keycloak runs `start-dev`**
Dev mode (HTTP, no TLS) is appropriate for a local, single-machine case study; production would use `start` with TLS and a real database. Realm import as committed JSON arrives in Phase 2. Health checked via bash `/dev/tcp` against the management port (the image ships no curl/wget).

**2026-08-13 23:08 — uv inside Docker, none on host**
Host has no uv; builds install uv==0.12.4 in the builder stage and `uv lock && uv sync --frozen`. Lockfiles get committed once generated from the first build so later builds are reproducible.

**2026-08-13 23:08 — SQL files over Alembic**
Versioned SQL in `db/init/` (Postgres entrypoint runs them on first boot). Faster at this scale and more legible to an assessor reading cold. Trade-off: no down-migrations; acceptable for a demo with `make down && make up` as the reset path.
