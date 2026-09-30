# Unified editor extraction

Studio owns editing, display policy, selection handling, summaries, and interactive
validation. AtomWorks owns structural data and serialization. RFD4 owns model
conditions, featurization, inference, and scientific invariants. Neither upstream
checkout is changed by this work.

## Shared document

`atomworks_studio_editing.document.StudioDocument` is independent of the service,
renderers, and RFD4. It supplies editing operations and bounded, transactional undo.
Nested edits form one undo step; failed edits restore the array without consuming
history or advancing the document revision. The API re-exports the existing public
names for compatibility.

PyMOL keeps its renderer identity maps and coordinate synchronization. Its arrays
and undo history live in the same document class as browser sessions. The existing
`STATE.arrays` mapping exposes those arrays to extensions, including Quantum; it
is not a second copy of the structures. Charge, chain, segment, deletion, and
residue-renumbering actions use shared document operations.
RFD4 mask, enum, group, internal-coordinate, CATH, probability and centroid
controls also use document operations, including shared validation and undo.

## RFD4 boundary

The editor loads upstream scientific condition and annotation definitions from the
configured/installed checkout without substituting a fake `rfproteina` package or
changing environment variables. `model.py` is the sole source-loading adapter.
Definitions are registered in AtomWorks under a Studio module namespace; an already
imported model's definitions are reused. An editor that loads the definitions first
must run full model code in a separate process, as the annotation registry rejects
duplicate class registrations.

Full validation runs in a separate process with the actual RFD4 initializer. Set
`ANNOTATION_STUDIO_RFD4_VALIDATION_PYTHON` to its Python command when that runtime
lives in a different environment. Missing runtime dependencies are reported as
validation errors, not successful validation. Rendering, editing, and generic
structural validation do not require that runtime.

The worker receives an exact AtomArrayPlus through a private process pipe and
calls RFD4's canonical `conditions_reporting` functions. It never reparses a CIF
before validation. This preserves coordinate precision, bonds, custom annotations
and invalid values that parsing could repair. The upstream pipeline supplies its
fixed expandable-segment RNG seed. Each pre-save hook receives a separate copy;
hook failures do not prevent subsequent checks. This pipe is not an upload API.
Browser save validates and serializes one frozen document snapshot, then offers
Cancel / Save anyway for findings, matching the native panel's decision.

RFD4's package depends on the editing package. Install its `service` extra to use
its process-job factory without an existing Studio API installation; install its
`pymol` extra for native editors. Model definitions and the CATH vocabulary remain
upstream scientific assets. CATH's widget, input normalization, and reporting are
Studio responsibilities.

## Extraction inventory

| Area | Studio owner | Upstream dependency retained |
| --- | --- | --- |
| Documents, edits, undo, inspection | `packages/editing` | AtomWorks arrays, IO, structural/scientific operations |
| PyMOL bridge and dialogs | `packages/pymol-plugin` | PyMOL, Qt |
| Display copies, styles, named selections | `packages/pymol-plugin/display.py` | AtomWorks annotation definitions |
| RFD4 widget schema and native editors | `providers/rfd4` | Scientific condition definitions |
| RFD4 summaries and index-group synchronization | `providers/rfd4/reporting.py` | Scientific defaults and annotation operations |
| JSON/YAML ConditionSpec and contig compilation | Remains in RFD4 | Canonical upstream compiler; no Studio copy |
| Queries and document combination | `packages/editing` | AtomWorks query engine and annotation-preserving concatenation |
| Full model validation and scientific pre-save reports | isolated `_validate.py` worker | RFD4 `conditions_reporting` functions and validation pipeline |
| Browser rendering | existing Molstar/Patinae adapters | Existing renderer packages |
| Quantum jobs, results, trajectories, electronic-state summaries | existing Quantum provider and UI | Existing Quantum process interface |

## Acceptance baselines

The original Studio pins are AtomWorks `82fd08ae51688f48ff2949ae16ee320d87d434a1`
and RFD4 `26094e08e6f979ee6da53d51bc34357ffc062025`. The initial test baseline was
193 passed and 13 skipped. Skips cover optional database/runtime integrations and
real Quantum execution; they do not constitute acceptance of those paths.

The dependency pins are now AtomWorks dev
`38f448459a5834afa6caef2f14acd4e7084fcd61` (verified as the latest remote dev tip on
2026-09-29) and RFD4 `a12ccf17f746590b05ebe032ce744af2c7caa485`, its matching
annotation-refactor commit. RFD4 pins that exact AtomWorks revision internally.
The older RFD4 pin is incompatible: its parse presets omit the required
`return_atom_array_plus=True`, and its conditions lack value-derived defaults.
Only Studio's dependency references changed; neither upstream working checkout
was edited.

Studio derives widget metadata from the scientific annotation contract and keeps
authoring policy locally. Its annotated-CIF parsing requests AtomArrayPlus;
combination uses AtomWorks' standard-field filling and rejects unknown field
mismatches; pair analysis uses the current `n_body=2` accessor.

Alias normalization, coupled coordinate/group edits, all 28 probability channels,
explicit-zero probability round trips, whole-residue authoring, and saved-target
preservation have regression coverage. Edits that invalidate saved internal
targets require inline confirmation in both native and browser editors. The
preview rolls back completely; confirmation is bound to the document revision,
and undo restores all affected group members. Group edits preserve absolute
coordinate constraints; unchanged coordinate edits do not consume undo history.
An explicit clear-targets operation also preserves membership and supports undo.
Combining structures preserves independent coordinate groups and saved targets.

## Verification (2026-09-29)

| Check | Result |
| --- | --- |
| Full Python suite, new pinned submodule checkouts | 287 passed, 38 skipped; 26 runtime-gated differential cases also run separately below |
| Independent upstream coordinate comparisons | 17 passed: exact annotations, errors, target loss, no-op history, CIF round trips and undo |
| Independent upstream model pre-save comparisons | 9 passed: valid/authored inputs, expandable segments, bond geometry, hydrogen bonds, probabilities, index gaps, molecule groups and partial targets |
| Upstream generic missing-atom comparison | 7 passed, including actual preparation failures and a controlled successful completion |
| Molstar and Patinae browser interaction suites, current tips | Passed, including save Cancel / Save anyway, exact downloaded bytes, inline target-loss confirmation, combining inputs and CATH |
| Native PyMOL/Qt controls | 19 confirmation, stale-group, orphan-target and no-op cases; existing selection, CIF round trips, CATH, coordinates, enums, groups, centroids, probability and undo coverage |
| Frontend unit tests, TypeScript, production build | Passed |
| Python lint/format and dependency lock validation | Passed |
| Actual current RFD4 CPU validation pipeline | Accepted the PPI fixture and a Studio-authored CIF; real validated-export API and reload retained condition arrays and passed model checks |

Current AtomWorks `io/utils/protonation.py:755` indexes
`_resolve_standard_annotation(annot)[0]`, but that lookup now returns an
annotation class. Studio interaction analysis now passes a geometry-only copy to
that scientific operation, preserving live coordinates, bonds and charge while
excluding authoring fields. The regression passes on the new pins and verifies
that stored targets neither affect the analysis nor change in the document.

The pinned AtomWorks missing-heavy-atom check can fail template concatenation
with an annotation mismatch, including for an alanine missing CB. The upstream
panel logs and skips that check; Studio matches it. The real export/reload check
also encountered this generic-check limitation while its RFD4 model checks
passed. This is documented upstream behavior, not successful missing-atom
validation; see [the parity matrix](PARITY.md).

Tests ran in isolated environments and source snapshots under the user's scratch
directory; neither upstream working tree was modified. Optional database/runtime
tests and real Quantum execution were skipped. A missing inference runtime is
reported as a validation error by the worker; it is not successful scientific
validation. Headless browser and Qt tests are not human interactive acceptance.

## Remaining extraction and acceptance

1. Run real configured RFD4 GPU design and Quantum execution, then check
   representative user structures interactively in the native plugin and both
   browser backends: selections/scopes, conditions, bonds/charges, chains,
   segments, combine/align, undo, and save/reload.
2. After acceptance, update upstream consumers and remove their old visualization
   and interactive editor modules in separate changes. ConditionSpec and contig
   parsing remain in RFD4; they are excluded from this extraction. Keep the
   canonical scientific functions in `conditions_reporting` (or relocate them
   within RFD4 with a compatibility import) when removing its editor summaries.

Removal of upstream plugin modules requires condition and saved-target round trips,
native editor and both browser-renderer acceptance, real configured model/Quantum
validation, and a human interactive check. No incompatibility with Quantum's
ownership was identified: its job, result and trajectory code is preserved, with
only a parser option adjusted to retain annotated arrays.
