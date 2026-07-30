# Source provenance

AtomWorks Studio was extracted with commit history from
`baker-laboratory/RFD4-Proteina-dev` at
`db94ca937fba69c390731f7688b3a4bf0c0a6495`. The extraction used
`git subtree split --prefix=apps/annotation-studio`; its imported tip is
`6a33a68e29b11f4ac9d3fc1bbb6bf1208171ad3a`.

The patched Patinae viewer is rebuilt from
`https://github.com/zmactep/patinae.git` at
`5e40f6d1654b2d68cacfc2923e2db1a52c875058`. The reviewed patch series in
`vendor/patinae/patches` reproduces Studio branch
`2bf8d2cc6dde586e80a728f0d8718ba0bbb584f0`.

## Vendored dependencies (git submodules)

AtomWorks and RFD4-Proteina remain upstream dependencies, vendored as git
submodules under `external/` and pinned by commit (see `dependencies.toml` and
`.gitmodules`):

- `external/atomworks-dev` — atomworks, branch `dev`.
- `external/RFD4-Proteina-dev` — RFD4-Proteina, branch `main`.

Their complete source trees, checkpoints, and model weights are intentionally not
copied here.

## Migrated editor code

Some editor code was copied out of the dependencies so Studio can track their
default branches; it is maintained here and removed upstream by the coordinated
PRs noted below. Re-verify parity when bumping the submodules.

- `packages/editing/atomworks_studio_editing/{structure_mutations,mutations,validation,selection}.py`
  — from atomworks commit `8e4b2c39` (before upstream removal).
- `providers/rfd4/atomworks_studio_rfd4/{condition_specs,interactive_validation}.py`
  and `providers/rfd4/atomworks_studio_rfd4/pymol/` — from RFD4 commit `0e9050ca`
  (`rfproteina.condition_specs`, `rfproteina.interactive_validation`, `rfproteina.pymol`);
  the RFD4 PyMOL editors align with RFD4-Proteina-dev PR #107.
- `providers/rfd4/atomworks_studio_rfd4/internal_coords.py` — lightweight
  Studio-operation adapter for
  `rfproteina.transforms.utils.conditioning_utils.convert_to_internal_coords`.
  The adapter is retained because importing that upstream module pulls the
  RFD4 training stack (`lightning_fabric`) into the CPU editor. Its behavioral
  invariants are checked by `tests/test_rfd4_internal_coords.py`.
- `deploy/lib/{inference_runtime.sh,build_latent_dev_docker.sh}` — RFD4 deploy helpers
  vendored so Studio's launchers do not depend on scripts inside the RFD4 checkout.

The base PyMOL plugin (`packages/pymol-plugin`) is published as
`atomworks_studio_pymol`; atomworks-dev PR #297 removes its former
`atomworks.pymol_plugin` copy.
