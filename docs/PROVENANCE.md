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
default branches; it is maintained here. Upstream removal is a separate, gated change; see
[the extraction inventory](REFACTOR.md). Re-verify parity when bumping the submodules.

- `packages/editing/atomworks_studio_editing/{structure_mutations,mutations,validation,selection}.py`
  — from atomworks commit `8e4b2c39` (before upstream removal).
- `providers/rfd4/atomworks_studio_rfd4/interactive_validation.py` and the
  model-specific PyMOL widgets originated in RFD4 commit `0e9050ca`. Semantic
  editor metadata belongs to Studio and is derived from the scientific annotation
  contract through a compatibility adapter for both stored-mask and value-derived formats.
- `providers/rfd4/atomworks_studio_rfd4/internal_coordinate.py` adapts Studio
  atom indices to RFD4's shared mask/group authoring function. Centering and
  random rotation live exclusively in RFD4 featurization;
  `tests/test_rfd4_internal_coords.py` guards that boundary.
- `deploy/lib/{inference_runtime.sh,build_latent_dev_docker.sh}` — RFD4 deploy helpers
  vendored so Studio's launchers do not depend on scripts inside the RFD4 checkout.

The base PyMOL plugin (`packages/pymol-plugin`) is published as
`atomworks_studio_pymol`; upstream removal of its former
`atomworks.pymol_plugin` copy remains subject to the acceptance gates above.

Additional Studio-owned components extracted during the unified-editor refactor:

- `packages/pymol-plugin/display.py`: display preparation and style generation from
  AtomWorks `82fd08ae51688f48ff2949ae16ee320d87d434a1`, `ml/utils/visualize.py`.
- `providers/rfd4/atomworks_studio_rfd4/reporting.py`: summaries and index-group
  synchronization from RFD4 `26094e08e6f979ee6da53d51bc34357ffc062025`,
  `conditions_reporting.py`. Scientific pre-save checks now call the canonical
  upstream reporting functions in a private worker; their local copies were removed.
- CATH input vocabulary validation follows the current RFD4 editor's C.A.T.x
  convention. The vocabulary data and condition definition remain upstream.
- `providers/rfd4/atomworks_studio_rfd4/sequence_probability.py`: input
  canonicalization and validation extracted from RFD4
  `a12ccf17f746590b05ebe032ce744af2c7caa485`,
  `datapipes/pipeline.py` and `transforms/invariants.py`. Scientific condition
  definitions and token vocabulary remain imported from upstream.
- Current-format coordinate authoring follows that same RFD4 revision's
  `transforms/utils/conditioning_utils.py`; group validation and preservation of
  saved targets remain upstream scientific responsibilities.
- The native `group_editor.py` was refreshed against `a12ccf17` for reserved
  group IDs, stale selection handling and per-atom defaults on removal/deletion.
  Confirmation and undo use Studio's shared document transaction.
- The shared missing-heavy-atom save check follows AtomWorks `38f44845`'s
  `pymol_plugin/panel.py`, including its configuration and log-and-skip behavior
  when preparation fails. Independent tests compare the actual upstream panel.
- `packages/editing/atomworks_studio_editing/query.py` adapts query evaluation
  from that revision's `utils/mask_spec.py`. Studio derives selection fields on
  temporary copies so queries do not persist stale annotations in a document.

ConditionSpec, contig parsing, and their compiler tests remain owned by RFD4.
Studio does not vendor those modules. Coordinate-group separation used by
interactive document combination lives in `internal_coordinate.py`; it preserves
saved targets while giving independent inputs disjoint group IDs.
