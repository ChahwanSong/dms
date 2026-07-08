# DMS Portal image: multi-stage build.
#   stage 1 (node)   — build the Vite SPA into static assets
#   stage 2 (python) — install the BFF and serve the API + built SPA
# Build context is the repo root:
#   docker build -f src/portal/deploy/Dockerfile -t pkg-01:5000/dms-portal:v1 .

FROM node:22-alpine AS frontend
# Optional build-time-only proxy for `npm install` on a proxy-only network. Empty by
# default → direct internet (no effect). Set via install/docker/build-images.sh or
# `--build-arg npm_config_proxy=http://127.0.0.1:7227`. NOT baked into the runtime
# image (build ARG, not ENV), so the served BFF never routes through the proxy.
# apt/curl/git/pip use the predefined http_proxy/https_proxy build-args (no ARG needed).
ARG npm_config_proxy=
ARG npm_config_https_proxy=
WORKDIR /fe
COPY src/portal/frontend/package.json ./
RUN npm install
COPY src/portal/frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Install the BFF (portal package + its extra deps). fastapi/uvicorn/pydantic
# come from the base deps; httpx/itsdangerous from the [portal] extra.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[portal]"
# Built SPA served as static assets by the BFF.
COPY --from=frontend /fe/dist /app/static
ENV PORTAL_STATIC_DIR=/app/static
EXPOSE 8090
CMD ["uvicorn", "portal.backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
