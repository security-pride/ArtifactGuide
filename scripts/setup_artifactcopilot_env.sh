#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-artifactcopilot-ae}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/artifactcopilot/environment.yml"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda not found in PATH"
  exit 1
fi

echo "[1/3] Creating conda environment: ${ENV_NAME}"
conda --no-plugins env remove -n "${ENV_NAME}" -y >/dev/null 2>&1 || true
conda --no-plugins env create -n "${ENV_NAME}" -f "${ENV_FILE}"

echo "[2/3] Verifying Python dependencies"
conda --no-plugins run -n "${ENV_NAME}" python - <<'PY'
import requests
import yaml
print("artifactcopilot dependency check passed")
PY

echo "[3/3] Verifying ArtifactCopilot CLI"
cd "${REPO_ROOT}"
conda --no-plugins run -n "${ENV_NAME}" python -m artifactcopilot --help >/dev/null

echo "Environment ready: ${ENV_NAME}"
