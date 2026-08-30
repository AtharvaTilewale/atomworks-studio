#!/usr/bin/env bash
set -euo pipefail

preflight_only="${ANNOTATION_STUDIO_SETUP_PREFLIGHT_ONLY:-0}"
if [[ "${preflight_only}" != "1" ]]; then
  : "${DATA_PATH:?Set DATA_PATH to persistent RFD4/AF3 assets}"
fi

clean_root="${ATOMWORKS_STUDIO_CLEAN_ROOT:-${HOME}/workspace/atomworks-studio-clean-acceptance}"
source_repository="${ATOMWORKS_STUDIO_CLEAN_REPOSITORY:-https://github.com/baker-laboratory/atomworks-studio.git}"
source_ref="${ATOMWORKS_STUDIO_CLEAN_REF:-main}"
case "${clean_root}" in
  *atomworks-studio-clean-acceptance) ;;
  *) echo "Refusing unsafe clean root: ${clean_root}" >&2; exit 2 ;;
esac

rm -rf "${clean_root}"
git clone --no-checkout "${source_repository}" "${clean_root}"
git -C "${clean_root}" fetch origin "${source_ref}"
git -C "${clean_root}" checkout --detach FETCH_HEAD
echo "Testing AtomWorks Studio $(git -C "${clean_root}" rev-parse HEAD) from ${source_repository}"
(
  cd "${clean_root}"
  bash deploy/brev/launchable-setup.sh
  [[ "${preflight_only}" != "1" ]] || exit 0
  ANNOTATION_STUDIO_PORT=18000 bash deploy/brev/start.sh >artifacts/clean-server.log 2>&1 &
  server_pid=$!
  trap 'kill "${server_pid}" >/dev/null 2>&1 || true' EXIT
  ready=false
  for _ in {1..180}; do
    if curl --fail --silent http://127.0.0.1:18000/api/ready >/dev/null; then
      ready=true
      break
    fi
    sleep 2
  done
  if [[ "${ready}" != "true" ]]; then
    cat artifacts/clean-server.log >&2
    echo "Clean Studio service did not become ready on port 18000." >&2
    exit 1
  fi
  ANNOTATION_STUDIO_URL=http://127.0.0.1:18000 bash scripts/accept_brev.sh
)
