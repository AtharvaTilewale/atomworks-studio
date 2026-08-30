import initCifCore, { inspect_cif } from "./wasm/annotation_studio_core.js";
import { api, apiText } from "./api";
import { initializeDesignController } from "./studio/design-controller";
import { applyCapabilities, element, escapeHtml } from "./dom";
import type { StudioExample } from "./examples";
import { SelectionController } from "./selection-controller";
import { AnalysisController } from "./studio/analysis-controller";
import { AnnotationController } from "./studio/annotation-controller";
import { ConditionController } from "./studio/condition-controller";
import {
  PENDING_EXAMPLE_KEY,
  VIEWER_STORAGE_KEY,
  parseViewerMode,
  persistSession as persistSessionStorage,
  requestedViewerMode,
} from "./studio/persistence";
import { SequenceController } from "./studio/sequence-controller";
import { SessionFileController } from "./studio/session-file-controller";
import { StructureEditController } from "./studio/structure-edit-controller";
import { ViewerToolbarController } from "./studio/viewer-toolbar-controller";
import type {
  SelectionScope,
  SessionResponse,
  StructureSummary,
  StudioSchema,
} from "./types";
import { MolecularViewer } from "./viewers";
import "./style.css";

const combineInput = element<HTMLInputElement>("combine-input");
const selectionCount = element<HTMLSpanElement>("selection-count");
const viewerBackend = element<HTMLSpanElement>("viewer-backend");
const viewerModeSelect = element<HTMLSelectElement>("viewer-mode");
const pickScope = element<HTMLSelectElement>("pick-scope");
const selectionSyntax = element<HTMLSelectElement>("selection-syntax");
const summaryElement = element<HTMLDivElement>("structure-summary");
const statusElement = element<HTMLPreElement>("status");
const query = new URLSearchParams(window.location.search);
const hostThemes = new Set(["light", "dark", "high-contrast", "high-contrast-light"]);

function applyHostTheme(theme: unknown): void {
  if (typeof theme === "string" && hostThemes.has(theme)) {
    document.body.dataset.hostTheme = theme;
  }
}

window.addEventListener("message", (event) => {
  if (
    query.get("host") === "vscode" &&
    event.source === window.parent &&
    event.data?.source === "atomworks-vscode-host" &&
    event.data?.type === "theme"
  ) {
    applyHostTheme(event.data.theme);
  }
});

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
let sessionId: string | null = null;
let currentName = "structure";
let lastSummary: StructureSummary | null = null;
let designController: {
  updateSession: () => void;
  applyPreset: (metricsTags: string[], search?: StudioExample["search"]) => string[];
} | null = null;
let viewerToolbarController: ViewerToolbarController | null = null;
let analysisController: AnalysisController | null = null;
let annotationController: AnnotationController | null = null;
let conditionController: ConditionController | null = null;
let sequenceController: SequenceController | null = null;
let structureEditController: StructureEditController | null = null;
let sessionFileController: SessionFileController | null = null;
const selectionController = new SelectionController(
  () => molecularViewer,
  () => updateSelectionUi(),
);

function persistSession(response: SessionResponse, message?: string): void {
  persistSessionStorage(response, message);
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
  conditionController?.updateSelectionUi(hasSession);
  structureEditController?.updateSelectionUi(hasSession);
  element<HTMLButtonElement>("run-analysis").disabled = !hasSession;
  analysisController?.updateSelectionUi(hasSession);
  element<HTMLButtonElement>("inspect-bonds").disabled = !hasSession;
  element<HTMLButtonElement>("infer-bonds").disabled = !hasSession;
  element<HTMLButtonElement>("add-restraint").disabled = !hasSession || count !== 2;
  element<HTMLButtonElement>("remove-restraint").disabled = !hasSession || count !== 2;
  sequenceController?.syncSelection();
}

function renderSummary(summary: StructureSummary): void {
  lastSummary = summary;
  const active = Object.entries(summary.condition_counts).filter(([, count]) => count > 0);
  summaryElement.classList.remove("muted");
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
  sequenceController?.render(summary.sequence);
  annotationController?.render(summary.annotations, summary.stored_fields, summary.scalar_fields);
  structureEditController?.updateKnownChains(summary.chain_ids);
  element<HTMLButtonElement>("undo").disabled = summary.undo_depth === 0;
  if (summary.validation.length) {
    log(summary.validation.map((issue) => `${issue.check}: ${issue.message}`).join("\n"));
  }
  viewerToolbarController?.renderDrawers();
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
  if (!molecularViewer.initialized) {
    log(
      `Imported ${wasmSummary.atom_count} atoms while the renderer is unavailable; ` +
        `${wasmSummary.condition_categories.length} annotation categories remain editable.`,
    );
    return;
  }
  const backend = molecularViewer.backend === "patinae" ? "Patinae" : "Mol*";
  log(
    `${backend} loaded ${wasmSummary.atom_count} atoms; lossless WASM found ` +
      `${wasmSummary.condition_categories.length} annotation categories.`,
  );
}

async function handleAtomPicked(atomIndex: number, label?: string): Promise<void> {
  if (!sessionId) return;
  if (await analysisController?.handleAtomPicked(atomIndex, label)) return;
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
    void annotationController?.inspectAtom(atomIndex);
  } catch (error) {
    log(`Could not resolve ${scope} selection: ${String(error)}`);
  }
}

async function refreshFromServer(summary?: StructureSummary, preserveSelection = true): Promise<void> {
  if (!sessionId) return;
  const cif = await apiText(`/api/sessions/${sessionId}/cif`);
  await loadViewerCif(cif, currentName);
  if (summary) renderSummary(summary);
  if (preserveSelection) {
    if (summary) selectionController.retainValid(summary.atom_count);
    await annotationController?.restore();
  } else {
    annotationController?.reset();
    selectionController.clear();
  }
}

async function mutate(path: string, body?: unknown): Promise<void> {
  if (!sessionId) return;
  const targetSession = sessionId;
  try {
    const response = await api<SessionResponse>(`/api/sessions/${targetSession}${path}`, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (sessionId !== targetSession) return;
    persistSession(response);
    const structural = /^\/(?:delete|segments|reset-residue-ids)/.test(path);
    if (structural) await molecularViewer.clearAllVisuals();
    await refreshFromServer(response.structure, !structural);
    log("Annotation Studio state updated.");
    notifyHost("changed");
  } catch (error) {
    if (sessionId === targetSession) log(`Error: ${String(error)}`);
  }
}

async function initialize(): Promise<void> {
  document.body.classList.toggle("embedded", query.get("embed") === "1");
  const host = query.get("host");
  document.body.classList.toggle("host-vscode", host === "vscode");
  if (host === "vscode") applyHostTheme(query.get("hostTheme"));
  // A renderer pinned in the URL (?viewer=) is honored exactly and never auto-swapped.
  // Otherwise Patinae is the default and may auto-fall-back to Mol* when WebGPU is absent.
  const urlPinnedViewer = parseViewerMode(query.get("viewer"));
  const mode = requestedViewerMode(query) ?? "patinae";
  viewerModeSelect.value = mode;
  localStorage.setItem(VIEWER_STORAGE_KEY, mode);
  await initCifCore();
  const schema = await api<StudioSchema>("/api/schema");
  molecularViewer = new MolecularViewer(
    element("viewer"),
    viewerBackend,
    mode,
    handleAtomPicked,
  );
  viewerToolbarController = new ViewerToolbarController({
    viewer: () => molecularViewer,
    selection: selectionController,
    getSessionId: () => sessionId,
    getSummary: () => lastSummary,
    requestedMode: () => requestedViewerMode(query),
    log,
  });
  analysisController = new AnalysisController({
    viewer: () => molecularViewer,
    selection: selectionController,
    getSessionId: () => sessionId,
    getAtomCount: () => lastSummary?.atom_count ?? 0,
    mutate,
    renderViewerDrawers: () => viewerToolbarController?.renderDrawers(),
    log,
    onSelectionUiChange: updateSelectionUi,
  });
  annotationController = new AnnotationController({
    viewer: () => molecularViewer,
    selection: selectionController,
    getSessionId: () => sessionId,
    log,
  });
  conditionController = new ConditionController({ selection: selectionController, mutate });
  sequenceController = new SequenceController({
    selection: selectionController,
    mutate,
    inspectAtom: (atomIndex) => annotationController!.inspectAtom(atomIndex),
  });
  structureEditController = new StructureEditController({
    selection: selectionController,
    getSessionId: () => sessionId,
    mutate,
    log,
  });
  sessionFileController = new SessionFileController({
    getSessionId: () => sessionId,
    getCurrentName: () => currentName,
    onOpened: async (response) => {
      sessionId = response.session_id;
      currentName = response.structure.name;
      selectionController.clear();
      await molecularViewer.clearAllVisuals();
      persistSession(response);
      await refreshFromServer(response.structure, false);
      enableSessionControls(true);
      notifyHost("ready");
    },
    onCombined: async (response, filename) => {
      persistSession(response);
      await refreshFromServer(response.structure, false);
      log(`Combined ${filename}; conflicting chain IDs were reassigned.`);
      notifyHost("changed");
    },
    applyExamplePreset: (example) =>
      designController?.applyPreset(example.metricsTags, example.search) ?? [],
    log,
  });
  conditionController.initialize(schema);
  structureEditController.initializeBondTypes(schema.bond_types);
  viewerToolbarController.bind();
  analysisController.bind();
  annotationController.bind();
  conditionController.bind();
  sequenceController.bind();
  structureEditController.bind();
  sessionFileController.bind();
  pickScope.addEventListener("change", () => {
    selectionController.setScope(pickScope.value as SelectionScope);
  });
  element("clear-selection").addEventListener("click", () => selectionController.clear());
  for (const button of document.querySelectorAll<HTMLButtonElement>(".example-load")) {
    button.disabled = false;
  }
  const degradeToEditingOnly = (error: unknown): void => {
    applyCapabilities({});
    element("style-menu").hidden = true;
    pickScope.disabled = true;
    selectionSyntax.querySelector<HTMLOptionElement>('option[value="patinae"]')!.disabled = true;
    if (selectionSyntax.value === "patinae") selectionSyntax.value = "expression";
    log(`Renderer initialization failed; editing remains available: ${String(error)}`);
  };
  try {
    await molecularViewer.initialize();
    viewerToolbarController.refreshCapabilities();
    updateSelectionUi();
  } catch (error) {
    // Patinae (WebGPU) is the default renderer. If it is unavailable and the user did not
    // pin a renderer in the URL, fall back to Mol* (WebGL) rather than dropping to
    // editing-only, so the 3D viewer still works on browsers without WebGPU. The fallback is
    // not persisted, so a browser that later gains WebGPU support returns to Patinae.
    if (mode === "patinae" && urlPinnedViewer === null) {
      try {
        await molecularViewer.switchBackend("molstar");
        viewerModeSelect.value = "molstar";
        viewerToolbarController.refreshCapabilities();
        updateSelectionUi();
        log(`Patinae (WebGPU) unavailable; using Mol* (WebGL) instead: ${String(error)}`);
      } catch (fallbackError) {
        degradeToEditingOnly(fallbackError);
      }
    } else {
      degradeToEditingOnly(error);
    }
  }
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
        structure: designDocument.structure,
      });
      await refreshFromServer(designDocument.structure, false);
      if (designDocument.kind === "refolded") {
        const confidence = designDocument.structure.scalar_fields.find(
          (field) => field.name === "b_factor" || /plddt|confidence/i.test(field.name),
        );
        if (confidence) {
          await annotationController?.display(confidence);
          annotationController?.setDetail(`${confidence.label} colored on the refolded structure.`);
        }
      }
      enableSessionControls(true);
      notifyHost("changed");
      log(`Opened ${designDocument.kind} structure ${designDocument.label}.`);
    },
    compareDocuments: async (designDocuments) => {
      const outputColors = ["magenta", "orange", "green", "yellow"] as const;
      const structures = await Promise.all(designDocuments.map(async (designDocument, index) => {
        return {
          cif: await apiText(`/api/sessions/${designDocument.session_id}/cif`),
          name: designDocument.label,
          color: designDocument.kind === "input"
            ? "cyan" as const
            : outputColors[index % outputColors.length],
        };
      }));
      selectionController.clear();
      annotationController?.reset();
      await molecularViewer.loadCifs(structures);
      viewerToolbarController?.renderDrawers();
      log(`Comparing ${designDocuments.length} design structures in one viewer.`);
    },
    clearComparison: async () => {
      if (!lastSummary) return;
      await refreshFromServer(lastSummary, false);
      viewerToolbarController?.renderDrawers();
    },
    log,
  });
  designController.updateSession();
  const pendingExample = sessionStorage.getItem(PENDING_EXAMPLE_KEY);
  if (pendingExample) {
    sessionStorage.removeItem(PENDING_EXAMPLE_KEY);
    await sessionFileController.loadPendingExample(pendingExample);
  }
}

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  void navigator.serviceWorker.register("/sw.js");
}

void initialize().catch((error) => log(`Initialization failed: ${String(error)}`));
