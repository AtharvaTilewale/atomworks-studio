#!/usr/bin/env bash
set -euo pipefail

studio_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${RFPROTEINA_ROOT:-}" && -n "${ANNOTATION_STUDIO_RFD4_ROOT:-}" ]] \
  && [[ "${RFPROTEINA_ROOT}" != "${ANNOTATION_STUDIO_RFD4_ROOT}" ]]; then
  echo "RFPROTEINA_ROOT and ANNOTATION_STUDIO_RFD4_ROOT must identify the same checkout." >&2
  exit 2
fi
rfd4_root="${RFPROTEINA_ROOT:-${ANNOTATION_STUDIO_RFD4_ROOT:-}}"
: "${rfd4_root:?Set RFPROTEINA_ROOT or ANNOTATION_STUDIO_RFD4_ROOT to the RFD4 checkout}"
[[ -d "${rfd4_root}" ]] || {
  echo "RFD4 checkout not found: ${rfd4_root}" >&2
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

[[ -x "${rfd4_root}/.venv/bin/python" ]] || {
  echo "Run ${rfd4_root}/setup.sh before starting the Brev service." >&2
  exit 1
}
: "${DATA_PATH:?Set DATA_PATH to the persistent directory containing RFD4 checkpoints}"
"${rfd4_root}/.venv/bin/python" "${studio_root}/scripts/check_rfd4_assets.py" \
  --rfd4-root "${rfd4_root}" --data-path "${DATA_PATH}"
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
  "${state_root}/sessions" \
  "${state_root}/tmp"
configure_persistent_compile_cache "${state_root}"

export ANNOTATION_STUDIO_HOST="${ANNOTATION_STUDIO_HOST:-0.0.0.0}"
export ANNOTATION_STUDIO_PORT="${ANNOTATION_STUDIO_PORT:-8000}"
# Allow the VS Code / Cursor extension webview to embed the editor. Override to tighten.
export ANNOTATION_STUDIO_FRAME_ANCESTORS="${ANNOTATION_STUDIO_FRAME_ANCESTORS:-'self' vscode-webview://* vscode-webview:}"
export ANNOTATION_STUDIO_JOB_DIR="${ANNOTATION_STUDIO_JOB_DIR:-${state_root}/jobs}"
export ANNOTATION_STUDIO_SESSION_DIR="${ANNOTATION_STUDIO_SESSION_DIR:-${state_root}/sessions}"
export ANNOTATION_STUDIO_MAX_ACTIVE_JOBS=1
export ANNOTATION_STUDIO_RFD4_ROOT="${rfd4_root}"
export RFPROTEINA_ROOT="${rfd4_root}"
export PYTHONPATH="${studio_root}/providers/rfd4:${rfd4_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
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
  docker_command=(
    docker run --rm
    # The host-side OF3 daemon consumes manifests from the shared state mount.
    # Keep their ownership with the service user rather than Docker's root.
    --user "$(id -u):$(id -g)"
    --gpus "device=${gpu_device}"
    --volume "${rfd4_root}:${rfd4_root}"
    --volume "${DATA_PATH}:${DATA_PATH}:ro"
    --volume "${state_root}:${state_root}"
    --volume "${state_root}/brev.env:${rfd4_root}/.ipd/.env.brev:ro"
    # The host AF3 daemon publishes Unix sockets below /tmp/af3_*.
    --volume "/tmp:/tmp"
    --env "DATA_PATH=${DATA_PATH}"
    --env "CLUSTER=brev"
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
    "${BREV_RFD4_IMAGE}"
    "${rfd4_root}/src/rfproteina/generate.py"
  )
  printf -v ANNOTATION_STUDIO_RFD4_COMMAND '%q ' "${docker_command[@]}"
  export ANNOTATION_STUDIO_RFD4_COMMAND
else
  if [[ -z "${ANNOTATION_STUDIO_RFD4_COMMAND:-}" ]]; then
    rfd4_command=(
      env RFPROTEINA_LIGHTWEIGHT_IMPORT=0
      "${rfd4_root}/.venv/bin/python"
      "${rfd4_root}/src/rfproteina/generate.py"
    )
    printf -v ANNOTATION_STUDIO_RFD4_COMMAND '%q ' "${rfd4_command[@]}"
    export ANNOTATION_STUDIO_RFD4_COMMAND
  fi
fi
export RFPROTEINA_LIGHTWEIGHT_IMPORT=1

run_inference_service_with_af3 \
  "${rfd4_root}" \
  "${state_root}" \
  0 \
  "${studio_root}/run.sh"
