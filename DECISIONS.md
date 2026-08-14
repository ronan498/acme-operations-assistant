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

**2026-08-14 00:35 — Keycloak issuer split: internal JWKS URL, public issuer claim**
Tokens are minted at `http://localhost:8080/realms/acme` (what browser/curl see) while the api verifies against JWKS fetched via `http://keycloak:8080` (compose network). Two distinct settings — conflating them is the classic Keycloak-in-docker 401. Production would set KC_HOSTNAME and collapse the split.

**2026-08-14 00:35 — audience not verified in JWTs**
Keycloak access tokens carry `aud=account` by default; mapping a custom audience buys nothing for a single-API deployment. Signature (RS256), expiry, and issuer are enforced. Documented so the panel hears it as a choice, not an oversight.

**2026-08-14 00:35 — password grant enabled on the demo client**
`directAccessGrantsEnabled: true` exists so the gate and evals can mint tokens via curl. OAuth 2.1 deprecates password grant — the real login path is auth-code + PKCE (Phase 5b). Demo convenience, clearly scoped.

**2026-08-14 00:35 — Keycloak user IDs pinned to match the seeded users table**
The realm import fixes each user's UUID (ada=1111…, sara=2222…, sam=3333…) so JWT `sub` claims join directly against `users`/`audit_log` with FK integrity. Keycloak remains the identity source of truth; Postgres mirrors the subjects.

**2026-08-14 02:05 — Redis vs Postgres (§4.7 rationale)**
Redis holds two things: conversation memory (keyed principal+session, 20-turn cap, 1h TTL) and a read-through customer-lookup cache (5-min TTL). Both are ephemeral by nature — losing them costs a cache miss or a fresh conversation, never data. Postgres holds everything a business decision depends on: issues, updates, actions, audit. Rule: anything a write depends on is re-read from Postgres; Redis is never the source of truth. Trade-off: repeat reads cost a Redis hop; correctness is never negotiated against a TTL.

**2026-08-14 02:05 — authorship is injected server-side, not model-supplied**
Write tools take an `actor` param that is stripped from the model-facing schema and overwritten by the registry from the JWT principal. The model cannot claim to be someone else, no matter what a prompt (or injected issue text) says. Complements the fail-closed policy table.

**2026-08-14 02:05 — parallel reads reuse the registry's read_only flag**
Consecutive concurrency-safe tool calls in one round are gathered in parallel; anything else serialises, order preserved. No separate concurrency config to drift out of sync — the same annotation drives both authorization semantics and scheduling.

**2026-08-14 02:40 — the Skill is a local tool behind the same gates**
customer_escalation_summary runs in-process (it orchestrates tools + one structured LLM call) but registers, authorizes, and audits through the identical registry dispatch as MCP tools. Its gather steps run AS the calling principal — every sub-call individually audited — and its persist stage re-enters dispatch for the admin-gated write. The Skill has no privilege its caller lacks, by construction.

**2026-08-14 02:40 — two-tier skill loading**
Only frontmatter (name/description/when_to_use) rides in the agent's context as the tool description; the SKILL.md body loads on invocation as the reasoning call's instructions. The agent pays for the body only when the Skill fires — and the model demonstrably selects it from frontmatter alone.
