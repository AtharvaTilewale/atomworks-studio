#!/usr/bin/env bash
# Install the local RFD4 editor into a dedicated Conda environment.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

conda_tool="${CONDA_EXE:-}"
if [[ ! -x "$conda_tool" ]]; then
    conda_tool="$(command -v conda || true)"
fi
if [[ -z "$conda_tool" ]]; then
    for candidate in "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" /opt/miniconda3/bin/conda; do
        if [[ -x "$candidate" ]]; then conda_tool="$candidate"; break; fi
    done
fi
if [[ -z "$conda_tool" ]]; then
    echo "Install Miniforge (https://github.com/conda-forge/miniforge), then run make rfd4 again." >&2
    exit 1
fi

# Update only missing checkouts; never overwrite a developer's initialized submodule.
for dependency in external/atomworks-dev external/RFD4-Proteina-dev; do
    if [[ ! -f "$dependency/pyproject.toml" ]]; then
        # Retry failed HTTPS clones using the transport of the Studio remote.
        git submodule sync -- "$dependency"
        git submodule update --init -- "$dependency"
    fi
    expected="$(git rev-parse ":$dependency")"
    actual="$(git -C "$dependency" rev-parse HEAD)"
    if [[ "$actual" != "$expected" || -n "$(git -C "$dependency" status --porcelain --untracked-files=no)" ]]; then
        echo "$dependency differs from the Studio pin. Use a clean Studio checkout for installation." >&2
        exit 1
    fi
done

environment="${1:-atomworks-studio-rfd4}"
environment_args=(-n "$environment")
if [[ "$environment" == */* ]]; then environment_args=(-p "$environment"); fi
if ! "$conda_tool" run "${environment_args[@]}" python -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
    "$conda_tool" env create "${environment_args[@]}" -f packages/pymol-plugin/rfd4_env.yaml --yes
else
    # Apply native dependency constraints to existing environments before pip installation.
    "$conda_tool" env update "${environment_args[@]}" -f packages/pymol-plugin/rfd4_env.yaml
fi
"$conda_tool" run --no-capture-output "${environment_args[@]}" python scripts/install_rfd4_pymol.py "${2:-}"
echo
echo "Ready. Start PyMOL with:"
echo "  conda activate $environment"
echo "  pymol"
echo "Then choose Plugin > AtomWorks Studio."
