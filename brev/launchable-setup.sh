#!/usr/bin/env bash
set -euo pipefail

project_root="${BREV_RFD4_REPO:-$(git rev-parse --show-toplevel)}"
cd "${project_root}"
: "${DATA_PATH:?Create a DATA_PATH launch parameter pointing at persistent RFD4 checkpoints}"

if ! command -v node >/dev/null 2>&1 || ! node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  process.exit(major > 20 || (major === 20 && minor >= 19) ? 0 : 1);
'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install --yes nodejs
fi

if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "${HOME}/.cargo/env"
fi
rustup target add wasm32-unknown-unknown
command -v wasm-pack >/dev/null 2>&1 || cargo install --locked --version 0.13.1 wasm-pack
corepack enable
corepack prepare pnpm@11.16.0 --activate

# Reuse the repository's only Python/model setup path; "other" avoids SLURM rc configuration.
printf '5\n' | bash "${project_root}/setup.sh"
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

default_state_root="${HOME}/.local/share/rfd4-annotation-studio"
[[ -d "${HOME}/workspace" ]] && default_state_root="${HOME}/workspace/rfd4-annotation-studio"
state_root="${BREV_RFD4_STATE_DIR:-${default_state_root}}"
mkdir -p "${state_root}"

service_launcher="${state_root}/start-service.sh"
{
  echo "#!/usr/bin/env bash"
  echo "set -euo pipefail"
  for variable in \
    DATA_PATH AF3_APPTAINER_PATH AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR \
    AF3_MODEL_DIR AF3_GPU_DEVICES APPTAINER_BIND_PATHS CUDA_VISIBLE_DEVICES; do
    [[ -z "${!variable:-}" ]] || printf 'export %s=%q\n' "${variable}" "${!variable}"
  done
  printf 'export BREV_RFD4_STATE_DIR=%q\n' "${state_root}"
  printf 'exec bash %q\n' "${project_root}/apps/annotation-studio/brev/start.sh"
} >"${service_launcher}"
chmod 700 "${service_launcher}"

service_name="rfd4-annotation-studio.service"
if command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm=)" == "systemd" ]]; then
  current_user="$(id -un)"
  sudo tee "/etc/systemd/system/${service_name}" >/dev/null <<EOF
[Unit]
Description=RFD4 Annotation Studio and AF3 daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${current_user}
WorkingDirectory=${project_root}
Environment=HOME=${HOME}
Environment=PATH=${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/env bash ${service_launcher}
Restart=on-failure
RestartSec=10
TimeoutStartSec=1200

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${service_name}"
else
  pid_file="${state_root}/server.pid"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    kill "$(cat "${pid_file}")"
  fi
  nohup bash "${service_launcher}" >"${state_root}/server.log" 2>&1 &
  echo "$!" >"${pid_file}"
fi

for _ in $(seq 1 180); do
  if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Annotation Studio is ready on port 8000."
    exit 0
  fi
  if command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm=)" == "systemd" ]]; then
    sudo systemctl is-active --quiet "${service_name}" || {
      sudo journalctl --unit "${service_name}" --no-pager --lines 100 >&2
      exit 1
    }
  elif ! kill -0 "$(cat "${state_root}/server.pid")" 2>/dev/null; then
    cat "${state_root}/server.log" >&2
    exit 1
  fi
  sleep 2
done

echo "Annotation Studio did not become ready; see ${state_root}/server.log" >&2
exit 1
