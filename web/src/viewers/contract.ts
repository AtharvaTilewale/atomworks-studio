export type ViewerBackend = "patinae" | "molstar";
export type ViewerMode = ViewerBackend;

export type AnnotationGroup = {
  label: string;
  atomIndices: number[];
  color?: AnnotationColor;
};

export type AnnotationColor = {
  name: string;
  value: number;
};

export type AnnotationRenderResult = {
  atomCount: number;
  groupCount: number;
};

export type AtomPick = {
  atomIndex: number;
  label?: string;
};

export type ViewerAdapterContext = {
  container: HTMLElement;
  onPick: (pick: AtomPick) => void;
};

export interface ViewerAdapter {
  readonly backend: ViewerBackend;
  initialize(): Promise<void>;
  loadCif(cif: string, name: string): Promise<void>;
  showAnnotation(groups: AnnotationGroup[]): Promise<AnnotationRenderResult>;
  clearAnnotation(): Promise<void>;
  clearSelection(): void;
  destroy(): void;
}

export const ANNOTATION_PALETTE: readonly AnnotationColor[] = [
  { name: "yellow", value: 0xffd54f },
  { name: "cyan", value: 0x4dd0e1 },
  { name: "magenta", value: 0xce93d8 },
  { name: "orange", value: 0xffa726 },
  { name: "green", value: 0x66bb6a },
  { name: "red", value: 0xef5350 },
  { name: "blue", value: 0x42a5f5 },
];

export function normalizedAnnotationGroups(groups: AnnotationGroup[]): AnnotationGroup[] {
  return groups.flatMap((group) => {
    const atomIndices = [...new Set(group.atomIndices)].sort((left, right) => left - right);
    return atomIndices.length ? [{ ...group, atomIndices }] : [];
  });
}

export function annotationRenderResult(groups: AnnotationGroup[]): AnnotationRenderResult {
  return {
    atomCount: new Set(groups.flatMap((group) => group.atomIndices)).size,
    groupCount: groups.length,
  };
}
