#!/usr/bin/env bash
# Run the full browser -> local RFD4 -> browser prototype on one workstation.
set -euo pipefail

studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_root="$(cd "${studio_dir}/../.." && pwd)"
# shellcheck source=../../../scripts/inference_runtime.sh
source "${project_root}/scripts/inference_runtime.sh"
env_file="${project_root}/.ipd/.env.local"
if [[ -f "${env_file}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${env_file}"
  set +a
fi

if [[ "${ANNOTATION_STUDIO_RFD4_ONLY:-0}" == "1" ]]; then
  unset AF3_APPTAINER_PATH AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR AF3_MODEL_DIR
fi

if ! node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  process.exit(major > 22 || (major === 22 && minor >= 12) || (major === 20 && minor >= 19) ? 0 : 1);
' 2>/dev/null && command -v brew >/dev/null 2>&1; then
  node_prefix="$(brew --prefix node@22 2>/dev/null || true)"
  [[ -x "${node_prefix}/bin/node" ]] && export PATH="${node_prefix}/bin:${PATH}"
fi

: "${DATA_PATH:?Set DATA_PATH or configure it in .ipd/.env.local}"
checkpoints=(
  "${DATA_PATH}/latent_checkpoints/ldm_checkpoints/arch_sweep_2026_07_03_pair_trimult_every2_720k-ema.ckpt"
  "${DATA_PATH}/latent_checkpoints/ae_checkpoints/AE_ucond_512.ckpt"
)
for checkpoint in "${checkpoints[@]}"; do
  [[ -f "${checkpoint}" ]] || {
    echo "Missing checkpoint: ${checkpoint}" >&2
    echo "Run: bash scripts/download_example_data.sh \"${DATA_PATH}\"" >&2
    exit 1
  }
done
command -v nvidia-smi >/dev/null 2>&1 || {
  echo "An NVIDIA GPU and driver are required for local RFD4 generation." >&2
  exit 1
}
nvidia-smi >/dev/null

state_root="${LOCAL_RFD4_STUDIO_STATE_DIR:-${HOME}/.local/share/rfd4-annotation-studio-local}"
mkdir -p \
  "${state_root}/jobs" \
  "${state_root}/sessions"
configure_persistent_compile_cache "${state_root}"

gpu_device="${RFD4_GPU_DEVICE:-0}"
export ANNOTATION_STUDIO_HOST="${ANNOTATION_STUDIO_HOST:-127.0.0.1}"
export ANNOTATION_STUDIO_PORT="${ANNOTATION_STUDIO_PORT:-8000}"
export ANNOTATION_STUDIO_JOB_DIR="${state_root}/jobs"
export ANNOTATION_STUDIO_SESSION_DIR="${state_root}/sessions"
export ANNOTATION_STUDIO_RFD4_ROOT="${project_root}"
if [[ -n "${LOCAL_RFD4_IMAGE:-}" ]]; then
  command -v docker >/dev/null 2>&1 || {
    echo "Docker is required for LOCAL_RFD4_IMAGE=${LOCAL_RFD4_IMAGE}." >&2
    exit 1
  }
  docker image inspect "${LOCAL_RFD4_IMAGE}" >/dev/null 2>&1 || {
    echo "Local RFD4 image does not exist: ${LOCAL_RFD4_IMAGE}" >&2
    exit 1
  }
  export ANNOTATION_STUDIO_RFD4_COMMAND="docker run --rm --gpus device=${gpu_device} --volume ${project_root}:${project_root} --volume ${DATA_PATH}:${DATA_PATH}:ro --volume ${state_root}:${state_root} --volume /tmp:/tmp --env DATA_PATH=${DATA_PATH} --env CLUSTER=local --env PROJECT_ROOT=${project_root} --env PYTHONPATH=${project_root}/src:${project_root}/lib/atomworks/src --env TORCHINDUCTOR_CACHE_DIR=${TORCHINDUCTOR_CACHE_DIR} --env TRITON_CACHE_DIR=${TRITON_CACHE_DIR} --env CUDA_CACHE_PATH=${CUDA_CACHE_PATH} --env TORCHINDUCTOR_FX_GRAPH_CACHE=${TORCHINDUCTOR_FX_GRAPH_CACHE} --env AF3_LAUNCHER_SOCKET_TEMPLATE --workdir ${project_root} ${LOCAL_RFD4_IMAGE} ${project_root}/src/rfproteina/generate.py"
elif [[ -n "${LOCAL_RFD4_COMMAND:-}" ]]; then
  export ANNOTATION_STUDIO_RFD4_COMMAND="env CUDA_VISIBLE_DEVICES=${gpu_device} ${LOCAL_RFD4_COMMAND}"
else
  CUDA_VISIBLE_DEVICES="${gpu_device}" CLUSTER=local RFPROTEINA_LIGHTWEIGHT_IMPORT=0 \
    "${project_root}/.venv/bin/python" - <<'PY' || {
import torch
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY
    echo "The host RFD4 environment cannot initialize CUDA." >&2
    echo "Upgrade to a driver compatible with its CUDA stack, or set LOCAL_RFD4_IMAGE to a CUDA-compatible image." >&2
    exit 1
  }
  export ANNOTATION_STUDIO_RFD4_COMMAND="env CUDA_VISIBLE_DEVICES=${gpu_device} RFPROTEINA_LIGHTWEIGHT_IMPORT=0 ${project_root}/.venv/bin/python ${project_root}/src/rfproteina/generate.py"
fi
export RFPROTEINA_LIGHTWEIGHT_IMPORT=1

echo "Local RFD4 Studio: http://${ANNOTATION_STUDIO_HOST}:${ANNOTATION_STUDIO_PORT}/"
echo "RFD4 GPU: ${gpu_device}; state: ${state_root}"

run_inference_service_with_af3 \
  "${project_root}" \
  "${state_root}" \
  1 \
  "${studio_dir}/run.sh"
