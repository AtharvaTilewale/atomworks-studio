export type ViewerBackend = "patinae" | "molstar";
export type ViewerMode = ViewerBackend;
export type ViewerTarget = "all" | "selection";
export type ViewerRepresentation =
  | "cartoon"
  | "ribbon"
  | "lines"
  | "sticks"
  | "spheres"
  | "surface";
export type ViewerColor =
  | "atomic"
  | "chain"
  | "ss"
  | "residue"
  | "rainbow"
  | "yellow"
  | "cyan"
  | "magenta"
  | "orange"
  | "green"
  | "red"
  | "blue";
export type ViewerCameraAction = "reset" | "zoom" | "center" | "orient";

export type ViewerCapabilities = {
  representations: boolean;
  colors: boolean;
  background: boolean;
  commands: boolean;
  picking: boolean;
  softwarePicking: boolean;
  selectionExpressions: boolean;
  selectionList: boolean;
  commandCatalog: boolean;
  measurements: boolean;
  interactionOverlays: boolean;
};

export type ViewerSelection = {
  name: string;
  expression?: string;
  atomIndices?: number[];
};

export type ViewerObject = {
  name: string;
  type?: string;
  atomCount?: number;
  enabled?: boolean;
};

export type ViewerCommand = {
  name: string;
  aliases?: string[];
  description?: string;
  usage?: string;
  help?: string;
  webSafe?: boolean;
  unavailableReason?: string | null;
};

export type ViewerMeasurement = {
  id?: string;
  label?: string;
  kind?: string;
  value?: number;
  unit?: string;
  atomIndices?: number[];
};

export type ViewerMeasurementRequest = {
  kind: "distance" | "angle" | "dihedral";
  atomIndices: number[];
  label?: string;
  color?: number;
};

export type ViewerInteraction = {
  id: string;
  type: string;
  atom1: number;
  atom2: number;
  label: string;
  color: number;
};

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
  readonly capabilities: ViewerCapabilities;
  initialize(): Promise<void>;
  loadCif(cif: string, name: string): Promise<void>;
  showAnnotation(groups: AnnotationGroup[]): Promise<AnnotationRenderResult>;
  clearAnnotation(): Promise<void>;
  setSelection(atomIndices: number[]): void;
  clearSelection(): void;
  setRepresentation?(representation: ViewerRepresentation, target: ViewerTarget, exclusive: boolean): void;
  setColor?(color: ViewerColor, target: ViewerTarget): void;
  setBackground?(color: "black" | "white"): void;
  camera?(action: ViewerCameraAction): void;
  executeCommand?(command: string): unknown;
  evaluateSelection?(expression: string): number[];
  getObjectList?(): ViewerObject[];
  setObjectEnabled?(name: string, enabled: boolean): void;
  getSelectionList?(): ViewerSelection[];
  getCommandCatalog?(): ViewerCommand[];
  listMeasurements?(): ViewerMeasurement[];
  addMeasurement?(request: ViewerMeasurementRequest): Promise<ViewerMeasurement[]>;
  showInteractions?(interactions: ViewerInteraction[]): Promise<void>;
  clearMeasurements?(): Promise<void>;
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
