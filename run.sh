#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
web_dir="${studio_dir}/packages/web"
api_dir="${studio_dir}/packages/api"
dist_index="${web_dir}/dist/index.html"

if ! command -v pnpm >/dev/null 2>&1; then
  for pnpm_dir in "${HOME}/.local/bin" "${HOME}/.local/share/pnpm"; do
    if [[ -x "${pnpm_dir}/pnpm" ]]; then
      export PATH="${pnpm_dir}:${PATH}"
      break
    fi
  done
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
  for source in "${web_dir}/src"/**/* "${web_dir}/public"/**/* \
    "${web_dir}/index.html" "${web_dir}/package.json" "${web_dir}/vite.config.ts"; do
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
  bash "${studio_dir}/scripts/build_patinae_viewer.sh"
  (
    cd "${studio_dir}"
    pnpm install --frozen-lockfile
    pnpm --dir "${web_dir}" build
  )
fi

if [[ ! -f "${studio_dir}/docs/_build/html/index.html" ]]; then
  make -C "${studio_dir}" docs
fi

cd "${studio_dir}"
export PYTHONPATH="${api_dir}:${studio_dir}/providers/rfd4${PYTHONPATH:+:${PYTHONPATH}}"
exec uv run --project "${studio_dir}" \
  uvicorn annotation_studio.main:app \
  --host "${ANNOTATION_STUDIO_HOST:-127.0.0.1}" \
  --port "${ANNOTATION_STUDIO_PORT:-8000}"
