#!/usr/bin/env bash
# Shared direct-inference runtime used outside SLURM.
#
# launch_infer.sh emits equivalent refolding-daemon orchestration into its SLURM
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
    local default_gpu_devices="${3:?default refolding GPU devices are required}"
    shift 3
    (( $# > 0 )) || {
        echo "A service command is required." >&2
        return 2
    }

    local required_variables=(
        AF3_IMAGE
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
        [[ -f "${AF3_IMAGE}" ]] || {
            echo "AF3_IMAGE is not a file: ${AF3_IMAGE}" >&2
            return 1
        }
        for variable in AF3_JAX_CACHE_DIR AF3_MODEL_DIR; do
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
        local daemon_devices="${AF3_GPU_DEVICES:-${default_gpu_devices}}"
        local daemon_launcher="${project_root}/src/rfproteina/af3/af3_daemon.py"
        local launch_help
        launch_help="$("${python}" "${daemon_launcher}" launch --help)"
        if grep -q -- '--gpu-devices' <<<"${launch_help}"; then
            eval "$(
                "${python}" "${daemon_launcher}" launch \
                    --gpu-devices "${daemon_devices}" \
                    --log-dir "${state_root}/af3-logs" \
                    --background
            )"
        else
            # The Studio-compatible RFD4 branch launches ranks 0..N-1. Scope
            # CUDA visibility so its rank 0 maps to the requested physical GPU.
            local legacy_num_gpus
            legacy_num_gpus="$(tr ',' '\n' <<<"${daemon_devices}" | wc -l | tr -d ' ')"
            eval "$(
                CUDA_VISIBLE_DEVICES="${daemon_devices}" \
                    "${python}" "${daemon_launcher}" launch \
                        --num-gpus "${legacy_num_gpus}" \
                        --log-dir "${state_root}/af3-logs" \
                        --background
            )"
        fi
        af3_supervisor_pid="${AF3_DAEMONS_PID:?AF3 launcher did not return a supervisor PID}"
        echo "Refolding daemon started on GPU device(s) ${daemon_devices}."
    else
        echo "Refolding daemon is disabled; refolding metrics and model-guided search are unavailable."
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
