# Runtime image for the Agent Runtime Observatory API and worker.
# Build context is the REPOSITORY ROOT (compose uses context: .., dockerfile: infra/Dockerfile):
#   docker build -f infra/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Workspace metadata and members. apps/web is deliberately NOT copied
# (frontend is built separately; no node in this image).
COPY pyproject.toml uv.lock .python-version ./
COPY packages/ packages/
COPY apps/api/ apps/api/
COPY apps/worker/ apps/worker/
COPY examples/ examples/

RUN uv sync --frozen --no-dev

# Put the venv on PATH so `uvicorn` and `python` resolve to it directly.
ENV PATH="/app/.venv/bin:$PATH" \
    ARO_EXAMPLES_DIR=/app/examples

EXPOSE 8000

CMD ["uvicorn", "aro_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
