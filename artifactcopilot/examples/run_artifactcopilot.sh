#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <artifact_repo> <paper_source> [run_root]"
  exit 1
fi

ARTIFACT_REPO="$1"
PAPER_SOURCE="$2"
RUN_ROOT="${3:-}"

if [[ -n "${RUN_ROOT}" ]]; then
  python -m artifact_submission.artifactcopilot \
    "${ARTIFACT_REPO}" \
    --paper "${PAPER_SOURCE}" \
    --run-root "${RUN_ROOT}"
else
  python -m artifact_submission.artifactcopilot \
    "${ARTIFACT_REPO}" \
    --paper "${PAPER_SOURCE}"
fi

