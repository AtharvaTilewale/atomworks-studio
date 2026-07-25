# Product style

- The AtomWorks mark and “Annotation Studio” identify the product. RFD4 is an
  optional workflow, not a competing brand.
- Dark neutral workbench surfaces carry the website. Restrained IPD purple is
  the interaction accent and institutional gold is reserved for identity and
  supporting emphasis.
- NVIDIA green communicates Brev/GPU readiness only. It is not used for
  navigation, selection, or general actions.
- The VS Code surface maps the active host theme kind to Studio semantic tokens;
  its containing webview uses native `--vscode-*` colors and typography.
- Molecular element and chain colors are renderer-owned. Condition and metric
  colors use a separate color-blind-safe data palette and never inherit product
  branding colors.
- The app is one `100dvh` plane: header, toolbar, sequence, and molecular
  canvas. Page-level scrolling is forbidden.
- AtomWorks Studio and RFD4-Proteina use separate toolbar tabs. Panels are
  full-width, vertically stacked, resizable, and never clipped by the canvas.
- Sequence and metric data use the product mono font. Residues are compact
  single-letter tokens with keyboard-visible focus, hover, and selection.
- Measurement labels contain only their value (for example `3.44 Å`) and sit
  immediately above the projected geometry midpoint.
- Motion is limited to panel opening, sequence visibility, and comparison
  loading, and is disabled under `prefers-reduced-motion`.
- Required responsive baselines are 760 px width, 125% browser zoom, and DPR 2
  for both renderers.
- Automated accessibility acceptance permits no serious or critical
  violations; all primary workflows must be keyboard operable.
