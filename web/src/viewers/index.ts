import type {
  AnnotationGroup,
  AnnotationRenderResult,
  ViewerAdapter,
  ViewerAdapterContext,
  ViewerBackend,
  ViewerCameraAction,
  ViewerColor,
  ViewerMode,
  ViewerObject,
  ViewerRepresentation,
  ViewerCommand,
  ViewerMeasurement,
  ViewerMeasurementRequest,
  ViewerInteraction,
  ViewerSelection,
  ViewerTarget,
} from "./contract";

export type {
  AnnotationGroup,
  ViewerBackend,
  ViewerCameraAction,
  ViewerColor,
  ViewerMode,
  ViewerObject,
  ViewerRepresentation,
  ViewerCommand,
  ViewerMeasurement,
  ViewerMeasurementRequest,
  ViewerInteraction,
  ViewerSelection,
  ViewerTarget,
} from "./contract";

const BACKEND_LABELS: Record<ViewerBackend, string> = {
  molstar: "Mol* · WebGL",
  patinae: "Patinae · WebGPU",
};

async function createAdapter(
  backend: ViewerBackend,
  context: ViewerAdapterContext,
): Promise<ViewerAdapter> {
  if (backend === "molstar") {
    const { createMolstarAdapter } = await import("./molstar");
    return createMolstarAdapter(context);
  }
  const { createPatinaeAdapter } = await import("./patinae");
  return createPatinaeAdapter(context);
}

export class MolecularViewer {
  private adapter: ViewerAdapter | null = null;
  private cif: string | null = null;
  private name = "structure";
  private selectedAtomIndices: number[] = [];
  private annotationGroups: AnnotationGroup[] = [];
  private interactionOverlays: ViewerInteraction[] = [];
  private measurementRequests: ViewerMeasurementRequest[] = [];

  constructor(
    private readonly container: HTMLElement,
    private readonly backendLabel: HTMLElement,
    private mode: ViewerMode,
    private readonly onPick: (atomIndex: number, label?: string) => void,
  ) {}

  get backend(): ViewerBackend {
    if (!this.adapter) throw new Error("Molecular viewer is not initialized");
    return this.adapter.backend;
  }

  get capabilities() {
    if (!this.adapter) throw new Error("Molecular viewer is not initialized");
    return this.adapter.capabilities;
  }

  private updateAnnotationDiagnostics(result?: AnnotationRenderResult): void {
    this.container.dataset.annotationAtoms = String(result?.atomCount ?? 0);
    this.container.dataset.annotationGroups = String(result?.groupCount ?? 0);
  }

  private async activate(backend: ViewerBackend): Promise<void> {
    try {
      this.adapter?.destroy();
    } catch {
      // A failed GPU context can reject cleanup.
    }
    this.adapter = null;
    this.container.replaceChildren();
    this.updateAnnotationDiagnostics();
    const adapter = await createAdapter(backend, {
      container: this.container,
      onPick: ({ atomIndex, label }) => this.onPick(atomIndex, label),
    });
    try {
      await adapter.initialize();
    } catch (error) {
      try {
        adapter.destroy();
      } catch {
        // Preserve the initialization error.
      }
      throw error;
    }
    this.adapter = adapter;
    this.container.dataset.backend = backend;
    const softwarePicking =
      backend === "patinae" && this.container.dataset.picking === "software-disabled";
    this.backendLabel.textContent =
      BACKEND_LABELS[backend] + (softwarePicking ? " · software WebGPU (canvas picking off)" : "");
  }

  async initialize(): Promise<void> {
    await this.activate(this.mode);
  }

  async switchBackend(mode: ViewerMode): Promise<void> {
    if (this.adapter?.backend === mode) return;
    this.mode = mode;
    await this.activate(mode);
    if (this.cif !== null) {
      await this.adapter?.loadCif(this.cif, this.name);
      if (this.annotationGroups.length) {
        this.updateAnnotationDiagnostics(await this.adapter?.showAnnotation(this.annotationGroups));
      }
      if (this.interactionOverlays.length) {
        await this.adapter?.showInteractions?.(this.interactionOverlays);
      }
      for (const request of this.measurementRequests) {
        await this.adapter?.addMeasurement?.(request);
      }
      this.adapter?.setSelection(this.selectedAtomIndices);
    }
  }

  async loadCif(cif: string, name: string): Promise<void> {
    this.cif = cif;
    this.name = name;
    this.updateAnnotationDiagnostics();
    await this.adapter?.loadCif(cif, name);
  }

  clearSelection(): void {
    this.selectedAtomIndices = [];
    this.adapter?.clearSelection();
  }

  setSelection(atomIndices: number[]): void {
    this.selectedAtomIndices = [...new Set(atomIndices)];
    this.adapter?.setSelection(this.selectedAtomIndices);
  }

  setRepresentation(
    representation: ViewerRepresentation,
    target: ViewerTarget,
    exclusive = false,
  ): void {
    if (!this.adapter?.setRepresentation) throw new Error("Representation controls are unavailable.");
    this.adapter.setRepresentation(representation, target, exclusive);
  }

  setColor(color: ViewerColor, target: ViewerTarget): void {
    if (!this.adapter?.setColor) throw new Error("Color controls are unavailable.");
    this.adapter.setColor(color, target);
  }

  setBackground(color: "black" | "white"): void {
    if (!this.adapter?.setBackground) throw new Error("Background controls are unavailable.");
    this.adapter.setBackground(color);
  }

  camera(action: ViewerCameraAction): void {
    if (!this.adapter?.camera) throw new Error("Camera controls are unavailable.");
    this.adapter.camera(action);
  }

  executeCommand(command: string): unknown {
    if (!this.adapter?.executeCommand) {
      throw new Error("Viewer commands are available only with the Patinae renderer.");
    }
    return this.adapter.executeCommand(command);
  }

  evaluateSelection(expression: string): number[] {
    if (!this.adapter?.evaluateSelection) throw new Error("Renderer selection evaluation is unavailable.");
    return this.adapter.evaluateSelection(expression);
  }

  getObjectList(): ViewerObject[] {
    return this.adapter?.getObjectList?.() ?? [];
  }

  setObjectEnabled(name: string, enabled: boolean): void {
    if (!this.adapter?.setObjectEnabled) throw new Error("Object visibility is unavailable.");
    this.adapter.setObjectEnabled(name, enabled);
  }

  getSelectionList(): ViewerSelection[] {
    return this.adapter?.getSelectionList?.() ?? [];
  }

  getCommandCatalog(): ViewerCommand[] {
    return this.adapter?.getCommandCatalog?.() ?? [];
  }

  listMeasurements(): ViewerMeasurement[] {
    return this.adapter?.listMeasurements?.() ?? [];
  }

  async addMeasurement(request: ViewerMeasurementRequest): Promise<ViewerMeasurement[]> {
    if (!this.adapter?.addMeasurement) throw new Error("Renderer measurements are unavailable.");
    const measurements = await this.adapter.addMeasurement(request);
    this.measurementRequests.push(request);
    return measurements;
  }

  async showInteractions(interactions: ViewerInteraction[]): Promise<void> {
    if (!this.adapter?.showInteractions) throw new Error("Renderer interaction overlays are unavailable.");
    this.interactionOverlays = interactions;
    await this.adapter.showInteractions(interactions);
    for (const request of this.measurementRequests) await this.adapter.addMeasurement?.(request);
  }

  async clearMeasurements(): Promise<void> {
    this.interactionOverlays = [];
    this.measurementRequests = [];
    await this.adapter?.clearMeasurements?.();
  }

  async showAnnotation(groups: AnnotationGroup[]): Promise<void> {
    if (!this.adapter) throw new Error("Molecular viewer is not initialized");
    this.annotationGroups = groups;
    this.updateAnnotationDiagnostics(await this.adapter.showAnnotation(groups));
  }

  async clearAnnotation(): Promise<void> {
    this.annotationGroups = [];
    await this.adapter?.clearAnnotation();
    this.updateAnnotationDiagnostics();
  }

  destroy(): void {
    this.adapter?.destroy();
    this.adapter = null;
    this.container.replaceChildren();
    delete this.container.dataset.backend;
    delete this.container.dataset.picking;
    this.cif = null;
    this.selectedAtomIndices = [];
    this.annotationGroups = [];
    this.interactionOverlays = [];
    this.measurementRequests = [];
  }
}
