# Desktop parity

This matrix records behavioral parity and automated evidence; it is not an
installation guide. Use the [main README](../README.md#three-interfaces-one-document)
to choose an interface, or open the dedicated
[PyMOL](../packages/pymol-plugin/README.md),
[VS Code/Cursor](../packages/vscode/README.md), and
[Patinae](../packages/patinae-plugin/README.md) guides.

The AtomWorks PyMOL plugin is the behavioral specification. “Implemented”
means the web/API path uses the same AtomWorks semantics; release acceptance
still requires the cited automated check to pass.

| Native action | Studio implementation | Evidence | Status |
|---|---|---|---|
| Load annotated CIF | upload with desktop parse preset, assembly, model, water, and hydrogen options | `tests/test_api.py`, browser smoke | Implemented |
| Combine CIF objects | combine route and file controller | API + browser smoke | Implemented |
| Save / round-trip CIF | lossless export and re-import, including unknown categories | core/API conformance | Implemented |
| Select atom / residue / chain | shared selection controller and renderer identity map | viewer-contract + dual-renderer smoke | Implemented |
| AtomWorks expression selection | API query evaluator and Patinae command integration | API + browser smoke | Implemented |
| Selection sync / undo | one document selection owner; mutation undo stack | API + PyMOL conformance | Implemented |
| Mask condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Enum condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Group condition edit / clear / summary | provider condition schema drives editor | provider + browser smoke | Implemented |
| Vector / pair condition edit / clear / summary | schema-driven value editor | provider + browser smoke | Implemented |
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
| Internal-coordinate conditioning | shared torch-light `convert_to_internal_coordinates` op; web `/internal-coords` route + PyMOL editor | API test + PyMOL conformance | Implemented |
| Submit / poll / cancel design | optional RFD4 process provider with persisted job records | fake-provider + Brev acceptance | Implemented |
| Metrics and progress | requested metrics, ranges, phase, elapsed time, logs | fake-provider + Brev acceptance | Implemented |
| Cycle / align / compare designs | simultaneous input/output objects, colors, toggles, renderer replay | dual-renderer browser smoke | Implemented |
| AF3 reward-guided search | capability-gated oracle controls and daemon provider | Brev acceptance | Implemented |
| VS Code open / configure / renderer | thin URL launcher | VSIX acceptance | Implemented |
| Patinae desktop launch | thin URL launcher | native plugin acceptance | Implemented |
| PWA install shell | shell-only service worker; API excluded from cache | PWA acceptance | Implemented |
| PyMOL panel / wizards | installable Studio package using upstream AtomWorks mutations | headless PyMOL acceptance | Implemented |

No renderer silently falls back to another renderer. GPU-only behavior is
capability-gated; lack of a configured provider is not represented as editor
failure.
