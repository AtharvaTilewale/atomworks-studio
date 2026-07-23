#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_root="$(cd "${studio_dir}/../.." && pwd)"

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
mkdir -p "${state_root}/jobs" "${state_root}/sessions"

export ANNOTATION_STUDIO_HOST="${ANNOTATION_STUDIO_HOST:-0.0.0.0}"
export ANNOTATION_STUDIO_PORT="${ANNOTATION_STUDIO_PORT:-8000}"
export ANNOTATION_STUDIO_JOB_DIR="${ANNOTATION_STUDIO_JOB_DIR:-${state_root}/jobs}"
export ANNOTATION_STUDIO_SESSION_DIR="${ANNOTATION_STUDIO_SESSION_DIR:-${state_root}/sessions}"
export ANNOTATION_STUDIO_RFD4_ROOT="${project_root}"
export ANNOTATION_STUDIO_RFD4_COMMAND="${ANNOTATION_STUDIO_RFD4_COMMAND:-env RFPROTEINA_LIGHTWEIGHT_IMPORT=0 ${project_root}/.venv/bin/python ${project_root}/src/rfproteina/generate.py}"
export RFPROTEINA_LIGHTWEIGHT_IMPORT=1

af3_variables=(AF3_APPTAINER_PATH AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR AF3_MODEL_DIR)
configured_af3_variables=0
for variable in "${af3_variables[@]}"; do
  [[ -n "${!variable:-}" ]] && configured_af3_variables=$((configured_af3_variables + 1))
done
if (( configured_af3_variables != 0 && configured_af3_variables != ${#af3_variables[@]} )); then
  echo "Configure all AF3 variables (${af3_variables[*]}) or none of them." >&2
  exit 1
fi

af3_supervisor_pid=""
if (( configured_af3_variables == ${#af3_variables[@]} )); then
  command -v apptainer >/dev/null 2>&1 || {
    echo "Apptainer is required when the AF3 daemon is configured." >&2
    exit 1
  }
  [[ -f "${AF3_APPTAINER_PATH}" ]] || {
    echo "AF3_APPTAINER_PATH is not a file: ${AF3_APPTAINER_PATH}" >&2
    exit 1
  }
  for directory_variable in AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR AF3_MODEL_DIR; do
    [[ -d "${!directory_variable}" ]] || {
      echo "${directory_variable} is not a directory: ${!directory_variable}" >&2
      exit 1
    }
  done
  mkdir -p "${state_root}/af3-logs"
  eval "$(
    "${project_root}/.venv/bin/python" "${project_root}/src/rfproteina/af3/af3_daemon.py" launch \
      --gpu-devices "${AF3_GPU_DEVICES:-0}" \
      --log-dir "${state_root}/af3-logs" \
      --background
  )"
  af3_supervisor_pid="${AF3_DAEMONS_PID}"
  echo "AF3 daemon started on GPU device(s) ${AF3_GPU_DEVICES:-0}."
else
  echo "AF3 daemon is disabled; refolding metrics and AF3-guided search are unavailable."
fi

server_pid=""
cleanup() {
  trap - EXIT INT TERM
  [[ -z "${server_pid}" ]] || kill -TERM "${server_pid}" 2>/dev/null || true
  [[ -z "${server_pid}" ]] || wait "${server_pid}" 2>/dev/null || true
  [[ -z "${af3_supervisor_pid}" ]] || kill -TERM "${af3_supervisor_pid}" 2>/dev/null || true
  [[ -z "${af3_supervisor_pid}" ]] || wait "${af3_supervisor_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"${studio_dir}/run.sh" &
server_pid=$!
wait "${server_pid}"
