import initCifCore, { inspect_cif } from "./wasm/annotation_studio_core.js";
import { initializeDesignController } from "./design";
import { STUDIO_EXAMPLES, type StudioExample } from "./examples";
import { MolecularViewer, type ViewerMode } from "./viewers";
import "./style.css";

type ConditionDefinition = {
  label: string;
  kind: "mask" | "enum" | "integer" | "vector3";
  level: string;
  default: number | boolean | null;
  values?: Record<string, number>;
};

type StudioSchema = {
  conditions: Record<string, ConditionDefinition>;
  bond_types: string[];
};

type AnnotationValue = {
  label: string;
  value?: number | number[] | string | boolean | null;
  count: number;
  atom_indices?: number[];
  color?: { name: string; value: number };
};

type AnnotationSummary = {
  name: string;
  label: string;
  count: number;
  unit: string;
  values: AnnotationValue[];
  truncated?: boolean;
};

type SequenceResidue = {
  kind: "residue";
  atom_indices: number[];
  residue_id: number;
  residue_name: string;
  code: string;
  conditions: string[];
};

type SequenceSegment = {
  kind: "segment";
  atom_indices: number[];
  minimum: number;
  maximum: number;
  realized: boolean;
  realized_length: number | null;
};

type SequenceChain = {
  chain_id: string;
  items: Array<SequenceResidue | SequenceSegment>;
};

type StructureSummary = {
  name: string;
  atom_count: number;
  chain_ids: string[];
  sequence: SequenceChain[];
  condition_counts: Record<string, number>;
  annotations: AnnotationSummary[];
  stored_fields: AnnotationSummary[];
  scalar_fields: AnnotationSummary[];
  undo_depth: number;
  validation: Array<{ severity: string; check: string; message: string }>;
};

type SessionResponse = {
  session_id: string;
  editor_path?: string;
  structure: StructureSummary;
};

const element = <T extends HTMLElement>(id: string): T => {
  const value = document.getElementById(id);
  if (!value) throw new Error(`Missing element #${id}`);
  return value as T;
};

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

function navigateToViewer(selected: ViewerMode): void {
  localStorage.setItem(VIEWER_STORAGE_KEY, selected);
  const url = new URL(window.location.href);
  url.searchParams.set("viewer", selected);
  window.location.assign(url);
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
let selectedAtoms = new Set<number>();
let designController: {
  updateSession: () => void;
  applyPreset: (metricsTags: string[], search?: StudioExample["search"]) => string[];
} | null = null;

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

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]!,
  );
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      // Keep HTTP status when a proxy or static host returns non-JSON.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function updateSelectionUi(): void {
  const count = selectedAtoms.size;
  selectionCount.textContent = `${count} atom${count === 1 ? "" : "s"} selected`;
  const hasSession = sessionId !== null;
  applyCondition.disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("apply-charge").disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("delete-atoms").disabled = !hasSession || count === 0;
  element<HTMLButtonElement>("insert-segment").disabled = !hasSession || count !== 1;
  element<HTMLButtonElement>("add-bond").disabled = !hasSession || count !== 2;
  element<HTMLButtonElement>("remove-bond").disabled = !hasSession || count !== 2;
}

function clearSelection(): void {
  selectedAtoms.clear();
  updateSelectionUi();
  molecularViewer?.clearSelection();
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
  await molecularViewer.showAnnotation(groups);
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
        token.title = `${chain.chain_id}/${item.residue_name} ${item.residue_id}`
          + (item.conditions.length ? ` · ${item.conditions.join(", ")}` : "");
        for (const condition of item.conditions) token.classList.add(`condition-${condition}`);
        token.addEventListener("click", () => {
          selectedAtoms = new Set(item.atom_indices);
          updateSelectionUi();
          void molecularViewer.showAnnotation([
            {
              label: `${chain.chain_id}/${item.residue_name} ${item.residue_id}`,
              atomIndices: item.atom_indices,
            },
          ]);
          element<HTMLButtonElement>("clear-annotation-view").disabled = false;
          void inspectAtom(item.atom_indices[0]);
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
  element<HTMLButtonElement>("sequence-realize").disabled = !hasCollapsed;
  element<HTMLButtonElement>("sequence-collapse").disabled = !hasRealized;
}

function renderSummary(summary: StructureSummary): void {
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

function handleAtomPicked(atomIndex: number, label?: string): void {
  if (selectedAtoms.has(atomIndex)) selectedAtoms.delete(atomIndex);
  else selectedAtoms.add(atomIndex);
  updateSelectionUi();
  log(label ?? `Atom ${atomIndex}`);
  void inspectAtom(atomIndex);
}

async function refreshFromServer(summary?: StructureSummary): Promise<void> {
  if (!sessionId) return;
  const response = await fetch(`/api/sessions/${sessionId}/cif`);
  if (!response.ok) throw new Error(await response.text());
  await loadViewerCif(await response.text(), currentName);
  if (summary) renderSummary(summary);
  clearSelection();
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
    await refreshFromServer(response.structure);
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
    selectedAtoms.clear();
    updateSelectionUi();
    persistSession(response);
    await refreshFromServer(response.structure);
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
  const mode = requestedViewerMode();
  viewerModeSelect.value = mode ?? "";
  viewerModeSelect.addEventListener("change", () => {
    const selected = viewerMode(viewerModeSelect.value);
    if (!selected) return;
    navigateToViewer(selected);
  });
  if (!mode) {
    fileInput.disabled = true;
    viewerBackend.textContent = "Choose renderer";
    element("design-availability").textContent = "Choose a renderer to start the studio.";
    element("renderer-prompt").hidden = false;
    for (const button of document.querySelectorAll<HTMLButtonElement>(".example-load")) {
      button.disabled = false;
    }
    log("Choose Patinae or Mol*; neither renderer is selected automatically.");
    return;
  }
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
      await refreshFromServer(designDocument.structure as StructureSummary);
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
    if (!requestedViewerMode()) {
      sessionStorage.setItem(PENDING_EXAMPLE_KEY, example.id);
      element("renderer-prompt").hidden = false;
      log(`Choose Patinae or Mol* to load ${example.name}.`);
      return;
    }
    void loadExample(example, button);
  });
}
for (const button of document.querySelectorAll<HTMLButtonElement>(".renderer-prompt-choice")) {
  button.addEventListener("click", () => {
    const selected = viewerMode(button.dataset.renderer ?? null);
    if (selected) navigateToViewer(selected);
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
      await refreshFromServer(combined.structure);
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
element("clear-selection").addEventListener("click", clearSelection);
element("clear-annotation-view").addEventListener("click", () => {
  void molecularViewer.clearAnnotation().then(() => {
    element<HTMLButtonElement>("clear-annotation-view").disabled = true;
    annotationDetail.textContent = "Annotation colors cleared. Click an annotation or pick an atom.";
  }).catch((error) => log(`Could not clear annotation colors: ${String(error)}`));
});

applyCondition.addEventListener("click", () => {
  void mutate("/conditions", {
    condition: conditionSelect.value,
    atom_indices: [...selectedAtoms],
    enabled: enabledInput.checked,
    value: selectedValue(),
    scope: scopeSelect.value,
  });
});

element("apply-charge").addEventListener("click", () => {
  void mutate("/charges", {
    atom_indices: [...selectedAtoms],
    value: Number(element<HTMLInputElement>("charge").value),
  });
});

for (const action of ["add", "remove"] as const) {
  element(`${action}-bond`).addEventListener("click", () => {
    const [atom1, atom2] = [...selectedAtoms];
    void mutate("/bonds", {
      atom1,
      atom2,
      action,
      bond_type: element<HTMLSelectElement>("bond-type").value,
    });
  });
}

element("delete-atoms").addEventListener("click", () => {
  void mutate("/delete", { atom_indices: [...selectedAtoms], close_residue_gaps: false });
});
element("reset-residues").addEventListener("click", () => void mutate("/reset-residue-ids"));
element("undo").addEventListener("click", () => void mutate("/undo"));

element("insert-segment").addEventListener("click", () => {
  void mutate("/segments", {
    minimum: Number(element<HTMLInputElement>("segment-min").value),
    maximum: Number(element<HTMLInputElement>("segment-max").value),
    atom_index: [...selectedAtoms][0],
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
