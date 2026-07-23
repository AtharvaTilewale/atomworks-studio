#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${studio_dir}/../.." && pwd)"
web_dir="${studio_dir}/web"
dist_index="${web_dir}/dist/index.html"

if ! command -v pnpm >/dev/null 2>&1 && [[ -x "${HOME}/.local/share/pnpm/pnpm" ]]; then
  export PATH="${HOME}/.local/share/pnpm:${PATH}"
fi
if ! command -v uv >/dev/null 2>&1 && [[ -x "${HOME}/.local/bin/uv" ]]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
command -v pnpm >/dev/null 2>&1 || { echo "pnpm is required to build Annotation Studio." >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "uv is required to run Annotation Studio." >&2; exit 1; }

needs_build=false
if [[ ! -f "${dist_index}" ]]; then
  needs_build=true
else
  shopt -s globstar nullglob
  for source in "${web_dir}/src"/**/* "${web_dir}/index.html" "${web_dir}/package.json" "${web_dir}/vite.config.ts"; do
    if [[ -f "${source}" && "${source}" -nt "${dist_index}" ]]; then
      needs_build=true
      break
    fi
  done
fi

if [[ "${needs_build}" == true ]]; then
  command -v node >/dev/null 2>&1 || {
    echo "Node 20.19+ or 22.12+ is required to rebuild Annotation Studio." >&2
    exit 1
  }
  node -e '
    const [major, minor] = process.versions.node.split(".").map(Number);
    process.exit(major > 22 || (major === 22 && minor >= 12) || (major === 20 && minor >= 19) ? 0 : 1);
  ' || {
    echo "Node 20.19+ or 22.12+ is required to rebuild Annotation Studio." >&2
    exit 1
  }
  if ! command -v wasm-pack >/dev/null 2>&1 && [[ -x "${HOME}/.cargo/bin/wasm-pack" ]]; then
    export PATH="${HOME}/.cargo/bin:${PATH}"
  fi
  command -v wasm-pack >/dev/null 2>&1 || {
    echo "wasm-pack is required when Annotation Studio web assets need rebuilding." >&2
    exit 1
  }
  (
    cd "${web_dir}"
    pnpm install --frozen-lockfile
    pnpm build
  )
fi

cd "${repo_dir}"
export PYTHONPATH="${studio_dir}/api${PYTHONPATH:+:${PYTHONPATH}}"
exec uv run --with fastapi --with python-multipart --with pyyaml --with uvicorn \
  uvicorn annotation_studio.main:app \
  --host "${ANNOTATION_STUDIO_HOST:-127.0.0.1}" \
  --port "${ANNOTATION_STUDIO_PORT:-8000}"
