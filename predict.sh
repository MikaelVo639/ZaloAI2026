#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m datamate predict \
  --data-dir "${DATA_DIR:-/data}" \
  --output-dir "${RESULT_DIR:-/result}" \
  --config "${CONFIG_PATH:-configs/baseline.toml}" "$@"
