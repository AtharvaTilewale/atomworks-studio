#!/usr/bin/env bash
set -euo pipefail

studio_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${RFPROTEINA_ROOT:-}" && -n "${ANNOTATION_STUDIO_RFD4_ROOT:-}" ]] \
  && [[ "${RFPROTEINA_ROOT}" != "${ANNOTATION_STUDIO_RFD4_ROOT}" ]]; then
  echo "RFPROTEINA_ROOT and ANNOTATION_STUDIO_RFD4_ROOT must identify the same checkout." >&2
  exit 2
fi
rfd4_revision="0e9050ca426a74069b99b63ca4a6520b05c2f8c7"
rfd4_root="${RFPROTEINA_ROOT:-${ANNOTATION_STUDIO_RFD4_ROOT:-${studio_root}/../RFD4-Proteina-dev}}"
if [[ ! -d "${rfd4_root}/.git" ]]; then
  git clone https://github.com/baker-laboratory/RFD4-Proteina-dev.git "${rfd4_root}"
  git -C "${rfd4_root}" checkout --detach "${rfd4_revision}"
fi
rfd4_root="$(cd "${rfd4_root}" && pwd)"
git -C "${rfd4_root}" merge-base --is-ancestor "${rfd4_revision}" HEAD || {
  echo "RFD4 checkout must contain pinned revision ${rfd4_revision}." >&2
  exit 1
}
[[ -f "${rfd4_root}/setup.sh" ]] || {
  echo "Not an RFD4 checkout (missing setup.sh): ${rfd4_root}" >&2
  exit 1
}
cd "${studio_root}"
: "${DATA_PATH:?Create a DATA_PATH launch parameter pointing at persistent RFD4 checkpoints}"

if ! command -v node >/dev/null 2>&1 || ! node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  process.exit(major > 22 || (major === 22 && minor >= 12) || (major === 20 && minor >= 19) ? 0 : 1);
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
mkdir -p "${HOME}/.local/bin"
corepack enable --install-directory "${HOME}/.local/bin"
corepack prepare pnpm@11.16.0 --activate
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

# Reuse the repository's only Python/model setup path; "other" avoids SLURM rc configuration.
printf '5\n' | bash "${rfd4_root}/setup.sh"
bash "${studio_root}/scripts/build_patinae_viewer.sh"
pnpm --dir "${studio_root}" install --frozen-lockfile
uv sync --project "${studio_root}" --all-packages --all-extras --all-groups

BREV_RFD4_IMAGE="${BREV_RFD4_IMAGE:-latent-dev:cuda12}"
if ! docker image inspect "${BREV_RFD4_IMAGE}" >/dev/null 2>&1; then
  RFPROTEINA_CUDA_STACK=cuda12 \
    bash "${rfd4_root}/scripts/build_latent_dev_docker.sh" "${BREV_RFD4_IMAGE}"
fi

default_state_root="${HOME}/.local/share/rfd4-annotation-studio"
[[ -d "${HOME}/workspace" ]] && default_state_root="${HOME}/workspace/rfd4-annotation-studio"
state_root="${BREV_RFD4_STATE_DIR:-${default_state_root}}"
mkdir -p "${state_root}"

service_launcher="${state_root}/start-service.sh"
{
  echo "#!/usr/bin/env bash"
  echo "set -euo pipefail"
  for variable in \
    DATA_PATH BREV_RFD4_IMAGE AF3_APPTAINER_PATH AF3_SCRIPTS_DIR AF3_JAX_CACHE_DIR \
    AF3_MODEL_DIR AF3_GPU_DEVICES APPTAINER_BIND_PATHS CUDA_VISIBLE_DEVICES; do
    [[ -z "${!variable:-}" ]] || printf 'export %s=%q\n' "${variable}" "${!variable}"
  done
  printf 'export RFPROTEINA_ROOT=%q\n' "${rfd4_root}"
  printf 'export ANNOTATION_STUDIO_RFD4_ROOT=%q\n' "${rfd4_root}"
  printf 'export BREV_RFD4_STATE_DIR=%q\n' "${state_root}"
  printf 'exec bash %q\n' "${studio_root}/deploy/brev/start.sh"
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
WorkingDirectory=${studio_root}
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
  sudo systemctl enable "${service_name}"
  sudo systemctl restart "${service_name}"
else
  pid_file="${state_root}/server.pid"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    kill "$(cat "${pid_file}")"
  fi
  nohup bash "${service_launcher}" >"${state_root}/server.log" 2>&1 &
  echo "$!" >"${pid_file}"
fi

for ((attempt = 0; attempt < 180; attempt++)); do
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
