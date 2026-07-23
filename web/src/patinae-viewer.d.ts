declare module "@patinae/viewer" {
  export type PickHit = {
    object_name: string | null;
    atom_index: number | null;
    chain: string | null;
    residue: number | null;
    expression: string | null;
  };

  export type ViewerOptions = {
    picking?: boolean;
    selectionOverlay?: boolean;
    memoryProfile?: "auto" | "performance" | "balanced" | "lite" | `manual:${number}`;
  };

  export class PatinaeViewer {
    constructor(container: HTMLElement, options?: ViewerOptions);
    init(): Promise<void>;
    execute(command: string): unknown;
    loadData(data: Uint8Array, name: string, format: string): void;
    countAtoms(selection?: string): number;
    getObjectNames(): string[];
    on(event: "atom-picked", callback: (hit: PickHit) => void): void;
    destroy(): void;
  }
}
