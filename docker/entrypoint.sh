#!/usr/bin/env bash
# Detector entrypoint: build missing TensorRT engines into /cache/engines, then start
# the pipeline. Arguments are passed to "python3 -m traffic_core" (e.g. --site <json>,
# repeated once per site when one container runs several lanes).
set -euo pipefail

sites=()
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--site" && $((i + 1)) -lt ${#args[@]} ]]; then
    sites+=("${args[i + 1]}")
  fi
done

if (( ${#sites[@]} == 0 )); then
  echo "No --site argument; skipping TensorRT engine preparation" >&2
fi
# Sequential on purpose: sites share the plate engines (prepare_engines skips engines
# that already exist), and two trtexec builds at once would double the peak memory.
for site in "${sites[@]}"; do
  python3 /app/docker/prepare_engines.py "${site}"
done

exec python3 -m traffic_core "$@"
