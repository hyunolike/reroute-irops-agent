# ReRoute API image (control plane, mock airline service and agent worker share one image).
# Build context: repository root.   docker build -f apps/api/Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
RUN pip install --no-cache-dir uv==0.5.11

COPY apps/api/pyproject.toml ./pyproject.toml
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python --no-cache -r pyproject.toml

COPY apps/api/app ./app
COPY documents ./documents
COPY config ./config
COPY data/seed ./data/seed
COPY nvidia/openshell/policies ./nvidia/openshell/policies

# Non-root runtime user (matches the OpenShell policy's non-root process requirement)
RUN useradd --uid 10001 --create-home reroute && chown -R reroute /app
USER reroute

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
