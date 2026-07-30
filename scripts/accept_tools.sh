#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${root}/artifacts/tools"
mkdir -p "${artifact_dir}"
rfd4_root="${ANNOTATION_STUDIO_RFD4_ROOT:-${RFPROTEINA_ROOT:-${root}/external/RFD4-Proteina-dev}}"
if [[ -d "${rfd4_root}/src/rfproteina" ]]; then
  export PYTHONPATH="${rfd4_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi
if command -v pnpm >/dev/null 2>&1; then
  pnpm_cmd=(pnpm)
else
  pnpm_cmd=(corepack pnpm)
fi

bash "${root}/scripts/build_patinae_viewer.sh"
"${pnpm_cmd[@]}" --dir "${root}/packages/vscode" install --frozen-lockfile
(
  cd "${root}/packages/vscode"
  "${pnpm_cmd[@]}" package
  cp ./*.vsix "${artifact_dir}/"
)
cargo test --manifest-path "${root}/packages/patinae-plugin/Cargo.toml" --locked
cargo build --manifest-path "${root}/packages/patinae-plugin/Cargo.toml" --release --locked

uv run --project "${root}" --all-extras python "${root}/scripts/run_pytest.py" -q \
  "${root}/packages/pymol-plugin/smoke" \
  "${root}/packages/pymol-plugin/tests" \
  "${root}/tests/test_pymol_conformance.py" \
  | tee "${artifact_dir}/pymol.log"

python "${root}/scripts/verify_distribution.py"
tar -tf "${root}/packages/web/vendor/"patinae-viewer-*.tgz \
  >"${artifact_dir}/patinae-package.txt"
