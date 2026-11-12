#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/compose.analysis.yaml"
ACTION="${1:-}"
ENV_FILE="${2:-${PROJECT_DIR}/.env.jetson}"
PROJECT_NAME="moda-analysis"

usage() {
  echo "Usage: $0 {start|stop|restart|status|logs|config} [env-file]" >&2
}

compose() {
  docker compose \
    --project-name "${PROJECT_NAME}" \
    --env-file "${ENV_FILE}" \
    --file "${COMPOSE_FILE}" \
    "$@"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command is not installed: $1" >&2
    exit 1
  fi
}

require_environment() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Environment file not found: ${ENV_FILE}" >&2
    echo "Copy .env.jetson.example to .env.jetson and fill in real values." >&2
    exit 1
  fi

  local missing=0
  local key
  for key in \
    MODA_KAFKA_BOOTSTRAP_SERVERS \
    MODA_WEIGHT_CAMERA_URL; do
    if ! grep -Eq "^[[:space:]]*${key}=.+$" "${ENV_FILE}"; then
      echo "Missing or empty setting in ${ENV_FILE}: ${key}" >&2
      missing=1
    fi
  done
  if (( missing )); then
    exit 1
  fi

  if grep -Eq 'user:password|camera-host|weight-camera' "${ENV_FILE}"; then
    echo "Environment file still contains example camera values: ${ENV_FILE}" >&2
    exit 1
  fi
}

preflight() {
  require_command docker
  docker compose version >/dev/null
  require_environment

  if ! docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'; then
    echo "Docker runtime 'nvidia' is not available on this host." >&2
    exit 1
  fi

  if [[ "$(uname -m)" != "aarch64" ]]; then
    echo "Warning: this deployment targets Jetson aarch64; current architecture is $(uname -m)." >&2
  fi

  local model
  for model in tram_can; do
    if [[ ! -s "${PROJECT_DIR}/models/triton/${model}/1/model.onnx" ]]; then
      echo "Missing or empty Triton model: models/triton/${model}/1/model.onnx" >&2
      exit 1
    fi
    if [[ ! -f "${PROJECT_DIR}/models/triton/${model}/config.pbtxt" ]]; then
      echo "Missing Triton config: models/triton/${model}/config.pbtxt" >&2
      exit 1
    fi
  done

  mkdir -p "${PROJECT_DIR}/runtime/weight_result"
  compose config --quiet
}

case "${ACTION}" in
  start)
    preflight
    compose up --detach --build triton weight_reader
    compose ps
    ;;
  stop)
    require_command docker
    require_environment
    compose down
    ;;
  restart)
    preflight
    compose up --detach --build --force-recreate \
      triton weight_reader
    compose ps
    ;;
  status)
    require_command docker
    require_environment
    compose ps
    ;;
  logs)
    require_command docker
    require_environment
    compose logs --follow --tail 200
    ;;
  config)
    preflight
    compose config
    ;;
  *)
    usage
    exit 2
    ;;
esac
