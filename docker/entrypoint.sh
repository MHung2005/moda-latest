#!/usr/bin/env bash
# Detector entrypoint: build missing TensorRT engines into /cache/engines, then start
# the pipeline. Arguments are passed to "python3 -m traffic_core" (e.g. --site <json>).
set -euo pipefail

site=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--site" && $((i + 1)) -lt ${#args[@]} ]]; then
    site="${args[i + 1]}"
  fi
done

if [[ -n "${site}" ]]; then
  python3 /app/docker/prepare_engines.py "${site}"
else
  echo "No --site argument; skipping TensorRT engine preparation" >&2
fi

exec python3 -m traffic_core "$@"
