#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${root}/artifacts/local"
accept_port="${ANNOTATION_STUDIO_ACCEPT_PORT:-18000}"
accept_url="http://127.0.0.1:${accept_port}"
mkdir -p "${artifact_dir}"
rfd4_root="${ANNOTATION_STUDIO_RFD4_ROOT:-${RFPROTEINA_ROOT:-${root}/../RFD4-Proteina-dev}}"
if [[ -d "${rfd4_root}/src/rfproteina" ]]; then
  export PYTHONPATH="${rfd4_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi
if command -v pnpm >/dev/null 2>&1; then
  pnpm_cmd=(pnpm)
else
  pnpm_cmd=(corepack pnpm)
fi

bash "${root}/scripts/build_patinae_viewer.sh"
"${pnpm_cmd[@]}" --dir "${root}" install --frozen-lockfile
cargo test --manifest-path "${root}/Cargo.toml" --locked
uv run --project "${root}" --all-extras pytest -q "${root}/tests" | tee "${artifact_dir}/pytest.log"
"${pnpm_cmd[@]}" --dir "${root}/packages/web" test:unit | tee "${artifact_dir}/web-unit.log"
"${pnpm_cmd[@]}" --dir "${root}/packages/web" build | tee "${artifact_dir}/web-build.log"
python "${root}/scripts/check_web_budget.py" | tee "${artifact_dir}/web-budget.log"

ANNOTATION_STUDIO_PORT="${accept_port}" bash "${root}/run.sh" >"${artifact_dir}/server.log" 2>&1 &
server_pid=$!
trap 'kill "${server_pid}" >/dev/null 2>&1 || true' EXIT
for _ in {1..60}; do
  if curl --fail --silent "${accept_url}/api/ready" >"${artifact_dir}/ready.json"; then
    break
  fi
  sleep 1
done
curl --fail --silent "${accept_url}/api/ready" >/dev/null

for viewer in patinae molstar; do
  VIEWER_MODE="${viewer}" ANNOTATION_STUDIO_URL="${accept_url}" \
    "${pnpm_cmd[@]}" --dir "${root}/packages/web" test:e2e \
    | tee "${artifact_dir}/e2e-${viewer}.log"
done
ANNOTATION_STUDIO_URL="${accept_url}" node "${root}/packages/web/tests/renderer-failure.mjs" \
  | tee "${artifact_dir}/renderer-failure.log"

if command -v docker >/dev/null 2>&1 && command -v gh >/dev/null 2>&1; then
  export GH_TOKEN="${GH_TOKEN:-$(gh auth token)}"
  docker build --secret id=github_token,env=GH_TOKEN \
    --file "${root}/deploy/Dockerfile" --tag atomworks-studio:accept "${root}"
  container_id="$(
    docker run --detach --read-only --tmpfs /tmp --tmpfs /app/state \
      --publish 18001:8000 atomworks-studio:accept
  )"
  trap 'docker rm --force "${container_id}" >/dev/null 2>&1 || true; kill "${server_pid}" >/dev/null 2>&1 || true' EXIT
  for _ in {1..60}; do
    if curl --fail --silent http://127.0.0.1:18001/api/ready >"${artifact_dir}/container-ready.json"; then
      break
    fi
    sleep 1
  done
  [[ "$(docker inspect --format '{{.Config.User}}' "${container_id}")" != "0" ]]
  curl --fail --silent http://127.0.0.1:18001/api/ready >/dev/null
  docker rm --force "${container_id}" >/dev/null
fi
