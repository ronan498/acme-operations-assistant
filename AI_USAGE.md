# AI Usage Log

Append-only, kept from the first commit (§4.9 deliverable). Standing rule:
every time an AI tool produces something wrong, the wrong output and the fix
get pasted here in the moment — not reconstructed later.

Tooling: Claude Code (Fable 5) as pair programmer, directed and reviewed by me.

---

**2026-08-13 23:08 — Phase 0 scaffold**
Delegated: Compose file, service skeletons, Dockerfiles, readiness checks, Makefile.
Reviewed by: running the Phase 0 gate (`docker compose up` → six healthy, `/ready` green), not by reading alone.

**2026-08-13 23:08 — caught: stale-knowledge risk on versions, prevented by process**
The AI's training data predates current releases. Rule adopted: every image tag, package version, and model ID is verified against the live source (Docker Hub, Quay, PyPI, OpenAI `/v1/models`) before being pinned — never taken from the model's memory. First catch: `mcp` had a 2.0.0 major release 16 days ago that the AI didn't know existed; found by querying PyPI, pinned to `<2` deliberately.

**2026-08-13 23:08 — caught: AI initially proposed the deprecated MCP transport**
Early architecture draft said "HTTP/SSE" for the MCP transport; the current spec's remote transport is Streamable HTTP. Corrected in the architecture docs and the implementation targets `streamable-http` from day one.

**2026-08-14 00:15 — caught: AI-written Dockerfile broke venv shebangs across stages**
The multi-stage build created the venv at `/build/.venv` and copied it to `/srv/.venv`. Console scripts hardcode their interpreter path in the shebang at install time, so `uvicorn` pointed at a python that didn't exist — `exec: no such file or directory` on container start. Caught by the Phase 0 gate, not by code review (the Dockerfile *read* fine). Fix: build the venv at its final runtime path. Lesson: the gate catches what review can't.

**2026-08-14 00:15 — caught: healthcheck assumed a shell the image doesn't ship**
The phoenix healthcheck used `CMD-SHELL`, but `arizephoenix/phoenix` contains no `sh` — the check could never pass while the endpoint it probed returned 200 all along. Diagnosed by exec'ing the probe directly (worked) vs via shell (exec: "sh" not found). Fix: exec-form `CMD`. Lesson: verify image contents before assuming POSIX furniture.
