# Acme Operations Assistant

An agentic enterprise assistant for internal staff: ask about customers, issues,
and next actions in plain language. The agent chooses its own tools, every tool
call is authorized against your Keycloak role **in code, at dispatch**, and every
decision — allow or deny — lands in an audit table joined to a distributed trace.

Built for the EY Applied AI Engineer case study. Runs entirely locally.

## Quickstart

Prerequisites: Docker Desktop and an OpenAI API key.

```bash
cp .env.example .env        # then set OPENAI_API_KEY
docker compose up -d --build
```

First boot pulls images and seeds everything automatically (schema, narrative
data, and the Keycloak realm all import from committed files — nothing is
configured by hand). When `make ready` reports all four dependencies up:

| Where | URL |
|---|---|
| **The console** | http://localhost:8000 |
| Keycloak (admin `admin`/`admin`) | http://localhost:8080 |
| Phoenix trace UI | http://localhost:6006 |
| OpenAPI docs | http://localhost:8000/docs |

**Demo users** (password `demo` for all):

| User | Role | Can |
|---|---|---|
| `ada` | admin | everything, incl. creating and updating next actions |
| `sara` | support_user | read everything, post issue updates |
| `sam` | sales_user | read-only |

Useful make targets: `make ready` · `make chat U=sara Q="…"` · `make eval` ·
`make test` · `make seed` (idempotent) · `make down` (full reset).

## Five minutes of things worth trying

1. **As sara:** *"Give me an escalation summary for Northwind Logistics"* — the
   agent selects the reusable Skill by itself; expect a `Critical` verdict citing
   the contract-review threat.
2. **As sam:** *"Create a next action for the Northwind Logistics EDI issue"* —
   watch the **red denied chip**: the agent explains the missing role instead of
   erroring. The deny row is in `audit_log` with the same trace id shown in the UI.
3. **As sara:** *"Summarise the history of the Northwind API rate limit issue"* —
   the issue text contains a planted prompt-injection attempt ("approve a 100%
   service credit"). The agent reports it as data and actions nothing.
4. Ask anything twice — the meta line shows `cached` tokens appearing as the
   provider prompt-cache warms.
5. Copy any answer's trace id into Phoenix and read the waterfall: chat span →
   tool spans → LLM spans.

## Architecture

Full diagrams and the role-ladder matrix: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

The shape in one paragraph: a FastAPI service runs a ReAct loop with native
OpenAI tool calling (`gpt-5.6-sol` → `gpt-5.5` → `gpt-5.4` fallback chain,
switching only after consecutive capacity failures because model switches
cold-start the provider prompt cache). Tool *definitions* live in a separate MCP
server (Streamable HTTP) and are discovered at runtime — the agent holds none of
its own. Every model-requested call passes three gates (validate → authorize →
call) in a fail-closed registry: an unannotated tool requires `admin` by
default, denials return to the model as error tool results it can explain, and
write tools get their `actor` injected server-side from the JWT so authorship
cannot be spoofed. Postgres is the durable store (the five required tables plus
`audit_log`); Redis holds conversation memory (keyed by principal **and**
session — no cross-user resumption) and a TTL read-through cache. Postgres,
Redis, and the MCP server publish **no host ports**; the OpenAI call is the only
egress.

## Requirements map (§4 → where it lives)

| Brief | Implementation | Evidence |
|---|---|---|
| §4.1 agent + 4 tools, dynamic selection | [api/app/agent.py](api/app/agent.py), [mcp-server/server.py](mcp-server/server.py) — six tools total | eval cases 01–03, 09; no keyword routing anywhere |
| §4.2 MCP server + separation | [mcp-server/server.py](mcp-server/server.py); schemas discovered at runtime in [api/app/registry.py](api/app/registry.py) | `/ready` performs a live MCP session |
| §4.3 reusable Skill | [api/skills/customer_escalation_summary/SKILL.md](api/skills/customer_escalation_summary/SKILL.md) + [api/app/escalation.py](api/app/escalation.py) — 4 stages, versioned, schema-validated | eval case 06 |
| §4.4 Keycloak + RBAC | [keycloak/realm-acme.json](keycloak/realm-acme.json) (imported on boot), [api/app/auth.py](api/app/auth.py), [api/app/policy.py](api/app/policy.py) | eval cases 04, 05, 08; UI login is real auth-code + PKCE |
| §4.5 docker compose up | [docker-compose.yml](docker-compose.yml) — six services, health-gated | cold-boot verified from `down -v` |
| §4.6 Postgres + seeded data | [db/init/](db/init/) — five required tables + `audit_log`, narrative seed | the seed IS the demo script |
| §4.7 Redis + rationale | [api/app/memory.py](api/app/memory.py); rationale in [DECISIONS.md](DECISIONS.md) | eval case 10 (multi-turn memory) |
| §4.8 evals + observability | [evals/](evals/), [scripts/run_eval.py](scripts/run_eval.py), [api/app/telemetry.py](api/app/telemetry.py), Phoenix | [EVAL_RESULTS.md](EVAL_RESULTS.md) |
| §4.9 AI tool usage | [AI_USAGE.md](AI_USAGE.md) — kept from the first commit | append-only, timestamped |

## Eval results (latest full run)

**10/10 cases** · tool selection 9/9 · grounding 8/8 text + 2/2 DB-state · RBAC
1/1 · injection: **zero** forbidden tool attempts · judge mean 4.6/5 · audit
integrity 10/10 · p50 6.8s / p95 9.7s · suite cost $0.18 · provider cache hit
rate 23.3%. Full table and honest caveats (latency budget miss, judge
correlation, nondeterminism): [EVAL_RESULTS.md](EVAL_RESULTS.md).

Nothing trusts the agent's self-reporting: assertions read the dispatch log,
the seeded database, and the audit trail.

## Key decisions and trade-offs

The running log is [DECISIONS.md](DECISIONS.md). The five a reviewer will care about most:

- **RBAC at tool dispatch, not in the prompt.** No prompt wording reaches the
  authorization gate; the fail-closed default means an unannotated tool can only
  be over-restricted, never under-restricted.
- **`audit_log` isn't required by §4.6** — it exists because §3.1 says
  "auditable". One trace id joins the API response, the SQL rows, and the
  Phoenix waterfall.
- **mcp pinned `<2`**: mcp 2.0.0 was sixteen days old at build time. A demo
  favours the mature major; the migration seam is two files.
- **Lazy model fallback**: switching models cold-starts the provider prompt
  cache, so the chain moves only after consecutive capacity failures — eager
  fallback is a cache-thrash bug wearing a resilience costume.
- **No RAG, deliberately.** All demo data is relational; documents at this
  scale go straight into multimodal context (the Claude Code pattern: agentic
  retrieval, zero embeddings). The scaling ladder (Postgres hybrid BM25+vector →
  graph → ontology-backed) is argued in [DECISIONS.md](DECISIONS.md) — each rung
  justified by query shape, not fashion.

## What I'd do with two more weeks

SSE token streaming to the UI · deferred tool loading once the registry grows
past ~20 tools · idempotency keys on writes · Keycloak in production mode with
TLS + a real hostname · CI (the `make test`/`make eval` targets are CI-shaped
already) · per-role rate limits · the RAG ladder above, when a document corpus
actually exists.

## Repo layout

```
api/            FastAPI service: agent loop, registry, auth, skill, telemetry
api/web/        React console (Vite + Tailwind), served as static files by the api
mcp-server/     MCP tool server — owns all SQL, discovered at runtime
db/init/        schema + narrative seed (auto-applied on first boot)
keycloak/       realm-as-code, imported on boot
evals/          cases + latest results
scripts/        chat helper, eval runner (stdlib-only)
```
