# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
# uv pinned via PyPI (0.12.4 verified 2026-08-13); no local uv on the host
RUN pip install --no-cache-dir uv==0.12.4
# venv MUST be built at its final runtime path: console-script shebangs
# hardcode the interpreter path at install time (see AI_USAGE.md 2026-08-14)
WORKDIR /srv
COPY pyproject.toml uv.lock* ./
# --frozen once uv.lock is committed; first build generates it
RUN uv lock && uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --create-home app
WORKDIR /srv
COPY --from=builder /srv/.venv /srv/.venv
COPY app ./app
COPY skills ./skills
ENV PATH="/srv/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
