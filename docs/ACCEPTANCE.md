# Acceptance

Run `make bootstrap`, `make test`, `make accept-local`, and `make docs` from a
clean checkout. No model runtime, weights, database, or GPU worker is required.

A release must pass:

- Python editing, API, selection, validation, and native PyMOL tests.
- Browser workflows in Patinae and Mol*: annotations, protonation, Undo,
  selection syntax, save/reopen, renderer switching, and embedded views.
- Responsive and accessibility checks, plus the 80 KB gzip browser-shell budget.
- TypeScript, Rust, Python lint, package metadata, and strict documentation builds.
- Installation of built wheels outside the checkout, including bundled annotation
  definitions, browser assets, and WASM.

Check the installed VSIX in VS Code as well as a normal browser. Save confirmations
must work inside its sandbox. Inspect a representative structure in native PyMOL
before release. Automation does not replace that final visual check.

Artifacts are written under `artifacts/`; generated output is not committed.
