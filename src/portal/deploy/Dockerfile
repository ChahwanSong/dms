# DMS Portal image: multi-stage build.
#   stage 1 (node)   — build the Vite SPA into static assets
#   stage 2 (python) — install the BFF and serve the API + built SPA
# Build context is the repo root:
#   docker build -f src/portal/deploy/Dockerfile -t pkg-01:5000/dms-portal:v1 .

FROM node:22-alpine AS frontend
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
