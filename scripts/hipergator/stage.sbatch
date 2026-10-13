#!/usr/bin/env bash
# Resources and dependencies are supplied explicitly by launch.py.
set -euo pipefail
umask 077
phase="${1:?phase required}"
run="${2:?absolute campaign path required}"
repo="${3:?absolute checkout path required}"
cd "$repo"
export UV_PROJECT_ENVIRONMENT="$run/client-venv"
export UV_CACHE_DIR="$repo/.hpg-cache/uv"
export UV_LINK_MODE=copy
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
if [[ "$phase" == prepare ]]; then
  export CUDA_VISIBLE_DEVICES=""
  uv sync --project "$repo" --locked --python 3.13 --no-dev
  exec "$run/client-venv/bin/python" scripts/hipergator/prepare.py "$run"
elif [[ "$phase" == gpu ]]; then
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
  export UV_OFFLINE=1
  exec "$run/client-venv/bin/python" scripts/hipergator/gpu.py "$run"
elif [[ "$phase" == finish ]]; then
  export CUDA_VISIBLE_DEVICES=""
  # Completion receipts must still work if CPU preparation failed before sync.
  if [[ -x "$run/client-venv/bin/python" ]]; then
    exec "$run/client-venv/bin/python" scripts/hipergator/finish.py "$run"
  fi
  exec python3 scripts/hipergator/finish.py "$run"
else
  echo "Unknown stage: $phase" >&2; exit 2
fi
