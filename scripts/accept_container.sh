#!/usr/bin/env bash
set -euo pipefail

image="${1:-atomworks-studio:accept}"
port="${ANNOTATION_STUDIO_CONTAINER_PORT:-18001}"
container_id="$(
  docker run --detach --read-only --tmpfs /tmp --tmpfs /app/state \
    --publish "${port}:8000" "${image}"
)"
cleanup() {
  docker rm --force "${container_id}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in {1..60}; do
  if curl --fail --silent "http://127.0.0.1:${port}/api/ready"; then
    break
  fi
  sleep 1
done

container_user="$(docker inspect --format '{{.Config.User}}' "${container_id}")"
[[ -n "${container_user}" && "${container_user}" != "0" ]]
curl --fail --silent "http://127.0.0.1:${port}/api/ready" >/dev/null
