import initCifCore, { inspect_cif } from "./wasm/annotation_studio_core.js";
import { api, postJson } from "./api";
import { AnnotationOverlayState } from "./annotation-overlay";
import { initializeDesignController } from "./design";
import { applyCapabilities, closeToolbarMenus, element, escapeHtml } from "./dom";
import { STUDIO_EXAMPLES, type StudioExample } from "./examples";
import { SelectionController } from "./selection-controller";
import type {
  AnnotationSummary,
  AnnotationValue,
  InteractionAnalysisResponse,
  SelectionScope,
  SelectionSyntax,
  SequenceChain,
  SessionResponse,
  StructureSummary,
  StudioSchema,
} from "./types";
import {
  MolecularViewer,
  type ViewerColor,
  type ViewerInteraction,
  type ViewerMode,
  type ViewerRepresentation,
  type ViewerTarget,
} from "./viewers";
import "./style.css";

const fileInput = element<HTMLInputElement>("file-input");
const combineInput = element<HTMLInputElement>("combine-input");
const downloadButton = element<HTMLButtonElement>("download");
const conditionSelect = element<HTMLSelectElement>("condition");
const scopeSelect = element<HTMLSelectElement>("scope");
const enabledInput = element<HTMLInputElement>("enabled");
const conditionValue = element<HTMLDivElement>("condition-value");
const applyCondition = element<HTMLButtonElement>("apply-condition");
const selectionCount = element<HTMLSpanElement>("selection-count");
const viewerBackend = element<HTMLSpanElement>("viewer-backend");
const viewerModeSelect = element<HTMLSelectElement>("viewer-mode");
const pickScope = element<HTMLSelectElement>("pick-scope");
const viewerCommandForm = element<HTMLFormElement>("viewer-command-form");
const viewerCommand = element<HTMLInputElement>("viewer-command");
const runViewerCommand = element<HTMLButtonElement>("run-viewer-command");
const selectionSyntax = element<HTMLSelectElement>("selection-syntax");
const viewerActionTarget = element<HTMLSelectElement>("viewer-action-target");
const representationExclusive = element<HTMLInputElement>("representation-exclusive");
const studioMenu = element<HTMLDetailsElement>("atomworks-studio-menu");
const studioPopover = element<HTMLDivElement>("atomworks-studio-popover");
const studioPanel = document.querySelector<HTMLElement>(".workspace > aside");
if (!studioPanel) throw new Error("AtomWorks Studio panel is missing");
studioPopover.append(studioPanel);
const summaryElement = element<HTMLDivElement>("structure-summary");
const sequenceView = element<HTMLDivElement>("sequence-view");
const annotationList = element<HTMLDivElement>("annotation-list");
const annotationDetail = element<HTMLDivElement>("annotation-detail");
const statusElement = element<HTMLPreElement>("status");
const query = new URLSearchParams(window.location.search);
const VIEWER_STORAGE_KEY = "annotation-studio-viewer";
const PENDING_EXAMPLE_KEY = "annotation-studio-pending-example";

function viewerMode(value: string | null): ViewerMode | null {
  return value === "patinae" || value === "molstar" ? value : null;
}

function requestedViewerMode(): ViewerMode | null {
  return viewerMode(query.get("viewer")) ?? viewerMode(localStorage.getItem(VIEWER_STORAGE_KEY));
}

function notifyHost(type: "ready" | "changed"): void {
  const parentOrigin = query.get("parent_origin");
  if (!parentOrigin || window.parent === window || !sessionId) return;
  try {
    const targetOrigin = new URL(parentOrigin).origin;
    window.parent.postMessage(
      { source: "atomworks-annotation-studio", type, session_id: sessionId },
      targetOrigin,
    );
  } catch {
    log(`Ignored invalid parent_origin: ${parentOrigin}`);
  }
}

let molecularViewer: MolecularViewer;
let schema: StudioSchema;
let sessionId: string | null = null;
let currentName = "structure";
let lastSummary: StructureSummary | null = null;
let sequenceAnchor: number[] | null = null;
let sequenceAnchorOrder: number | null = null;
let sequenceDragging = false;
type MeasurementKind = "distance" | "angle" | "dihedral";
let measurementDraft: { kind: MeasurementKind; atomIndices: number[] } | null = null;
let designController: {
  updateSession: () => void;
  applyPreset: (metricsTags: string[], search?: StudioExample["search"]) => string[];
} | null = null;
const annotationOverlay = new AnnotationOverlayState();
const selectionController = new SelectionController(
  () => molecularViewer,
  () => updateSelectionUi(),
);

function persistSession(response: SessionResponse, message?: string): void {
  sessionStorage.setItem("annotation-studio-session", response.session_id);
  sessionStorage.setItem("annotation-studio-name", response.structure.name);
  if (message) sessionStorage.setItem("annotation-studio-message", message);
  designController?.updateSession();
}

function log(message: string): void {
  const stamp = new Date().toLocaleTimeString();
  statusElement.textContent = `[${stamp}] ${message}\n${statusElement.textContent ?? ""}`.slice(0, 5000);
}

function updateSelectionUi(): void {
  const count = selectionController.size;
  const scope = pickScope.value as SelectionScope;
  selectionCount.textContent =
    `${count} atom${count === 1 ? "" : "s"} selected · ${scope} picking`;
  const hasSession = sessionId !== null;
  applyCondition.disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("apply-charge").disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("delete-atoms").disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("insert-segment").disabled = !hasSession || count !== 1;
  element<HTMLButtonElement>("add-bond").disabled = !hasSession || count !== 2;
  element<HTMLButtonElement>("remove-bond").disabled = !hasSession || count !== 2;
  element<HTMLButtonElement>("run-analysis").disabled = !hasSession;
  const measurementKind = element<HTMLSelectElement>("measurement-kind").value;
  const measurementAtoms = measurementKind === "distance" ? 2 : measurementKind === "angle" ? 3 : 4;
  const measurementButton = element<HTMLButtonElement>("add-measurement");
  measurementButton.disabled = !hasSession || !molecularViewer?.capabilities.measurements;
  measurementButton.textContent = count === measurementAtoms
    ? "Add measurement"
    : `Pick ${measurementAtoms} atoms`;
  element<HTMLButtonElement>("inspect-bonds").disabled = !hasSession;
  element<HTMLButtonElement>("infer-bonds").disabled = !hasSession;
  element<HTMLButtonElement>("add-restraint").disabled = !hasSession || count !== 2;
  element<HTMLButtonElement>("remove-restraint").disabled = !hasSession || count !== 2;
  syncSequenceSelection();
}

function syncSequenceSelection(scroll = false): void {
  let firstActive: HTMLElement | null = null;
  for (const token of sequenceView.querySelectorAll<HTMLButtonElement>(".sequence-token[data-atoms]")) {
    const atoms = (token.dataset.atoms ?? "").split(",").filter(Boolean).map(Number);
    const active = atoms.length > 0 && atoms.every((index) => selectionController.values.has(index));
    token.classList.toggle("selected", active);
    if (active && firstActive === null) firstActive = token;
  }
  if (scroll) firstActive?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function clearSelection(): void {
  selectionController.clear();
}

function formatAnnotationValue(value: AnnotationValue): string {
  const suffix = Array.isArray(value.value)
    ? ` = (${value.value.map((item) => item.toFixed(3)).join(", ")})`
    : value.value === undefined ? "" : ` = ${value.value}`;
  return `${value.label}${suffix}: ${value.count.toLocaleString()} atom${value.count === 1 ? "" : "s"}`;
}

async function displayAnnotation(annotation: AnnotationSummary): Promise<void> {
  const groups = annotation.values
    .filter((value) => value.atom_indices?.length)
    .map((value) => ({ label: value.label, atomIndices: value.atom_indices!, color: value.color }));
  await annotationOverlay.show(molecularViewer, groups, selectionController.values);
  element<HTMLButtonElement>("clear-annotation-view").disabled = groups.length === 0;
}

async function inspectAnnotation(name: string, row: HTMLButtonElement): Promise<void> {
  if (!sessionId) return;
  try {
    const result = await api<{ annotation: AnnotationSummary | null }>(
      `/api/sessions/${sessionId}/annotations/${encodeURIComponent(name)}`,
    );
    if (!result.annotation) {
      annotationDetail.textContent = "This annotation is no longer active.";
      return;
    }
    for (const other of annotationList.querySelectorAll(".annotation-row")) {
      other.classList.toggle("active", other === row);
    }
    annotationDetail.textContent = [
      `${result.annotation.label}: ${result.annotation.count} ${result.annotation.unit}`,
      ...result.annotation.values.map(formatAnnotationValue),
    ].join("\n");
    await displayAnnotation(result.annotation);
  } catch (error) {
    annotationDetail.textContent = `Could not inspect annotation: ${String(error)}`;
  }
}

async function inspectStoredField(
  name: string,
  row: HTMLButtonElement,
  endpoint = "annotation-fields",
): Promise<void> {
  if (!sessionId) return;
  try {
    const result = await api<{ annotation: AnnotationSummary }>(
      `/api/sessions/${sessionId}/${endpoint}/${encodeURIComponent(name)}`,
    );
    for (const other of annotationList.querySelectorAll(".annotation-row")) {
      other.classList.toggle("active", other === row);
    }
    annotationDetail.textContent = [
      `${result.annotation.label}: ${result.annotation.count} ${result.annotation.unit}`,
      ...result.annotation.values.map(formatAnnotationValue),
      result.annotation.truncated ? "Additional distinct values omitted from this preview." : "",
    ].filter(Boolean).join("\n");
    await displayAnnotation(result.annotation);
  } catch (error) {
    annotationDetail.textContent = `Could not inspect stored field: ${String(error)}`;
  }
}

function renderAnnotationSummary(
  annotations: AnnotationSummary[],
  storedFields: AnnotationSummary[],
  scalarFields: AnnotationSummary[],
): void {
  annotationList.replaceChildren();
  if (!annotations.length && !storedFields.length && !scalarFields.length) {
    annotationList.className = "annotation-list muted";
    annotationList.textContent = "No explicitly stored, non-default annotations.";
    annotationDetail.textContent = "Pick an atom in the molecular viewer to inspect it.";
    return;
  }
  annotationList.className = "annotation-list";
  for (const annotation of annotations) {
    const row = document.createElement("button");
    row.className = "annotation-row";
    const label = document.createElement("span");
    label.textContent = annotation.label;
    const count = document.createElement("span");
    count.textContent = `${annotation.count} ${annotation.unit}`;
    row.append(label, count);
    row.addEventListener("click", () => void inspectAnnotation(annotation.name, row));
    annotationList.appendChild(row);
  }
  if (storedFields.length) {
    const heading = document.createElement("div");
    heading.className = "annotation-fields-heading";
    heading.textContent = `All serialized fields (${storedFields.length})`;
    annotationList.appendChild(heading);
    for (const field of storedFields) {
      const row = document.createElement("button");
      row.className = "annotation-row annotation-field-row";
      const label = document.createElement("span");
      label.textContent = field.label;
      const count = document.createElement("span");
      count.textContent = `${field.values.length} value${field.values.length === 1 ? "" : "s"}`;
      row.append(label, count);
      row.addEventListener("click", () => void inspectStoredField(field.name, row));
      annotationList.appendChild(row);
    }
  }
  if (scalarFields.length) {
    const heading = document.createElement("div");
    heading.className = "annotation-fields-heading";
    heading.textContent = "Output confidence";
    annotationList.appendChild(heading);
    for (const field of scalarFields) {
      const row = document.createElement("button");
      row.className = "annotation-row";
      const label = document.createElement("span");
      label.textContent = field.label;
      const count = document.createElement("span");
      count.textContent = `${field.count} ${field.unit}`;
      row.append(label, count);
      row.addEventListener("click", () => void inspectStoredField(field.name, row, "scalar-fields"));
      annotationList.appendChild(row);
    }
  }
  annotationDetail.textContent = "Click an annotation to color it, or pick an atom for per-atom details.";
}

function renderSequenceView(chains: SequenceChain[]): void {
  sequenceView.replaceChildren();
  let hasCollapsed = false;
  let hasRealized = false;
  let sequenceOrder = 0;
  const selectResidue = (token: HTMLButtonElement, atoms: number[], additive: boolean): void => {
    if (additive) selectionController.add(atoms);
    else selectionController.replace(atoms);
    sequenceAnchor = atoms;
    sequenceAnchorOrder = Number(token.dataset.order);
    void inspectAtom(atoms[0]);
  };
  for (const chain of chains) {
    const row = document.createElement("div");
    row.className = "sequence-chain";
    const label = document.createElement("span");
    label.className = "sequence-chain-label";
    label.textContent = chain.chain_id;
    const track = document.createElement("div");
    track.className = "sequence-track";
    for (const item of chain.items) {
      const token = document.createElement("button");
      token.className = "sequence-token";
      if (item.kind === "segment") {
        hasCollapsed ||= !item.realized;
        hasRealized ||= item.realized;
        token.classList.add("sequence-segment");
        token.classList.toggle("realized", item.realized);
        token.textContent = item.realized
          ? `${item.realized_length ?? 0}×X`
          : `[${item.minimum}–${item.maximum}]`;
        token.title = item.realized
          ? `Realized expandable segment (${item.realized_length} residues); click to collapse all segments`
          : `Expandable segment (${item.minimum}-${item.maximum} residues); click to realize all segments`;
        token.addEventListener("click", () => {
          void mutate(item.realized ? "/segments/collapse" : "/segments/realize", item.realized ? undefined : {});
        });
      } else {
        token.textContent = item.code;
        token.dataset.atoms = item.atom_indices.join(",");
        token.dataset.order = String(sequenceOrder++);
        token.title = `${chain.chain_id}/${item.residue_name} ${item.residue_id}`
          + (item.conditions.length ? ` · ${item.conditions.join(", ")}` : "");
        for (const condition of item.conditions) token.classList.add(`condition-${condition}`);
        token.addEventListener("pointerdown", (event) => {
          if (event.shiftKey) return;
          sequenceDragging = true;
          token.setPointerCapture(event.pointerId);
          selectResidue(token, item.atom_indices, event.ctrlKey || event.metaKey);
        });
        token.addEventListener("pointerenter", (event) => {
          if (!sequenceDragging || event.buttons !== 1) return;
          selectResidue(token, item.atom_indices, true);
        });
        token.addEventListener("click", (event) => {
          if (event.shiftKey && sequenceAnchorOrder !== null) {
            const current = Number(token.dataset.order);
            const low = Math.min(sequenceAnchorOrder, current);
            const high = Math.max(sequenceAnchorOrder, current);
            const rangeAtoms = [...sequenceView.querySelectorAll<HTMLButtonElement>("[data-order]")]
              .filter((candidate) => {
                const order = Number(candidate.dataset.order);
                return order >= low && order <= high;
              })
              .flatMap((candidate) => (candidate.dataset.atoms ?? "").split(",").filter(Boolean).map(Number));
            selectionController.replace(rangeAtoms);
          } else if (!sequenceAnchor?.every((index) => item.atom_indices.includes(index))) {
            selectResidue(token, item.atom_indices, event.ctrlKey || event.metaKey);
          }
        });
      }
      track.appendChild(token);
    }
    row.append(label, track);
    sequenceView.appendChild(row);
  }
  if (!chains.length) {
    sequenceView.className = "sequence-view muted";
    sequenceView.textContent = "No polymer sequence is available.";
  } else {
    sequenceView.className = "sequence-view";
  }
  document.addEventListener("pointerup", () => { sequenceDragging = false; }, { once: true });
  element<HTMLButtonElement>("sequence-realize").disabled = !hasCollapsed;
  element<HTMLButtonElement>("sequence-collapse").disabled = !hasRealized;
  syncSequenceSelection();
}

function renderSummary(summary: StructureSummary): void {
  lastSummary = summary;
  const active = Object.entries(summary.condition_counts).filter(([, count]) => count > 0);
  summaryElement.innerHTML = `
    <dl class="summary-grid">
      <dt>Name</dt><dd>${escapeHtml(summary.name)}</dd>
      <dt>Atoms</dt><dd>${summary.atom_count.toLocaleString()}</dd>
      <dt>Chains</dt><dd>${escapeHtml(summary.chain_ids.join(", ") || "—")}</dd>
      <dt>Undo</dt><dd>${summary.undo_depth}/10</dd>
    </dl>
    <div class="condition-pills">
      ${active.map(([name, count]) => `<span class="pill">${escapeHtml(name)}: ${count}</span>`).join("")}
    </div>`;
  renderSequenceView(summary.sequence);
  renderAnnotationSummary(summary.annotations, summary.stored_fields, summary.scalar_fields);
  element<HTMLButtonElement>("undo").disabled = summary.undo_depth === 0;
  if (summary.validation.length) {
    log(summary.validation.map((issue) => `${issue.check}: ${issue.message}`).join("\n"));
  }
  renderViewerDrawers();
}

function renderConditionValue(): void {
  const definition = schema.conditions[conditionSelect.value];
  conditionValue.replaceChildren();
  if (definition.kind === "mask") return;

  const label = document.createElement("label");
  label.textContent = definition.kind === "vector3" ? "Centroid (x, y, z)" : "Value";
  if (definition.kind === "enum") {
    const select = document.createElement("select");
    select.id = "condition-value-input";
    for (const [name, value] of Object.entries(definition.values ?? {})) {
      if (value === definition.default) continue;
      select.add(new Option(name, String(value)));
    }
    label.appendChild(select);
  } else if (definition.kind === "integer") {
    const input = document.createElement("input");
    input.id = "condition-value-input";
    input.type = "number";
    input.value = "0";
    label.appendChild(input);
  } else {
    const wrapper = document.createElement("div");
    wrapper.className = "inline-fields";
    for (const [axis, value] of [["x", "0"], ["y", "0"], ["z", "0"]]) {
      const input = document.createElement("input");
      input.id = `condition-value-${axis}`;
      input.type = "number";
      input.step = "0.1";
      input.value = value;
      wrapper.appendChild(input);
    }
    label.appendChild(wrapper);
  }
  conditionValue.appendChild(label);
}

function selectedValue(): number | number[] | null {
  const definition = schema.conditions[conditionSelect.value];
  if (!enabledInput.checked || definition.kind === "mask") return null;
  if (definition.kind === "vector3") {
    return ["x", "y", "z"].map((axis) =>
      Number(element<HTMLInputElement>(`condition-value-${axis}`).value),
    );
  }
  return Number(element<HTMLInputElement | HTMLSelectElement>("condition-value-input").value);
}

function conditionChanged(): void {
  const level = schema.conditions[conditionSelect.value].level;
  scopeSelect.value = level === "chain" ? "chain" : level === "atom" ? "atom" : "residue";
  renderConditionValue();
}

function enableSessionControls(enabled: boolean): void {
  for (const id of [
    "download", "validate", "reset-residues", "realize-segments", "collapse-segments",
  ]) {
    element<HTMLButtonElement>(id).disabled = !enabled;
  }
  combineInput.disabled = !enabled;
  updateSelectionUi();
}

async function loadViewerCif(cif: string, name: string): Promise<void> {
  const wasmSummary = inspect_cif(cif) as {
    atom_count: number;
    condition_categories: string[];
  };
  await molecularViewer.loadCif(cif, name);
  const backend = molecularViewer.backend === "patinae" ? "Patinae" : "Mol*";
  log(
    `${backend} loaded ${wasmSummary.atom_count} atoms; lossless WASM found ` +
      `${wasmSummary.condition_categories.length} annotation categories.`,
  );
}

async function inspectAtom(atomIndex: number): Promise<void> {
  if (!sessionId) return;
  try {
    const atom = await api<{
      atom_index: number;
      chain: string;
      residue_id: number;
      residue_name: string;
      atom_name: string;
      formal_charge?: number;
      annotations: Array<{ label: string; values: AnnotationValue[] }>;
      stored_fields: Array<{ name: string; label: string; value?: AnnotationValue["value"] }>;
      scalar_fields: Array<{ name: string; label: string; value: number }>;
    }>(`/api/sessions/${sessionId}/atoms/${atomIndex}/annotations`);
    const lines = [
      `${atom.chain}/${atom.residue_name} ${atom.residue_id}/${atom.atom_name} (atom ${atom.atom_index})`,
      atom.formal_charge ? `Formal charge: ${atom.formal_charge > 0 ? "+" : ""}${atom.formal_charge}` : "",
      ...atom.annotations.map((annotation) => {
        const values = annotation.values.map((value) => {
          if (Array.isArray(value.value)) return `${value.label} (${value.value.join(", ")})`;
          return value.value === undefined ? value.label : `${value.label} (${value.value})`;
        });
        return `${annotation.label}: ${values.join(", ")}`;
      }),
      ...atom.stored_fields.map((field) => `${field.name}: ${field.label}`),
      ...atom.scalar_fields.map((field) => `${field.label}: ${field.value.toFixed(2)}`),
    ].filter(Boolean);
    if (!atom.annotations.length) lines.push("No active design annotations on this atom.");
    annotationDetail.textContent = lines.join("\n");
    for (const row of annotationList.querySelectorAll(".annotation-row")) row.classList.remove("active");
  } catch (error) {
    annotationDetail.textContent = `Could not inspect atom: ${String(error)}`;
  }
}

async function handleAtomPicked(atomIndex: number, label?: string): Promise<void> {
  if (!sessionId) return;
  if (measurementDraft) {
    if (!measurementDraft.atomIndices.includes(atomIndex)) measurementDraft.atomIndices.push(atomIndex);
    const required = measurementDraft.kind === "distance" ? 2 : measurementDraft.kind === "angle" ? 3 : 4;
    selectionController.replace(measurementDraft.atomIndices);
    if (measurementDraft.atomIndices.length < required) {
      log(`${label ?? `Atom ${atomIndex}`} added to ${measurementDraft.kind}; pick ${required - measurementDraft.atomIndices.length} more.`);
      return;
    }
    const request = measurementDraft;
    measurementDraft = null;
    try {
      await molecularViewer.addMeasurement(request);
      renderViewerDrawers();
      studioMenu.open = true;
      element<HTMLDetailsElement>("measurements-drawer").open = true;
      log(`${request.kind} measurement added and visualized.`);
    } catch (error) {
      log(`Measurement failed: ${String(error)}`);
    }
    updateSelectionUi();
    return;
  }
  const scope = pickScope.value as SelectionScope;
  try {
    const resolved = scope === "atom"
      ? { atom_indices: [atomIndex] }
      : await api<{ atom_indices: number[]; count: number }>(
        `/api/sessions/${sessionId}/selections/resolve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ atom_indices: [atomIndex], scope }),
        },
      );
    const remove = selectionController.toggle(resolved.atom_indices);
    log(
      `${label ?? `Atom ${atomIndex}`} · ${remove ? "removed" : "selected"} ` +
      `${resolved.atom_indices.length} atom${resolved.atom_indices.length === 1 ? "" : "s"} (${scope})`,
    );
    void inspectAtom(atomIndex);
  } catch (error) {
    log(`Could not resolve ${scope} selection: ${String(error)}`);
  }
}

const FALLBACK_SAFE_PATINAE_COMMANDS = new Set([
  "show",
  "hide",
  "color",
  "zoom",
  "center",
  "orient",
  "enable",
  "disable",
  "toggle",
]);
const STUDIO_UNSAFE_PATINAE_COMMANDS = new Set([
  "alter", "bond", "create", "delete", "extract", "fuse", "load", "load_traj",
  "pseudoatom", "remove", "rotate", "transform_selection", "translate", "unbond",
]);

async function executeViewerCommand(command: string, syntax = selectionSyntax.value as SelectionSyntax): Promise<void> {
  const trimmed = command.trim();
  if (!trimmed) return;
  if (/[\n\r;]/.test(trimmed)) {
    throw new Error("Run one command at a time; command separators are not allowed.");
  }

  const atomworksMatch = /^(awselect|awselect-path)\s+(.+)$/i.exec(trimmed);
  if (syntax !== "patinae" || atomworksMatch) {
    if (!sessionId) throw new Error("Open a CIF before selecting atoms.");
    const querySyntax = atomworksMatch
      ? atomworksMatch[1].toLowerCase() === "awselect-path" ? "path" : "expression"
      : syntax;
    const expression = atomworksMatch?.[2] ?? trimmed;
    const response = await postJson<{ atom_indices: number[]; count: number }>(
      `/api/sessions/${sessionId}/selections/query`,
      { expression, syntax: querySyntax },
    );
    selectionController.replace(response.atom_indices, true);
    log(`AtomWorks ${querySyntax} query selected ${response.count} atoms.`);
    return;
  }

  const verb = trimmed.split(/\s+/, 1)[0].toLowerCase();
  const catalog = molecularViewer.getCommandCatalog();
  const metadata = catalog.find((command) =>
    command.name.toLowerCase() === verb
    || command.aliases?.some((alias) => alias.toLowerCase() === verb)
  );
  const allowed = catalog.length
    ? metadata?.webSafe !== false && metadata !== undefined && !STUDIO_UNSAFE_PATINAE_COMMANDS.has(metadata.name)
    : FALLBACK_SAFE_PATINAE_COMMANDS.has(verb);
  if (!allowed) {
    throw new Error(
      STUDIO_UNSAFE_PATINAE_COMMANDS.has(metadata?.name ?? verb)
        ? `Command '${verb}' would desynchronize the annotated CIF. Use AtomWorks Studio editing controls instead.`
        : metadata?.unavailableReason
        ? `Command '${verb}' is unavailable in the browser: ${metadata.unavailableReason}`
        : `Unsupported viewer command '${verb}'. Browse the web-safe command catalog under •••.`,
    );
  }
  const result = molecularViewer.executeCommand(trimmed) as {
    messages?: Array<{ level: string; text: string }>;
  };
  const messages = result?.messages?.map((message) => message.text.trim()).filter(Boolean) ?? [];
  log(messages.join("\n") || `Patinae: ${trimmed}`);
}

async function selectByExpression(expression: string, label: string): Promise<void> {
  if (!sessionId) throw new Error("Open a CIF before selecting atoms.");
  const response = await api<{ atom_indices: number[]; count: number }>(
    `/api/sessions/${sessionId}/selections/query`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expression, syntax: "expression" }),
    },
  );
  selectionController.replace(response.atom_indices, true);
  log(`${label}: ${response.count} atoms selected.`);
}

function initializeViewerToolbar(): void {
  for (const menu of document.querySelectorAll<HTMLDetailsElement>(".viewer-menu")) {
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      closeToolbarMenus(menu);
    });
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-representation]")) {
    button.addEventListener("click", () => {
      try {
        molecularViewer.setRepresentation(
          button.dataset.representation as ViewerRepresentation,
          viewerActionTarget.value as ViewerTarget,
          representationExclusive.checked,
        );
        button.closest("details")?.removeAttribute("open");
      } catch (error) {
        log(`Display action failed: ${String(error)}`);
      }
    });
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-color]")) {
    button.addEventListener("click", () => {
      try {
        molecularViewer.setColor(
          button.dataset.color as ViewerColor,
          viewerActionTarget.value as ViewerTarget,
        );
        button.closest("details")?.removeAttribute("open");
      } catch (error) {
        log(`Color action failed: ${String(error)}`);
      }
    });
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-camera]")) {
    button.addEventListener("click", () => {
      try {
        molecularViewer.camera(button.dataset.camera as "reset" | "zoom" | "center" | "orient");
        button.closest("details")?.removeAttribute("open");
      } catch (error) {
        log(`View action failed: ${String(error)}`);
      }
    });
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-background]")) {
    button.addEventListener("click", () => {
      try {
        molecularViewer.setBackground(button.dataset.background as "black" | "white");
        button.closest("details")?.removeAttribute("open");
      } catch (error) {
        log(`Background action failed: ${String(error)}`);
      }
    });
  }
  const selectionExpressions: Record<string, string> = {
    all: "is_polymer | ~is_polymer",
    polymer: "is_polymer",
    ligand: "~is_polymer",
  };
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-selection]")) {
    button.addEventListener("click", () => {
      const preset = button.dataset.selection ?? "";
      if (preset === "none") {
        clearSelection();
        return;
      }
      void selectByExpression(selectionExpressions[preset], `Select ${preset}`)
        .catch((error) => log(`Selection failed: ${String(error)}`));
    });
  }
}

function refreshCapabilityUi(): void {
  const capabilities = molecularViewer.capabilities;
  applyCapabilities(capabilities);
  element("style-menu").hidden = !capabilities.representations && !capabilities.colors;
  element("picking-fallback").hidden = !capabilities.softwarePicking;
  pickScope.disabled = !capabilities.picking;
  selectionSyntax.querySelector<HTMLOptionElement>('option[value="patinae"]')!.disabled =
    !capabilities.commands;
  if (!capabilities.commands && selectionSyntax.value === "patinae") selectionSyntax.value = "expression";
  renderCommandCatalog();
  updateSelectionUi();
}

function renderCommandCatalog(): void {
  const container = element("command-catalog");
  const query = element<HTMLInputElement>("command-search").value.trim().toLowerCase();
  const commands = molecularViewer.getCommandCatalog().filter((command) =>
    !query
    || command.name.toLowerCase().includes(query)
    || command.description?.toLowerCase().includes(query)
  );
  container.replaceChildren();
  if (!commands.length) {
    container.textContent = molecularViewer.capabilities.commandCatalog
      ? "No matching commands."
      : "Choose Patinae to browse commands.";
    return;
  }
  for (const command of commands) {
    const button = document.createElement("button");
    button.type = "button";
    const studioUnsafe = STUDIO_UNSAFE_PATINAE_COMMANDS.has(command.name);
    button.disabled = command.webSafe === false || studioUnsafe;
    const name = document.createElement("strong");
    name.textContent = command.name;
    const description = document.createElement("small");
    description.textContent = studioUnsafe
      ? "Use AtomWorks Studio so the annotated CIF remains canonical."
      : command.webSafe === false
      ? command.unavailableReason ?? "Desktop only"
      : command.usage ?? command.description ?? "";
    button.append(name, description);
    button.addEventListener("click", () => {
      selectionSyntax.value = "patinae";
      viewerCommand.value = `${command.name} `;
      viewerCommand.focus();
      closeToolbarMenus();
    });
    container.append(button);
  }
}

element("command-search").addEventListener("input", renderCommandCatalog);

function renderViewerDrawers(): void {
  const objects = element("object-list");
  const viewerObjects = molecularViewer.getObjectList();
  objects.replaceChildren();
  if (viewerObjects.length) {
    for (const object of viewerObjects) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "drawer-object";
      button.textContent = `${object.enabled === false ? "○" : "●"} ${object.name} · ${object.type ?? "object"}`
        + (object.atomCount === undefined ? "" : ` · ${object.atomCount.toLocaleString()} atoms`);
      button.title = object.enabled === false ? "Show object" : "Hide object";
      button.addEventListener("click", () => {
        molecularViewer.setObjectEnabled(object.name, object.enabled === false);
        renderViewerDrawers();
      });
      objects.append(button);
    }
  } else {
    objects.textContent = lastSummary
      ? `${lastSummary.name} · ${lastSummary.atom_count.toLocaleString()} atoms · chains ${lastSummary.chain_ids.join(", ") || "—"}`
      : "Open a structure to list objects.";
  }
  const selections = molecularViewer.getSelectionList();
  const selectionList = element("selection-list");
  selectionList.replaceChildren();
  if (selections.length) {
    for (const selection of selections) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "drawer-object";
      button.textContent = `${selection.name}${selection.expression ? ` · ${selection.expression}` : ""}`;
      button.addEventListener("click", () => {
        try {
          const atoms = molecularViewer.evaluateSelection(selection.name);
          selectionController.replace(atoms, true);
        } catch (error) {
          log(`Named selection failed: ${String(error)}`);
        }
      });
      selectionList.append(button);
    }
  } else {
    selectionList.textContent = `${selectionController.size} atoms in active selection`;
  }
  const measurements = molecularViewer.listMeasurements();
  element("measurement-list").textContent = measurements.length
    ? measurements.map((item) =>
      `${item.label ?? item.kind ?? "Measurement"}${item.value === undefined ? "" : ` · ${item.value}${item.unit ?? ""}`}`
    ).join("\n")
    : "No measurements.";
}

async function switchRenderer(mode: ViewerMode): Promise<void> {
  viewerModeSelect.disabled = true;
  try {
    await molecularViewer.switchBackend(mode);
    localStorage.setItem(VIEWER_STORAGE_KEY, mode);
    const url = new URL(window.location.href);
    url.searchParams.set("viewer", mode);
    history.replaceState(null, "", url);
    refreshCapabilityUi();
    renderViewerDrawers();
    log(`Switched renderer to ${mode === "patinae" ? "Patinae" : "Mol*"} without reloading.`);
  } catch (error) {
    viewerModeSelect.value = molecularViewer.backend;
    log(`Renderer switch failed: ${String(error)}`);
  } finally {
    viewerModeSelect.disabled = false;
  }
}

async function refreshFromServer(summary?: StructureSummary, preserveSelection = true): Promise<void> {
  if (!sessionId) return;
  const response = await fetch(`/api/sessions/${sessionId}/cif`);
  if (!response.ok) throw new Error(await response.text());
  await loadViewerCif(await response.text(), currentName);
  if (summary) renderSummary(summary);
  if (preserveSelection) {
    if (summary) selectionController.retainValid(summary.atom_count);
    await annotationOverlay.restore(molecularViewer, selectionController.values);
  } else {
    annotationOverlay.reset();
    clearSelection();
  }
}

async function mutate(path: string, body?: unknown): Promise<void> {
  if (!sessionId) return;
  try {
    const response = await api<SessionResponse>(`/api/sessions/${sessionId}${path}`, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    persistSession(response);
    const structural = /^\/(?:delete|segments|reset-residue-ids)/.test(path);
    await refreshFromServer(response.structure, !structural);
    log("Annotation Studio state updated.");
    notifyHost("changed");
  } catch (error) {
    log(`Error: ${String(error)}`);
  }
}

async function openFile(file: File): Promise<boolean> {
  try {
    const cif = await file.text();
    inspect_cif(cif); // fail fast without uploading malformed/ragged CIF
    const form = new FormData();
    form.append("file", file);
    const response = await api<SessionResponse>("/api/sessions", { method: "POST", body: form });
    sessionId = response.session_id;
    currentName = response.structure.name;
    selectionController.clear();
    persistSession(response);
    await refreshFromServer(response.structure, false);
    enableSessionControls(true);
    log("Native AtomWorks session ready.");
    notifyHost("ready");
    return true;
  } catch (error) {
    log(`Open failed: ${String(error)}`);
    return false;
  }
}

async function loadExample(example: StudioExample, activeButton: HTMLButtonElement): Promise<void> {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>(".example-load")];
  for (const button of buttons) button.disabled = true;
  const originalLabel = activeButton.textContent;
  activeButton.textContent = "Loading…";
  try {
    const loaded = await openFile(
      new File([example.cif], example.filename, { type: "chemical/x-mmcif" }),
    );
    if (!loaded) return;
    const selectedMetrics = designController?.applyPreset(example.metricsTags, example.search) ?? [];
    log(
      `Loaded ${example.name}.`
      + (selectedMetrics.length ? ` Suggested metrics selected: ${selectedMetrics.join(", ")}.` : ""),
    );
  } finally {
    activeButton.textContent = originalLabel;
    for (const button of buttons) button.disabled = false;
  }
}

async function initialize(): Promise<void> {
  document.body.classList.toggle("embedded", query.get("embed") === "1");
  const mode = requestedViewerMode() ?? "molstar";
  viewerModeSelect.value = mode;
  viewerModeSelect.addEventListener("change", () => {
    const selected = viewerMode(viewerModeSelect.value);
    if (!selected) return;
    void switchRenderer(selected);
  });
  localStorage.setItem(VIEWER_STORAGE_KEY, mode);
  await initCifCore();
  schema = await api<StudioSchema>("/api/schema");
  for (const [name, definition] of Object.entries(schema.conditions)) {
    conditionSelect.add(new Option(definition.label, name));
  }
  for (const name of schema.bond_types) {
    element<HTMLSelectElement>("bond-type").add(new Option(name, name));
  }
  conditionChanged();

  molecularViewer = new MolecularViewer(
    element("viewer"),
    viewerBackend,
    mode,
    handleAtomPicked,
  );
  await molecularViewer.initialize();
  initializeViewerToolbar();
  refreshCapabilityUi();
  let restoredSession = false;
  const savedSession = query.get("session") ?? sessionStorage.getItem("annotation-studio-session");
  if (savedSession) {
    try {
      sessionId = savedSession;
      const restored = await api<SessionResponse>(`/api/sessions/${sessionId}`);
      currentName = restored.structure.name;
      await refreshFromServer(restored.structure);
      persistSession(restored);
      enableSessionControls(true);
      notifyHost("ready");
      const message = sessionStorage.getItem("annotation-studio-message");
      sessionStorage.removeItem("annotation-studio-message");
      log(message ?? "Restored native AtomWorks session.");
      restoredSession = true;
    } catch {
      sessionStorage.removeItem("annotation-studio-session");
      sessionStorage.removeItem("annotation-studio-name");
    }
  }
  if (!restoredSession) log("Ready. Open a CIF to start.");
  designController = await initializeDesignController({
    request: api,
    getSessionId: () => sessionId,
    openDocument: async (designDocument) => {
      sessionId = designDocument.session_id;
      currentName = designDocument.structure.name;
      persistSession({
        session_id: designDocument.session_id,
        editor_path: designDocument.editor_path,
        structure: designDocument.structure as StructureSummary,
      });
      await refreshFromServer(designDocument.structure as StructureSummary, false);
      if (designDocument.kind === "refolded") {
        const confidence = (designDocument.structure as StructureSummary).scalar_fields.find(
          (field) => field.name === "b_factor" || /plddt|confidence/i.test(field.name),
        );
        if (confidence) {
          await displayAnnotation(confidence);
          annotationDetail.textContent = `${confidence.label} colored on the refolded structure.`;
        }
      }
      enableSessionControls(true);
      notifyHost("changed");
      log(`Opened ${designDocument.kind} structure ${designDocument.label}.`);
    },
    log,
  });
  designController.updateSession();
  for (const button of document.querySelectorAll<HTMLButtonElement>(".example-load")) {
    button.disabled = false;
  }
  const pendingExample = sessionStorage.getItem(PENDING_EXAMPLE_KEY);
  if (pendingExample) {
    sessionStorage.removeItem(PENDING_EXAMPLE_KEY);
    const example = STUDIO_EXAMPLES.find((candidate) => candidate.id === pendingExample);
    const button = document.querySelector<HTMLButtonElement>(
      `.example-load[data-example="${pendingExample}"]`,
    );
    if (example && button) await loadExample(example, button);
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) void openFile(file);
});
for (const button of document.querySelectorAll<HTMLButtonElement>(".example-load")) {
  button.addEventListener("click", () => {
    const example = STUDIO_EXAMPLES.find((candidate) => candidate.id === button.dataset.example);
    if (!example) return;
    void loadExample(example, button);
  });
}
for (const button of document.querySelectorAll<HTMLButtonElement>(".renderer-prompt-choice")) {
  button.addEventListener("click", () => {
    const selected = viewerMode(button.dataset.renderer ?? null);
    if (selected) {
      viewerModeSelect.value = selected;
      element("renderer-prompt").hidden = true;
      void switchRenderer(selected);
    }
  });
}
combineInput.addEventListener("change", () => {
  const file = combineInput.files?.[0];
  if (!file || !sessionId) return;
  const targetSession = sessionId;
  void (async () => {
    try {
      const cif = await file.text();
      inspect_cif(cif);
      const form = new FormData();
      form.append("file", file);
      const incoming = await api<SessionResponse>("/api/sessions", { method: "POST", body: form });
      const combined = await api<SessionResponse>(`/api/sessions/${targetSession}/combine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_ids: [incoming.session_id] }),
      });
      if (sessionId !== targetSession) return;
      persistSession(combined);
      await refreshFromServer(combined.structure, false);
      log(`Combined ${file.name}; conflicting chain IDs were reassigned.`);
      notifyHost("changed");
    } catch (error) {
      log(`Combine failed: ${String(error)}`);
    } finally {
      combineInput.value = "";
    }
  })();
});
conditionSelect.addEventListener("change", conditionChanged);
pickScope.addEventListener("change", () => selectionController.setScope(pickScope.value as SelectionScope));
viewerCommandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const command = viewerCommand.value;
  void executeViewerCommand(command)
    .then(() => {
      viewerCommand.value = "";
    })
    .catch((error) => log(`Command failed: ${String(error)}`));
});
element("clear-selection").addEventListener("click", clearSelection);
element("clear-annotation-view").addEventListener("click", () => {
  void annotationOverlay.clear(molecularViewer, selectionController.values).then(() => {
    element<HTMLButtonElement>("clear-annotation-view").disabled = true;
    annotationDetail.textContent = "Annotation colors cleared. Click an annotation or pick an atom.";
  }).catch((error) => log(`Could not clear annotation colors: ${String(error)}`));
});

for (const [buttonId, drawerId] of [
  ["open-objects", "objects-drawer"],
  ["open-selections", "selections-drawer"],
  ["open-measurements", "measurements-drawer"],
] as const) {
  element(buttonId).addEventListener("click", () => {
    closeToolbarMenus();
    studioMenu.open = true;
    element<HTMLDetailsElement>(drawerId).open = true;
    renderViewerDrawers();
  });
}

element("run-analysis").addEventListener("click", () => {
  if (!sessionId) return;
  const interactionTypes = [
    ...document.querySelectorAll<HTMLInputElement>('input[name="interaction-type"]:checked'),
  ].map((input) => input.value);
  if (!interactionTypes.length) {
    log("Choose at least one interaction type.");
    return;
  }
  const selectedAtoms = [...selectionController.values];
  const interactionScope = element<HTMLSelectElement>("interaction-scope").value;
  if (interactionScope !== "all" && !selectedAtoms.length) {
    log("Select atoms before using Selection contacts scope.");
    return;
  }
  const selectedSet = new Set(selectedAtoms);
  const targetAtoms = interactionScope === "within-selection"
    ? selectedAtoms
    : interactionScope === "selection-to-rest"
      ? Array.from({ length: lastSummary?.atom_count ?? 0 }, (_, index) => index)
        .filter((index) => !selectedSet.has(index))
      : [];
  void postJson<InteractionAnalysisResponse>(
    `/api/sessions/${sessionId}/interactions/analyze`,
    {
      interaction_types: interactionTypes,
      hbond_model: element<HTMLSelectElement>("hbond-model").value,
      inter_chain_only: element<HTMLInputElement>("inter-chain-only").checked,
      source_atom_indices: interactionScope === "all" ? [] : selectedAtoms,
      target_atom_indices: targetAtoms,
    },
  ).then(async (response) => {
    const colors: Record<string, number> = {
      hbond: 0x4dd0e1,
      hydrophobic: 0xffb74d,
      pistacking: 0xce93d8,
      pication: 0xf06292,
      saltbridge: 0x64b5f6,
      halogen: 0x81c784,
      metal: 0xffd54f,
    };
    const overlays: ViewerInteraction[] = response.interactions.map((interaction, index) => ({
      id: `interaction_${interaction.interaction_type}_${index}`,
      type: interaction.interaction_type,
      atom1: interaction.atom1,
      atom2: interaction.atom2,
      label: `${interaction.interaction_type} ${interaction.distance.toFixed(2)} Å`,
      color: colors[interaction.interaction_type] ?? 0xffd54f,
    }));
    const visualLimit = Number(element<HTMLInputElement>("interaction-visual-limit").value);
    const visualized = overlays.slice(0, visualLimit);
    await molecularViewer.showInteractions(
      element<HTMLInputElement>("visualize-interactions").checked ? visualized : [],
    );
    const results = element("analysis-results");
    results.replaceChildren();
    if (!response.interactions.length) results.textContent = "No matching interactions found.";
    for (const interaction of response.interactions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "interaction-result";
      button.textContent = `${interaction.interaction_type}: atoms ${interaction.atom1}–${interaction.atom2}`
        + ` · ${interaction.distance.toFixed(2)} Å · ${interaction.atom1_role}/${interaction.atom2_role}`;
      button.addEventListener("click", () => {
        selectionController.replace([interaction.atom1, interaction.atom2], true);
        molecularViewer.camera("center");
      });
      results.append(button);
    }
    element<HTMLDetailsElement>("measurements-drawer").open = true;
    studioMenu.open = true;
    renderViewerDrawers();
    log(
      `Interaction analysis completed: ${response.interaction_count} contacts`
      + `${visualized.length ? `; ${visualized.length} visualized` : ""}`
      + `${response.cached ? " (cached)" : ""}.`,
    );
  }).catch((error) => log(`Analysis failed: ${String(error)}`));
});

element("measurement-kind").addEventListener("change", () => {
  measurementDraft = null;
  updateSelectionUi();
});
element("add-measurement").addEventListener("click", () => {
  const kind = element<HTMLSelectElement>("measurement-kind").value as MeasurementKind;
  const atoms = [...selectionController.values];
  const required = kind === "distance" ? 2 : kind === "angle" ? 3 : 4;
  if (atoms.length !== required) {
    measurementDraft = { kind, atomIndices: [] };
    closeToolbarMenus();
    log(`${kind} measurement armed: pick ${required} atoms in order.`);
    return;
  }
  void molecularViewer.addMeasurement({ kind, atomIndices: atoms }).then(() => {
      renderViewerDrawers();
      element<HTMLDetailsElement>("measurements-drawer").open = true;
      studioMenu.open = true;
      log(`${kind} measurement added and visualized.`);
    }).catch((error) => log(`Measurement failed: ${String(error)}`));
});

element("clear-analysis-visuals").addEventListener("click", () => {
  void molecularViewer.clearMeasurements().then(() => {
    element("analysis-results").replaceChildren();
    renderViewerDrawers();
    log("Measurement and interaction visuals cleared.");
  });
});

element("inspect-bonds").addEventListener("click", () => {
  if (!sessionId) return;
  const selected = [...selectionController.values];
  const query = selected.length === 1 ? `?atom_index=${selected[0]}` : "";
  void api<{
    count: number;
    bonds: Array<{ atom1: number; atom2: number; bond_type: string }>;
  }>(`/api/sessions/${sessionId}/bonds${query}`).then((response) => {
    element("analysis-results").textContent = response.bonds.length
      ? response.bonds.map((bond) => `Bond ${bond.atom1}–${bond.atom2} · ${bond.bond_type}`).join("\n")
      : "No bonds found.";
    element<HTMLDetailsElement>("measurements-drawer").open = true;
  }).catch((error) => log(`Bond inspection failed: ${String(error)}`));
});

element("infer-bonds").addEventListener("click", () => {
  void mutate("/bonds/infer-ccd");
});

async function editDistanceRestraint(action: "add" | "remove"): Promise<void> {
  if (!sessionId) return;
  const [atom1, atom2] = [...selectionController.values];
  const distance = Number(element<HTMLInputElement>("restraint-distance").value);
  await mutate("/distance-restraints", {
    atom1,
    atom2,
    action,
    distance: action === "add" ? distance : null,
  });
  const response = await api<{
    restraints: Array<{ atom1: number; atom2: number; distance: number; current_distance: number }>;
  }>(`/api/sessions/${sessionId}/distance-restraints`);
  element("analysis-results").textContent = response.restraints.length
    ? response.restraints.map((restraint) =>
      `Restraint ${restraint.atom1}–${restraint.atom2} · target ${restraint.distance.toFixed(2)} Å`
      + ` · current ${restraint.current_distance.toFixed(2)} Å`
    ).join("\n")
    : "No distance restraints.";
  element<HTMLDetailsElement>("measurements-drawer").open = true;
}

element("add-restraint").addEventListener("click", () => {
  void editDistanceRestraint("add").catch((error) => log(`Restraint failed: ${String(error)}`));
});
element("remove-restraint").addEventListener("click", () => {
  void editDistanceRestraint("remove").catch((error) => log(`Restraint failed: ${String(error)}`));
});

applyCondition.addEventListener("click", () => {
  void mutate("/conditions", {
    condition: conditionSelect.value,
    atom_indices: [...selectionController.values],
    enabled: enabledInput.checked,
    value: selectedValue(),
    scope: scopeSelect.value,
  });
});

element("apply-charge").addEventListener("click", () => {
  void mutate("/charges", {
    atom_indices: [...selectionController.values],
    value: Number(element<HTMLInputElement>("charge").value),
  });
});

for (const action of ["add", "remove"] as const) {
  element(`${action}-bond`).addEventListener("click", () => {
    const [atom1, atom2] = [...selectionController.values];
    void mutate("/bonds", {
      atom1,
      atom2,
      action,
      bond_type: element<HTMLSelectElement>("bond-type").value,
    });
  });
}

element("delete-atoms").addEventListener("click", () => {
  void mutate("/delete", { atom_indices: [...selectionController.values], close_residue_gaps: false });
});
element("reset-residues").addEventListener("click", () => void mutate("/reset-residue-ids"));
element("undo").addEventListener("click", () => void mutate("/undo"));

element("insert-segment").addEventListener("click", () => {
  void mutate("/segments", {
    minimum: Number(element<HTMLInputElement>("segment-min").value),
    maximum: Number(element<HTMLInputElement>("segment-max").value),
    atom_index: [...selectionController.values][0],
    before: element<HTMLInputElement>("segment-before").checked,
    new_chain: false,
  });
});
element("realize-segments").addEventListener("click", () => void mutate("/segments/realize", {}));
element("collapse-segments").addEventListener("click", () => void mutate("/segments/collapse"));
element("sequence-realize").addEventListener("click", () => void mutate("/segments/realize", {}));
element("sequence-collapse").addEventListener("click", () => void mutate("/segments/collapse"));

element("validate").addEventListener("click", async () => {
  if (!sessionId) return;
  try {
    const result = await api<{ issues: StructureSummary["validation"] }>(
      `/api/sessions/${sessionId}/validate?full=true`,
      { method: "POST" },
    );
    log(result.issues.length ? result.issues.map((issue) => `${issue.check}: ${issue.message}`).join("\n") : "Validation passed.");
  } catch (error) {
    log(`Validation failed: ${String(error)}`);
  }
});

downloadButton.addEventListener("click", async () => {
  if (!sessionId) return;
  const response = await fetch(`/api/sessions/${sessionId}/cif`);
  const blob = await response.blob();
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = `${currentName}_annotated.cif`;
  anchor.click();
  URL.revokeObjectURL(anchor.href);
});

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  void navigator.serviceWorker.register("/sw.js");
}

void initialize().catch((error) => log(`Initialization failed: ${String(error)}`));
