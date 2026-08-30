#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
artifact_dir="${root}/artifacts/brev-${stamp}"
mkdir -p "${artifact_dir}"

: "${DATA_PATH:?Set DATA_PATH to the persistent RFD4 assets}"
rfd4_root="${ANNOTATION_STUDIO_RFD4_ROOT:-${RFPROTEINA_ROOT:-${root}/external/RFD4-Proteina-dev}}"
uv run --project "${root}" python "${root}/scripts/check_rfd4_assets.py" \
  --rfd4-root "${rfd4_root}" \
  --data-path "${DATA_PATH}" \
  --check-bucket \
  --json "${artifact_dir}/rfd4-assets.json"

base="${ANNOTATION_STUDIO_URL:-http://127.0.0.1:8000}"
curl --fail --silent "${base}/api/health" >"${artifact_dir}/health.json"
curl --fail --silent "${base}/api/ready" >"${artifact_dir}/ready.json"
nvidia-smi --query-compute-apps=pid,process_name,used_memory \
  --format=csv,noheader >"${artifact_dir}/gpu-processes.csv"

export ANNOTATION_STUDIO_URL="${base}"
export ATOMWORKS_STUDIO_ACCEPTANCE_ARTIFACT="${artifact_dir}/result.json"
uv run --project "${root}" python "${root}/scripts/accept_brev.py" \
  2>&1 | tee "${artifact_dir}/acceptance.log"

if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.ID}}\t{{.Image}}\t{{.Status}}' >"${artifact_dir}/containers.txt"
fi
printf '%s\n' "${stamp}" >"${artifact_dir}/revision.txt"
