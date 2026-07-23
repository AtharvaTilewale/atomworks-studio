import { PatinaeViewer } from "@patinae/viewer";

import {
  ANNOTATION_PALETTE,
  annotationRenderResult,
  normalizedAnnotationGroups,
  type AnnotationGroup,
  type AnnotationRenderResult,
  type ViewerAdapter,
  type ViewerAdapterContext,
} from "./contract";

function compressIndices(indices: number[]): string {
  const ranges: string[] = [];
  for (let start = 0; start < indices.length;) {
    let stop = start;
    while (stop + 1 < indices.length && indices[stop + 1] === indices[stop] + 1) stop += 1;
    ranges.push(stop === start ? String(indices[start]) : `${indices[start]}-${indices[stop]}`);
    start = stop + 1;
  }
  return ranges.join("+");
}

export class PatinaeAdapter implements ViewerAdapter {
  readonly backend = "patinae" as const;
  private viewer: PatinaeViewer | null = null;
  private cif: string | null = null;
  private name = "structure";

  constructor(private readonly context: ViewerAdapterContext) {}

  private async pickingSupported(): Promise<boolean> {
    const gpu = (
      navigator as Navigator & {
        gpu?: {
          requestAdapter(): Promise<{
            isFallbackAdapter?: boolean;
            info?: { isFallbackAdapter?: boolean };
          } | null>;
        };
      }
    ).gpu;
    const adapter = await gpu?.requestAdapter();
    return Boolean(adapter && !(adapter.isFallbackAdapter ?? adapter.info?.isFallbackAdapter));
  }

  async initialize(): Promise<void> {
    const picking = await this.pickingSupported();
    this.context.container.dataset.picking = picking ? "enabled" : "software-disabled";
    this.viewer = new PatinaeViewer(this.context.container, {
      picking,
      selectionOverlay: picking,
      memoryProfile: "auto",
    });
    await this.viewer.init();
    if (picking) {
      this.viewer.on("atom-picked", (hit) => {
        if (hit.atom_index === null) return;
        this.context.onPick({
          atomIndex: hit.atom_index,
          label: `${hit.chain ?? "?"}/${hit.residue ?? "?"}: atom ${hit.atom_index}`,
        });
      });
    }
  }

  private async renderCif(): Promise<void> {
    if (!this.viewer || this.cif === null) throw new Error("Patinae has no loaded structure");
    for (const objectName of this.viewer.getObjectNames()) this.viewer.execute(`delete ${objectName}`);
    this.viewer.loadData(new TextEncoder().encode(this.cif), this.name, "cif");
    this.viewer.execute("hide everything");
    this.viewer.execute("show cartoon");
    this.viewer.execute("show sticks, not polymer");
    this.viewer.execute("zoom all");
    await new Promise((resolve) => setTimeout(resolve, 250));
    this.viewer.getObjectNames(); // Surface deferred WebGPU/render errors.
  }

  async loadCif(cif: string, name: string): Promise<void> {
    this.cif = cif;
    this.name = name;
    await this.renderCif();
  }

  async showAnnotation(groups: AnnotationGroup[]): Promise<AnnotationRenderResult> {
    if (!this.viewer) throw new Error("Patinae is not initialized");
    const normalized = normalizedAnnotationGroups(groups);
    await this.renderCif();
    normalized.forEach((group, index) => {
      const selection = `index ${compressIndices(group.atomIndices)}`;
      const selectedCount = this.viewer!.countAtoms(selection);
      if (selectedCount !== group.atomIndices.length) {
        throw new Error(
          `Patinae selected ${selectedCount} atoms for ${group.label}; ` +
            `AtomWorks reported ${group.atomIndices.length}.`,
        );
      }
      const color = group.color ?? ANNOTATION_PALETTE[index % ANNOTATION_PALETTE.length];
      this.viewer!.execute(`color ${color.name}, ${selection}`);
      this.viewer!.execute(`show sticks, ${selection}`);
    });
    return annotationRenderResult(normalized);
  }

  async clearAnnotation(): Promise<void> {
    if (this.cif !== null) await this.renderCif();
    else this.clearSelection();
  }

  clearSelection(): void {
    this.viewer?.execute("select sele, none");
  }

  destroy(): void {
    this.viewer?.destroy();
    this.viewer = null;
    this.cif = null;
  }
}

export function createPatinaeAdapter(context: ViewerAdapterContext): ViewerAdapter {
  return new PatinaeAdapter(context);
}
