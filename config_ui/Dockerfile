# Configuration UI (FastAPI + SQLite + React) for the Moda Jetson deployment.
# Build from the repository root:
#   docker compose -f compose.ui.yaml --env-file .env.ui build

FROM node:20.18-bookworm-slim AS frontend
WORKDIR /src
COPY config_ui/frontend/package.json config_ui/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY config_ui/frontend/ ./
RUN npm run build

# Only used to copy the docker CLI and the compose plugin (multi-arch, includes arm64).
FROM docker:27.3-cli AS dockercli

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/moda_ui \
    MODA_UI_STATIC_DIR=/opt/moda_ui/static

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/libexec/docker/cli-plugins/docker-compose

COPY config_ui/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY config_ui/backend/moda_ui /opt/moda_ui/moda_ui
COPY --from=frontend /src/dist /opt/moda_ui/static

EXPOSE 8080
ENTRYPOINT ["python3", "-m", "moda_ui"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
