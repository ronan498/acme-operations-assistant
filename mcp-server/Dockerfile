# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.12.4
# venv built at its final runtime path — shebangs hardcode it (see api/Dockerfile)
WORKDIR /srv
COPY pyproject.toml uv.lock* ./
RUN uv lock && uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --create-home app
WORKDIR /srv
COPY --from=builder /srv/.venv /srv/.venv
COPY server.py .
ENV PATH="/srv/.venv/bin:$PATH"
USER app
EXPOSE 8765
CMD ["python", "server.py"]
