#!/usr/bin/env bash
# Start/stop every Compose project of this checkout in dependency order.
#
#   ./scripts/stack.sh start [--build]   kafka -> analysis -> detectors -> config UI
#   ./scripts/stack.sh stop              config UI -> detectors -> analysis -> kafka
#
# Kafka (service "kafka", container moda-kafka) lives in compose.yaml (detector project) but is
# started first and stopped last, because every other project publishes to / consumes from it.
#   ./scripts/stack.sh restart [--build]
#   ./scripts/stack.sh status
#   ./scripts/stack.sh logs [analysis|detector|ui]
#
# The three projects stay separate on purpose (a "down" of one never touches the others);
# this script only drives them together.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ACTION="${1:-}"
shift || true

# Seconds Docker waits after SIGTERM before SIGKILL. config_ui keeps live camera streams
# open, so the default 10 s ends in exit 137.
STOP_TIMEOUT="${MODA_STOP_TIMEOUT:-30}"

usage() {
  echo "Usage: $0 {start|stop|restart|status|logs} [--build | analysis|detector|ui]" >&2
}

# name | project | compose file | env file
project_info() {
  case "$1" in
    analysis) echo "moda-analysis|compose.analysis.yaml|.env.jetson" ;;
    detector) echo "moda-jp6|compose.yaml|.env" ;;
    ui)       echo "moda-config-ui|compose.ui.yaml|.env.ui" ;;
    *) echo "Unknown stack: $1 (analysis|detector|ui)" >&2; exit 2 ;;
  esac
}

compose() {
  local stack="$1"; shift
  local name file env
  IFS='|' read -r name file env <<<"$(project_info "${stack}")"
  docker compose \
    --project-directory "${PROJECT_DIR}" \
    --project-name "${name}" \
    --env-file "${PROJECT_DIR}/${env}" \
    --file "${PROJECT_DIR}/${file}" \
    "$@"
}

require_env_files() {
  local missing=0 stack name file env
  for stack in analysis detector ui; do
    IFS='|' read -r name file env <<<"$(project_info "${stack}")"
    if [[ ! -f "${PROJECT_DIR}/${env}" ]]; then
      echo "Missing ${env} (copy ${env}.example and fill in real values)" >&2
      missing=1
    fi
  done
  (( missing == 0 )) || exit 1
}

preflight() {
  command -v docker >/dev/null 2>&1 || { echo "docker is not installed" >&2; exit 1; }
  docker compose version >/dev/null
  require_env_files
  local stack
  for stack in analysis detector ui; do
    compose "${stack}" config --quiet
  done
  mkdir -p "${PROJECT_DIR}/runtime/weight_result" "${PROJECT_DIR}/config_ui/data"
}

start_all() {
  local build=()
  [[ "${1:-}" == "--build" ]] && build=(--build)
  preflight

  echo "==> [1/4] Kafka"
  # --wait: weight_reader, results_recorder and the detectors all fail without a broker.
  compose detector up --detach --wait kafka

  echo "==> [2/4] Analysis (triton, weight_reader)"
  # --wait: Triton must be healthy before weight_reader reads.
  compose analysis up --detach "${build[@]}" --wait triton
  compose analysis up --detach "${build[@]}" weight_reader

  echo "==> [3/4] Detectors"
  compose detector up --detach "${build[@]}"

  echo "==> [4/4] Config UI"
  compose ui up --detach "${build[@]}"

  status_all
}

stop_all() {
  require_env_files
  echo "==> [1/4] Config UI"
  compose ui down --timeout "${STOP_TIMEOUT}"
  echo "==> [2/4] Detectors"
  # Stop only the camera services here; Kafka keeps running until the consumers are gone.
  local detectors=()
  mapfile -t detectors < <(compose detector config --services | grep -vx kafka)
  if (( ${#detectors[@]} > 0 )); then
    compose detector stop --timeout "${STOP_TIMEOUT}" "${detectors[@]}"
    compose detector rm --force "${detectors[@]}"
  fi
  echo "==> [3/4] Analysis"
  compose analysis down --timeout "${STOP_TIMEOUT}"
  echo "==> [4/4] Kafka"
  # Plain "down" keeps the kafka_data volume (topics and consumer offsets survive).
  compose detector down --timeout "${STOP_TIMEOUT}"
}

status_all() {
  local stack
  for stack in analysis detector ui; do
    echo "--- ${stack}"
    compose "${stack}" ps --all
  done
}

case "${ACTION}" in
  start)   start_all "${1:-}" ;;
  stop)    stop_all ;;
  restart) stop_all; start_all "${1:-}" ;;
  status)  require_env_files; status_all ;;
  logs)
    require_env_files
    if [[ -n "${1:-}" ]]; then
      compose "$1" logs --follow --tail 200
    else
      # All three at once, prefixed by container name.
      compose analysis logs --follow --tail 50 &
      compose detector logs --follow --tail 50 &
      compose ui logs --follow --tail 50 &
      trap 'kill 0' INT TERM
      wait
    fi
    ;;
  *) usage; exit 2 ;;
esac
