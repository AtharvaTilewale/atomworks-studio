#!/usr/bin/env bash
# Run the full browser -> local RFD4 -> browser prototype on one workstation.
set -euo pipefail

studio_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${RFPROTEINA_ROOT:-}" && -n "${ANNOTATION_STUDIO_RFD4_ROOT:-}" ]] \
  && [[ "${RFPROTEINA_ROOT}" != "${ANNOTATION_STUDIO_RFD4_ROOT}" ]]; then
  echo "RFPROTEINA_ROOT and ANNOTATION_STUDIO_RFD4_ROOT must identify the same checkout." >&2
  exit 2
fi
rfd4_root="${RFPROTEINA_ROOT:-${ANNOTATION_STUDIO_RFD4_ROOT:-${studio_root}/external/RFD4-Proteina-dev}}"
[[ -d "${rfd4_root}" ]] || {
  echo "RFD4 checkout not found: ${rfd4_root}" >&2
  echo "Set RFPROTEINA_ROOT or ANNOTATION_STUDIO_RFD4_ROOT." >&2
  exit 1
}
rfd4_root="$(cd "${rfd4_root}" && pwd)"
minimum_rfd4_revision="$(awk -F ' = ' '$1 == "revision" { gsub(/^"|"$/, "", $2); print $2 }' \
  "${studio_root}/providers/rfd4/UPSTREAM.toml")"
git -C "${rfd4_root}" merge-base --is-ancestor "${minimum_rfd4_revision}" HEAD || {
  echo "RFD4 checkout must contain revision ${minimum_rfd4_revision}." >&2
  exit 1
}
runtime_helper="${studio_root}/deploy/lib/inference_runtime.sh"
[[ -r "${runtime_helper}" ]] || {
  echo "Missing RFD4 runtime helper: ${runtime_helper}" >&2
  exit 1
}
# shellcheck source=/dev/null
source "${runtime_helper}"
env_file="${rfd4_root}/.ipd/.env.local"
if [[ -f "${env_file}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${env_file}"
  set +a
fi

if [[ "${ANNOTATION_STUDIO_RFD4_ONLY:-0}" == "1" ]]; then
  unset AF3_APPTAINER_PATH AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR AF3_MODEL_DIR
fi

if ! command -v node >/dev/null 2>&1 || ! node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  process.exit(major > 22 || (major === 22 && minor >= 12) || (major === 20 && minor >= 19) ? 0 : 1);
' 2>/dev/null && command -v brew >/dev/null 2>&1; then
  node_prefix="$(brew --prefix node@22 2>/dev/null || true)"
  [[ -x "${node_prefix}/bin/node" ]] && export PATH="${node_prefix}/bin:${PATH}"
fi

: "${DATA_PATH:?Set DATA_PATH or configure it in ${rfd4_root}/.ipd/.env.local}"
[[ -x "${rfd4_root}/.venv/bin/python" ]] || {
  echo "Run ${rfd4_root}/setup.sh before starting local RFD4 Studio." >&2
  exit 1
}
"${rfd4_root}/.venv/bin/python" "${studio_root}/scripts/check_rfd4_assets.py" \
  --rfd4-root "${rfd4_root}" --data-path "${DATA_PATH}"
command -v nvidia-smi >/dev/null 2>&1 || {
  echo "An NVIDIA GPU and driver are required for local RFD4 generation." >&2
  exit 1
}
nvidia-smi >/dev/null

state_root="${LOCAL_RFD4_STUDIO_STATE_DIR:-${HOME}/.local/share/rfd4-annotation-studio-local}"
mkdir -p \
  "${state_root}/jobs" \
  "${state_root}/sessions" \
  "${state_root}/tmp"
configure_persistent_compile_cache "${state_root}"

gpu_device="${RFD4_GPU_DEVICE:-0}"
export ANNOTATION_STUDIO_HOST="${ANNOTATION_STUDIO_HOST:-127.0.0.1}"
export ANNOTATION_STUDIO_PORT="${ANNOTATION_STUDIO_PORT:-8000}"
export ANNOTATION_STUDIO_JOB_DIR="${state_root}/jobs"
export ANNOTATION_STUDIO_SESSION_DIR="${state_root}/sessions"
export ANNOTATION_STUDIO_MAX_ACTIVE_JOBS=1
export ANNOTATION_STUDIO_RFD4_ROOT="${rfd4_root}"
export RFPROTEINA_ROOT="${rfd4_root}"
export PYTHONPATH="${studio_root}/providers/rfd4:${rfd4_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${LOCAL_RFD4_IMAGE:-}" ]]; then
  command -v docker >/dev/null 2>&1 || {
    echo "Docker is required for LOCAL_RFD4_IMAGE=${LOCAL_RFD4_IMAGE}." >&2
    exit 1
  }
  docker image inspect "${LOCAL_RFD4_IMAGE}" >/dev/null 2>&1 || {
    echo "Local RFD4 image does not exist: ${LOCAL_RFD4_IMAGE}" >&2
    exit 1
  }
  docker_command=(
    docker run --rm
    --gpus "device=${gpu_device}"
    --volume "${rfd4_root}:${rfd4_root}"
    --volume "${DATA_PATH}:${DATA_PATH}:ro"
    --volume "${state_root}:${state_root}"
    # The host AF3 daemon publishes Unix sockets below /tmp/af3_*.
    --volume "/tmp:/tmp"
    --env "DATA_PATH=${DATA_PATH}"
    --env "CLUSTER=local"
    --env "PROJECT_ROOT=${rfd4_root}"
    --env "PYTHONPATH=${rfd4_root}/src:${rfd4_root}/lib/atomworks/src"
    --env "TMPDIR=${state_root}/tmp"
    --env "TORCHINDUCTOR_CACHE_DIR=${TORCHINDUCTOR_CACHE_DIR}"
    --env "TRITON_CACHE_DIR=${TRITON_CACHE_DIR}"
    --env "CUDA_CACHE_PATH=${CUDA_CACHE_PATH}"
    --env "TORCHINDUCTOR_FX_GRAPH_CACHE=${TORCHINDUCTOR_FX_GRAPH_CACHE}"
    --env "TORCH_COMPILE_CACHE_BLOB=${TORCH_COMPILE_CACHE_BLOB}"
    --env AF3_LAUNCHER_SOCKET_TEMPLATE
    --workdir "${rfd4_root}"
    "${LOCAL_RFD4_IMAGE}"
    "${rfd4_root}/src/rfproteina/generate.py"
  )
  printf -v ANNOTATION_STUDIO_RFD4_COMMAND '%q ' "${docker_command[@]}"
  export ANNOTATION_STUDIO_RFD4_COMMAND
elif [[ -n "${LOCAL_RFD4_COMMAND:-}" ]]; then
  export ANNOTATION_STUDIO_RFD4_COMMAND="env CUDA_VISIBLE_DEVICES=${gpu_device} ${LOCAL_RFD4_COMMAND}"
else
  CUDA_VISIBLE_DEVICES="${gpu_device}" CLUSTER=local RFPROTEINA_LIGHTWEIGHT_IMPORT=0 \
    "${rfd4_root}/.venv/bin/python" - <<'PY' || {
import torch
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY
    echo "The host RFD4 environment cannot initialize CUDA." >&2
    echo "Upgrade to a driver compatible with its CUDA stack, or set LOCAL_RFD4_IMAGE to a CUDA-compatible image." >&2
    exit 1
  }
  rfd4_command=(
    env "CUDA_VISIBLE_DEVICES=${gpu_device}" RFPROTEINA_LIGHTWEIGHT_IMPORT=0
    "${rfd4_root}/.venv/bin/python"
    "${rfd4_root}/src/rfproteina/generate.py"
  )
  printf -v ANNOTATION_STUDIO_RFD4_COMMAND '%q ' "${rfd4_command[@]}"
  export ANNOTATION_STUDIO_RFD4_COMMAND
fi
export RFPROTEINA_LIGHTWEIGHT_IMPORT=1

echo "Local RFD4 Studio: http://${ANNOTATION_STUDIO_HOST}:${ANNOTATION_STUDIO_PORT}/"
echo "RFD4 GPU: ${gpu_device}; state: ${state_root}"

run_inference_service_with_af3 \
  "${rfd4_root}" \
  "${state_root}" \
  1 \
  "${studio_root}/run.sh"
