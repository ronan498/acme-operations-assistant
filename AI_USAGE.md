# AI Usage Notes (§4.9)

Tooling: Claude Code (Fable 5) as pair programmer, directed and reviewed by me.
The four questions the brief asks, answered up front; the timestamped log below
is the evidence, kept append-only from the first commit.

## 1. What was delegated to AI tools, and why

Nearly all first-draft code: scaffolding, the Compose topology, the agent loop,
the registry, SQL, the eval runner, the React console. Delegated because
generation speed is the point of the tools. What was NOT delegated: the
architecture itself (planned and argued before any code), phase gates and their
pass criteria, model and pricing verification, scope decisions, and every
security posture choice (fail-closed defaults, actor injection, RBAC placement).

## 2. How AI-generated code was reviewed, validated, and tested

Primarily by execution, not by reading. Every phase ended in a gate that had to
pass live: containers healthy, `/ready` green, real tokens through real
Keycloak, denials landing in `audit_log`, the eval suite. Reading catches style;
gates catch lies - the venv-shebang Dockerfile bug below read perfectly and
failed instantly. Unit tests cover the pure logic (policy ladder, fallback
state machine, skill loader: 11 tests), and the eval harness deliberately
trusts nothing the agent reports about itself.

## 3. How errors and hallucinations were identified and corrected

Two standing rules did most of the work. First: **no version, tag, model id, or
price from the model's memory** - everything pinned against a live source
(Docker Hub, Quay, PyPI, OpenAI /v1/models, the live pricing page). This caught
a 16-day-old mcp 2.0 release and the fictional-looking-but-real gpt-5.6 family.
Second: **known seed data as ground truth** - the "7 open issues" bug below was
visible only because the true answer (3) was designed into the data. Each catch
is logged below with the wrong output and the fix, written at the moment it
happened.

## 4. What I would not trust AI tools to do unsupervised in a client engagement

- **Anything with a version number or a price** - training data is permanently
  stale; the live source is the only authority.
- **Security boundaries** - the AI happily wrote RBAC, but deciding WHERE it
  lives (dispatch, not prompt) and that defaults fail closed is judgement a
  client pays humans for.
- **SQL that aggregates** - the same join fan-out bug appeared twice in one
  build, once in my verification query and once in the AI's tool code. Grounding
  checks against known data are non-negotiable.
- **Declaring its own work correct** - the eval suite exists because
  self-reported success is worthless; assertions read the database and the
  audit trail, never the agent's claims.
- **Client data.** This build used fictional seed data. Real engagement data
  through a hosted LLM needs a DPA, retention terms, and client sign-off first.

---

## The log (append-only, from first commit)

**2026-08-13 23:08 - Phase 0 scaffold**
Delegated: Compose file, service skeletons, Dockerfiles, readiness checks, Makefile.
Reviewed by: running the Phase 0 gate (`docker compose up` → six healthy, `/ready` green), not by reading alone.

**2026-08-13 23:08 - caught: stale-knowledge risk on versions, prevented by process**
The AI's training data predates current releases. Rule adopted: every image tag, package version, and model ID is verified against the live source (Docker Hub, Quay, PyPI, OpenAI `/v1/models`) before being pinned - never taken from the model's memory. First catch: `mcp` had a 2.0.0 major release 16 days ago that the AI didn't know existed; found by querying PyPI, pinned to `<2` deliberately.

**2026-08-13 23:08 - caught: AI initially proposed the deprecated MCP transport**
Early architecture draft said "HTTP/SSE" for the MCP transport; the current spec's remote transport is Streamable HTTP. Corrected in the architecture docs and the implementation targets `streamable-http` from day one.

**2026-08-14 00:15 - caught: AI-written Dockerfile broke venv shebangs across stages**
The multi-stage build created the venv at `/build/.venv` and copied it to `/srv/.venv`. Console scripts hardcode their interpreter path in the shebang at install time, so `uvicorn` pointed at a python that didn't exist - `exec: no such file or directory` on container start. Caught by the Phase 0 gate, not by code review (the Dockerfile *read* fine). Fix: build the venv at its final runtime path. Lesson: the gate catches what review can't.

**2026-08-14 00:15 - caught: healthcheck assumed a shell the image doesn't ship**
The phoenix healthcheck used `CMD-SHELL`, but `arizephoenix/phoenix` contains no `sh` - the check could never pass while the endpoint it probed returned 200 all along. Diagnosed by exec'ing the probe directly (worked) vs via shell (exec: "sh" not found). Fix: exec-form `CMD`. Lesson: verify image contents before assuming POSIX furniture.

**2026-08-14 01:05 - caught: blanket 429 retry on a permanent billing error**
The first live /chat call hit OpenAI's `429 billing_not_active` and the AI-written retry loop dutifully backed off four times against a condition that can never clear. Fixed: 429s with `billing_not_active` / `insufficient_quota` codes raise immediately. Lesson: a status code is not a taxonomy - the error *code* decides retryability.

**2026-08-14 01:25 - caught: stale API-surface assumption for gpt-5.6**
The AI wrote the LLM seam against chat/completions (its training-era default). gpt-5.6-sol rejected it live: function tools + reasoning require the Responses API. The alternative - disabling reasoning to keep the old surface - was the wrong trade for a flagship reasoning model. Migrated the seam (one file + the loop's item handling); the 400's own error message was the migration guide.

**2026-08-14 01:25 - caught: the same SQL fan-out bug, twice**
The first live agent answer said Northwind has 7 open issues; it has 3. `count()` inflated by the LEFT JOIN to issue_updates - the exact bug caught and fixed in a hand-written verification query during Phase 1, then re-introduced by the AI in the MCP tool's SQL. Caught by reading the agent's output against known seed data. Two lessons: AI repeats a bug class even after one instance was fixed nearby, and "grounded in a tool result" is not "correct" - which is precisely why the eval set checks grounding against the database, not against the tool output.

**2026-08-14 02:05 - caught: import-time OpenAI client construction**
Module-level `AsyncOpenAI()` blew up test collection in an environment with no API key - an import-time side effect the AI wrote without being asked to. Made the client lazy. Caught by the test harness, which is the point of having one.
