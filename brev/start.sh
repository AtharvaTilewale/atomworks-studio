#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_root="$(cd "${studio_dir}/../.." && pwd)"
# shellcheck source=../../../scripts/inference_runtime.sh
source "${project_root}/scripts/inference_runtime.sh"

[[ -x "${project_root}/.venv/bin/python" ]] || {
  echo "Run ${project_root}/setup.sh before starting the Brev service." >&2
  exit 1
}
: "${DATA_PATH:?Set DATA_PATH to the persistent directory containing RFD4 checkpoints}"
command -v nvidia-smi >/dev/null 2>&1 || {
  echo "NVIDIA drivers are required for RFD4 generation." >&2
  exit 1
}
nvidia-smi >/dev/null

default_state_root="${HOME}/.local/share/rfd4-annotation-studio"
[[ -d "${HOME}/workspace" ]] && default_state_root="${HOME}/workspace/rfd4-annotation-studio"
state_root="${BREV_RFD4_STATE_DIR:-${default_state_root}}"
mkdir -p \
  "${state_root}/jobs" \
  "${state_root}/sessions"
configure_persistent_compile_cache "${state_root}"

export ANNOTATION_STUDIO_HOST="${ANNOTATION_STUDIO_HOST:-0.0.0.0}"
export ANNOTATION_STUDIO_PORT="${ANNOTATION_STUDIO_PORT:-8000}"
export ANNOTATION_STUDIO_JOB_DIR="${ANNOTATION_STUDIO_JOB_DIR:-${state_root}/jobs}"
export ANNOTATION_STUDIO_SESSION_DIR="${ANNOTATION_STUDIO_SESSION_DIR:-${state_root}/sessions}"
export ANNOTATION_STUDIO_RFD4_ROOT="${project_root}"
if [[ -n "${BREV_RFD4_IMAGE:-}" ]]; then
  command -v docker >/dev/null 2>&1 || {
    echo "Docker is required for BREV_RFD4_IMAGE=${BREV_RFD4_IMAGE}." >&2
    exit 1
  }
  docker image inspect "${BREV_RFD4_IMAGE}" >/dev/null 2>&1 || {
    echo "Brev RFD4 image does not exist: ${BREV_RFD4_IMAGE}" >&2
    exit 1
  }
  gpu_device="${CUDA_VISIBLE_DEVICES:-0}"
  touch "${state_root}/brev.env"
  export ANNOTATION_STUDIO_RFD4_COMMAND="docker run --rm --gpus device=${gpu_device} --volume ${project_root}:${project_root} --volume ${DATA_PATH}:${DATA_PATH}:ro --volume ${state_root}:${state_root} --volume ${state_root}/brev.env:${project_root}/.ipd/.env.brev:ro --env DATA_PATH=${DATA_PATH} --env CLUSTER=brev --env PROJECT_ROOT=${project_root} --env PYTHONPATH=${project_root}/src:${project_root}/lib/atomworks/src --env TORCHINDUCTOR_CACHE_DIR=${TORCHINDUCTOR_CACHE_DIR} --env TRITON_CACHE_DIR=${TRITON_CACHE_DIR} --env CUDA_CACHE_PATH=${CUDA_CACHE_PATH} --env TORCHINDUCTOR_FX_GRAPH_CACHE=${TORCHINDUCTOR_FX_GRAPH_CACHE} --workdir ${project_root} ${BREV_RFD4_IMAGE} ${project_root}/src/rfproteina/generate.py"
else
  export ANNOTATION_STUDIO_RFD4_COMMAND="${ANNOTATION_STUDIO_RFD4_COMMAND:-env RFPROTEINA_LIGHTWEIGHT_IMPORT=0 ${project_root}/.venv/bin/python ${project_root}/src/rfproteina/generate.py}"
fi
export RFPROTEINA_LIGHTWEIGHT_IMPORT=1

run_inference_service_with_af3 \
  "${project_root}" \
  "${state_root}" \
  0 \
  "${studio_dir}/run.sh"
