# Desktop parity

This matrix records behavioral parity and automated evidence; it is not an
installation guide. Use the [main README](../README.md#four-interfaces-one-document)
to choose an interface, or open the dedicated
[PyMOL](../packages/pymol-plugin/README.md),
[VS Code/Cursor](../packages/vscode/README.md), and
[Patinae](../packages/patinae-plugin/README.md) guides.

The original AtomWorks/RFD4 PyMOL plugins define the compatibility baseline.
Studio's browser and native plugin now share a document and authoring layer.
“Implemented” records a capability, not completion of human or GPU acceptance;
see [the current extraction checks and remaining gates](REFACTOR.md).

| Native action | Studio implementation | Evidence | Status |
|---|---|---|---|
| Load annotated CIF | upload with desktop parse preset, assembly, model, water, and hydrogen options | `tests/test_api.py`, browser smoke | Implemented |
| Combine CIF objects | combine route and file controller | API + browser smoke | Implemented |
| Save / round-trip CIF | validate and serialize one frozen snapshot; Cancel / Save anyway on findings | snapshot API tests + browser unit and smoke tests | Implemented |
| Automatic pre-save validation | upstream fixed-seed pipeline, index checks and AtomWorks missing-atom check; exact report ordering | independent upstream subprocess comparisons | Implemented, with upstream missing-atom limitation below |
| Select atom / residue / chain | shared selection controller and renderer identity map | viewer-contract + dual-renderer smoke | Implemented |
| AtomWorks expression selection | API query evaluator and Patinae command integration | API + browser smoke | Implemented |
| Selection sync / undo | one document selection owner; mutation undo stack | API + PyMOL conformance | Implemented |
| Mask condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Enum condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Group condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Vector / pair condition edit / clear / summary | schema-driven value editor | provider + browser smoke | Implemented |
| CATH text edit / clear / summary | shared vocabulary validation and residue-level assignment | core + native + dual-renderer smoke | Implemented |
| Sequence probability channels | all 28 current-RFD4 channels, coupled normalization and validation | current-format + native conformance | Implemented |
| JSON/YAML ConditionSpec import or source editing | compiler remains in RFD4; load its annotated-CIF output | [Ownership boundary](INPUTS.md) | Not implemented in Studio |
| Charge set and delta | shared `set_formal_charge` primitive | API + PyMOL conformance | Implemented |
| Bond add / remove / inspect | shared `edit_bond`; inspect endpoint | API + PyMOL conformance | Implemented |
| Infer CCD bonds | AtomWorks residue-template inference | API test | Implemented |
| Assign chain | shared `assign_chain_id` primitive | API + PyMOL conformance | Implemented |
| Insert / realize / collapse segment | AtomWorks expandable-segment primitives | API test | Implemented |
| Delete atoms, optional gap close | shared mutation primitive and guarded UI | API + PyMOL conformance | Implemented |
| Compact sequence strip | single-letter tokens, range/toggle multi-select, hide/show | dual-renderer browser smoke | Implemented |
| Interaction analysis | AtomWorks hbond, hydrophobic, pi, salt, halogen, metal analysis | API + browser smoke | Implemented |
| Interaction visualization | renderer overlays with distance-only midpoint labels | viewer-contract + screenshot smoke | Implemented |
| Distance / angle / dihedral | cardinality-checked renderer measurements | unit + dual-renderer smoke | Implemented |
| Distance restraints | AtomWorks condition mutation routes and editor | API + browser smoke | Implemented |
| Internal-coordinate conditioning | mask/group-only op; RFD4 featurization materializes values; web `/internal-coords` route + PyMOL editor | API test + PyMOL conformance | Implemented |
| Saved internal-coordinate target protection | preview target loss, confirm and retry against the same revision; cancellation and undo preserve targets | 17 upstream differential cases, 19 native confirmation and edge cases, API and browser tests | Implemented |
| Submit / poll / cancel design | optional RFD4 process provider with persisted job records | fake-provider + Brev acceptance | Implemented |
| Metrics and progress | requested metrics, ranges, phase, elapsed time, logs | fake-provider + Brev acceptance | Implemented |
| Cycle / align / compare designs | simultaneous input/output objects, colors, toggles, renderer replay | dual-renderer browser smoke | Implemented |
| AF3 reward-guided search | capability-gated oracle controls and daemon provider | Brev acceptance | Implemented |
| VS Code open / configure / renderer | thin URL launcher | VSIX acceptance | Implemented |
| Patinae desktop launch | thin URL launcher | native plugin acceptance | Implemented |
| PWA install shell | shell-only service worker; API excluded from cache | PWA acceptance | Implemented |
| PyMOL panel / wizards | installable Studio package using shared Studio documents and mutations | headless PyMOL acceptance | Implemented |

No renderer silently falls back to another renderer. GPU-only behavior is
capability-gated; lack of a configured provider is not represented as editor
failure.

The scientific-operation comparisons use RFD4 `a12ccf17` and AtomWorks
`38f44845`. They compare actual upstream operations in separate processes,
including exact annotation arrays, error text/order, target loss and no-op
behavior. They do not establish equivalence for every possible input or replace
human interactive acceptance.

At this AtomWorks pin, adding a missing heavy atom can fail because of an
annotation mismatch during concatenation. The upstream panel logs and skips
that check; Studio does the same. A seven-case differential test records both
the skipped check and successful completion behavior. The independent RFD4
model checks still run. This upstream limitation must not be represented as
successful missing-atom validation.
