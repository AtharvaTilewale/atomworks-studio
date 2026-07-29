#!/usr/bin/env bash
# Shared direct-inference runtime used outside SLURM.
#
# launch_infer.sh emits equivalent AF3 daemon orchestration into its SLURM
# script. Local and Brev Studio use this helper because they run directly on
# one host and do not have sbatch/srun.

configure_persistent_compile_cache() {
    local state_root="${1:?state directory is required}"

    mkdir -p \
        "${state_root}/compile-cache/torchinductor" \
        "${state_root}/compile-cache/triton" \
        "${state_root}/compile-cache/cuda" \
        "${state_root}/compile-cache/megacache"

    export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-${state_root}/compile-cache/torchinductor}"
    export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-${state_root}/compile-cache/triton}"
    export CUDA_CACHE_PATH="${CUDA_CACHE_PATH:-${state_root}/compile-cache/cuda}"
    export TORCHINDUCTOR_FX_GRAPH_CACHE="${TORCHINDUCTOR_FX_GRAPH_CACHE:-1}"
    # torch.compile Mega-Cache blob. generate.py's load_compile_cache/save_compile_cache
    # only act when TORCH_COMPILE_CACHE_BLOB is set: it persists the compiled per-bucket
    # graphs so a cold generate.py process starts warm instead of recompiling. _blob_path()
    # scopes this base per user and per torch build, so a single base path is safe to share.
    export TORCH_COMPILE_CACHE_BLOB="${TORCH_COMPILE_CACHE_BLOB:-${state_root}/compile-cache/megacache/blob}"
}

run_inference_service_with_af3() {
    local project_root="${1:?project root is required}"
    local state_root="${2:?state directory is required}"
    local default_gpu_devices="${3:?default AF3 GPU devices are required}"
    shift 3
    (( $# > 0 )) || {
        echo "A service command is required." >&2
        return 2
    }

    local required_variables=(
        AF3_APPTAINER_PATH
        AF3_SCRIPTS_DIR
        AF3_JAX_CACHE_DIR
        AF3_MODEL_DIR
    )
    local configured_variables=0
    local variable
    for variable in "${required_variables[@]}"; do
        [[ -n "${!variable:-}" ]] && configured_variables=$((configured_variables + 1))
    done
    if (( configured_variables != 0 && configured_variables != ${#required_variables[@]} )); then
        echo "Configure all AF3 variables (${required_variables[*]}) or none of them." >&2
        return 1
    fi

    if (( configured_variables == ${#required_variables[@]} )); then
        command -v apptainer >/dev/null 2>&1 || {
            echo "Apptainer is required when the AF3 daemon is configured." >&2
            return 1
        }
        [[ -f "${AF3_APPTAINER_PATH}" ]] || {
            echo "AF3_APPTAINER_PATH is not a file: ${AF3_APPTAINER_PATH}" >&2
            return 1
        }
        for variable in AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR AF3_MODEL_DIR; do
            [[ -d "${!variable}" ]] || {
                echo "${variable} is not a directory: ${!variable}" >&2
                return 1
            }
        done
        local python="${RFPROTEINA_PYTHON:-${project_root}/.venv/bin/python}"
        [[ -x "${python}" ]] || {
            echo "RFProteina Python is not executable: ${python}" >&2
            return 1
        }
    fi

    local af3_supervisor_pid=""
    local service_pid=""
    _inference_runtime_cleanup() {
        trap - EXIT INT TERM
        [[ -z "${service_pid}" ]] || kill -TERM "${service_pid}" 2>/dev/null || true
        [[ -z "${service_pid}" ]] || wait "${service_pid}" 2>/dev/null || true
        [[ -z "${af3_supervisor_pid}" ]] || kill -TERM "${af3_supervisor_pid}" 2>/dev/null || true
        [[ -z "${af3_supervisor_pid}" ]] || wait "${af3_supervisor_pid}" 2>/dev/null || true
    }
    trap _inference_runtime_cleanup EXIT
    trap '_inference_runtime_cleanup; exit 0' INT TERM

    if (( configured_variables == ${#required_variables[@]} )); then
        mkdir -p "${state_root}/af3-logs"
        eval "$(
            "${python}" "${project_root}/src/rfproteina/af3/af3_daemon.py" launch \
                --gpu-devices "${AF3_GPU_DEVICES:-${default_gpu_devices}}" \
                --log-dir "${state_root}/af3-logs" \
                --background
        )"
        af3_supervisor_pid="${AF3_DAEMONS_PID}"
        echo "AF3 daemon started on GPU device(s) ${AF3_GPU_DEVICES:-${default_gpu_devices}}."
    else
        echo "AF3 daemon is disabled; refolding metrics and AF3-guided search are unavailable."
    fi

    "$@" &
    service_pid=$!
    local status=0
    wait "${service_pid}" || status=$?
    service_pid=""
    _inference_runtime_cleanup
    unset -f _inference_runtime_cleanup
    return "${status}"
}
