import type {
  AnnotationGroup,
  AnnotationRenderResult,
  ViewerAdapter,
  ViewerAdapterContext,
  ViewerBackend,
  ViewerMode,
} from "./contract";

export type { AnnotationGroup, ViewerBackend, ViewerMode } from "./contract";

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

  constructor(
    private readonly container: HTMLElement,
    private readonly backendLabel: HTMLElement,
    private readonly mode: ViewerMode,
    private readonly onPick: (atomIndex: number, label?: string) => void,
  ) {}

  get backend(): ViewerBackend {
    if (!this.adapter) throw new Error("Molecular viewer is not initialized");
    return this.adapter.backend;
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

  async loadCif(cif: string, name: string): Promise<void> {
    this.updateAnnotationDiagnostics();
    await this.adapter?.loadCif(cif, name);
  }

  clearSelection(): void {
    this.adapter?.clearSelection();
  }

  async showAnnotation(groups: AnnotationGroup[]): Promise<void> {
    if (!this.adapter) throw new Error("Molecular viewer is not initialized");
    this.updateAnnotationDiagnostics(await this.adapter.showAnnotation(groups));
  }

  async clearAnnotation(): Promise<void> {
    await this.adapter?.clearAnnotation();
    this.updateAnnotationDiagnostics();
  }

  destroy(): void {
    this.adapter?.destroy();
    this.adapter = null;
    this.container.replaceChildren();
    delete this.container.dataset.backend;
    delete this.container.dataset.picking;
  }
}
