#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${root}/artifacts/tools"
mkdir -p "${artifact_dir}"

bash "${root}/scripts/build_patinae_viewer.sh"
corepack pnpm --dir "${root}/packages/vscode" install --frozen-lockfile
(
  cd "${root}/packages/vscode"
  corepack pnpm package
  cp ./*.vsix "${artifact_dir}/"
)
cargo test --manifest-path "${root}/packages/patinae-plugin/Cargo.toml" --locked
cargo build --manifest-path "${root}/packages/patinae-plugin/Cargo.toml" --release --locked

uv run --project "${root}" --all-extras pytest -q \
  "${root}/packages/pymol-plugin/tests" \
  "${root}/tests/test_pymol_conformance.py" \
  | tee "${artifact_dir}/pymol.log"

python "${root}/scripts/verify_distribution.py"
tar -tf "${root}/packages/web/vendor/"patinae-viewer-*.tgz \
  >"${artifact_dir}/patinae-package.txt"
