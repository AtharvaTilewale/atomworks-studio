#!/usr/bin/env bash
set -euo pipefail

: "${DATA_PATH:?Set DATA_PATH to persistent RFD4/AF3 assets}"
: "${ANNOTATION_STUDIO_RFD4_ROOT:?Set ANNOTATION_STUDIO_RFD4_ROOT to a clean or pinned RFD4 checkout}"

clean_root="${ATOMWORKS_STUDIO_CLEAN_ROOT:-${HOME}/workspace/atomworks-studio-clean-acceptance}"
case "${clean_root}" in
  *atomworks-studio-clean-acceptance) ;;
  *) echo "Refusing unsafe clean root: ${clean_root}" >&2; exit 2 ;;
esac

rm -rf "${clean_root}"
git clone https://github.com/baker-laboratory/atomworks-studio.git "${clean_root}"
(
  cd "${clean_root}"
  bash deploy/brev/launchable-setup.sh
  ANNOTATION_STUDIO_PORT=18000 bash deploy/brev/start.sh >artifacts/clean-server.log 2>&1 &
  server_pid=$!
  trap 'kill "${server_pid}" >/dev/null 2>&1 || true' EXIT
  for _ in {1..180}; do
    if curl --fail --silent http://127.0.0.1:18000/api/ready >/dev/null; then
      break
    fi
    sleep 2
  done
  ANNOTATION_STUDIO_URL=http://127.0.0.1:18000 bash scripts/accept_brev.sh
)
