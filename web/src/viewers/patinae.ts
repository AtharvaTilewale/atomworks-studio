import { PatinaeViewer } from "@patinae/viewer";

import {
  ANNOTATION_PALETTE,
  annotationRenderResult,
  normalizedAnnotationGroups,
  type AnnotationGroup,
  type AnnotationRenderResult,
  type ViewerAdapter,
  type ViewerAdapterContext,
  type ViewerCameraAction,
  type ViewerColor,
  type ViewerCommand,
  type ViewerMeasurement,
  type ViewerMeasurementRequest,
  type ViewerInteraction,
  type ViewerObject,
  type ViewerRepresentation,
  type ViewerSelection,
  type ViewerTarget,
} from "./contract";

type FuturePatinaeViewer = PatinaeViewer & {
  evaluateSelection?: (expression: string) => {
    atom_count: number;
    objects: Array<{ object_name: string; atom_indices: number[] }>;
  };
  getObjectList?: () => Array<{
    name: string;
    object_type: string;
    atom_count: number;
    enabled: boolean;
  }>;
  getSelectionList?: () => ViewerSelection[];
  getCommandCatalog?: () => Array<{
    name: string;
    aliases: string[];
    description: string;
    usage: string;
    help: string;
    web_safe: boolean;
    unavailable_reason: string | null;
  }>;
  getMeasurementList?: () => Array<{
    name: string;
    measurements: Array<{ kind: string; value: number; label: string }>;
  }>;
  addDistanceMeasurements?: (requests: Array<{
    name: string;
    selection1: string;
    selection2: string;
  }>) => Array<{
    name: string;
    measurements: Array<{ kind: string; value: number; label: string }>;
  }>;
  addMeasurements?: (requests: Array<{
    kind: "distance" | "angle" | "dihedral";
    name: string;
    selections: string[];
    color?: [number, number, number, number];
    label?: string;
  }>) => Array<{
    name: string;
    measurements: Array<{ kind: string; value: number; label: string }>;
  }>;
  removeMeasurements?: (names: string[]) => number;
};

function flattenMeasurements(
  groups: Array<{
    name: string;
    measurements: Array<{ kind: string; value: number; label: string }>;
  }>,
): ViewerMeasurement[] {
  return groups.flatMap((group) => group.measurements.map((measurement) => ({
    id: group.name,
    label: measurement.label || group.name,
    kind: measurement.kind,
    value: measurement.value,
    unit: measurement.kind === "distance" ? " Å" : "°",
  })));
}

function rgba(color: number): [number, number, number, number] {
  return [
    ((color >> 16) & 0xff) / 255,
    ((color >> 8) & 0xff) / 255,
    (color & 0xff) / 255,
    1,
  ];
}

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
  readonly capabilities = {
    representations: true,
    colors: true,
    background: true,
    commands: true,
    picking: false,
    softwarePicking: false,
    selectionExpressions: false,
    selectionList: false,
    commandCatalog: false,
    measurements: false,
    interactionOverlays: false,
  };
  private viewer: FuturePatinaeViewer | null = null;
  private cif: string | null = null;
  private name = "structure";
  private displayCommands = [
    "hide everything",
    "show cartoon",
    "show sticks, not polymer",
  ];
  private colorCommands: string[] = [];
  private backgroundCommand = "bg_color black";

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
    this.capabilities.picking = picking;
    this.capabilities.softwarePicking = !picking;
    this.context.container.dataset.picking = picking ? "enabled" : "software-disabled";
    this.viewer = new PatinaeViewer(this.context.container, {
      picking,
      selectionOverlay: picking,
      memoryProfile: "auto",
    });
    await this.viewer.init();
    this.capabilities.selectionExpressions = typeof this.viewer.evaluateSelection === "function";
    this.capabilities.selectionList = typeof this.viewer.getSelectionList === "function";
    this.capabilities.commandCatalog = typeof this.viewer.getCommandCatalog === "function";
    this.capabilities.measurements =
      typeof this.viewer.getMeasurementList === "function"
      && (typeof this.viewer.addMeasurements === "function"
        || typeof this.viewer.addDistanceMeasurements === "function");
    this.capabilities.interactionOverlays = this.capabilities.measurements;
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

  private parseCoordinateConditionedAtomIds(): number[] {
    if (this.cif === null) return [];
    const lines = this.cif.split("\n");
    const result: number[] = [];
    let colIdx = 0;
    let idCol = -1;
    let maskCoordCol = -1;
    let inAtomSiteLoop = false;
    let inData = false;

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line === "loop_") {
        colIdx = 0;
        idCol = -1;
        maskCoordCol = -1;
        inAtomSiteLoop = false;
        inData = false;
        continue;
      }
      if (line.trim().startsWith("_atom_site.")) {
        inAtomSiteLoop = true;
        inData = false;
        const col = line.trim();
        if (col === "_atom_site.id") idCol = colIdx;
        if (col === "_atom_site.mask_coordinate_1_atom") maskCoordCol = colIdx;
        colIdx++;
        continue;
      }
      if (!inAtomSiteLoop) continue;
      const trimmed = line.trim();
      if (!inData) {
        if (trimmed === "" || trimmed.startsWith("#")) continue;
        if (trimmed.startsWith("_")) { inAtomSiteLoop = false; continue; }
        inData = true;
      }
      // In data rows
      if (trimmed === "" || trimmed.startsWith("#") || trimmed.startsWith("_")) {
        inAtomSiteLoop = false;
        inData = false;
        continue;
      }
      if (idCol < 0 || maskCoordCol < 0) continue;
      const parts = trimmed.split(/\s+/);
      if (maskCoordCol < parts.length && parts[maskCoordCol] === "True") {
        const id = parseInt(parts[idCol], 10);
        if (!isNaN(id)) result.push(id);
      }
    }
    return result;
  }

  private async renderCif(): Promise<void> {
    if (!this.viewer || this.cif === null) throw new Error("Patinae has no loaded structure");
    for (const objectName of this.viewer.getObjectNames()) this.viewer.execute(`delete ${objectName}`);
    this.viewer.loadData(new TextEncoder().encode(this.cif), this.name, "cif");
    for (const command of this.displayCommands) this.viewer.execute(command);
    for (const command of this.colorCommands) this.viewer.execute(command);
    this.viewer.execute(this.backgroundCommand);
    const coordConditionedIds = this.parseCoordinateConditionedAtomIds();
    if (coordConditionedIds.length) {
      this.viewer.execute(`show sticks, index ${compressIndices(coordConditionedIds)}`);
    }
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

  setSelection(atomIndices: number[]): void {
    if (!this.viewer) return;
    const indices = [...new Set(atomIndices)].sort((left, right) => left - right);
    this.viewer.execute(indices.length
      ? `select sele, index ${compressIndices(indices)}`
      : "select sele, none");
  }

  clearSelection(): void {
    this.setSelection([]);
  }

  setRepresentation(
    representation: ViewerRepresentation,
    target: ViewerTarget,
    exclusive: boolean,
  ): void {
    if (!this.viewer) return;
    const selection = target === "selection" ? "sele" : "all";
    const command = exclusive
      ? `as ${representation}, ${selection}`
      : `show ${representation}, ${selection}`;
    if (exclusive && target === "all") this.displayCommands = [command];
    else this.displayCommands.push(command);
    this.viewer.execute(command);
  }

  setColor(color: ViewerColor, target: ViewerTarget): void {
    if (!this.viewer) return;
    const command = `color ${color}, ${target === "selection" ? "sele" : "all"}`;
    this.colorCommands.push(command);
    this.viewer.execute(command);
  }

  setBackground(color: "black" | "white"): void {
    if (!this.viewer) return;
    this.backgroundCommand = `bg_color ${color}`;
    this.viewer.execute(this.backgroundCommand);
  }

  camera(action: ViewerCameraAction): void {
    if (!this.viewer) return;
    const command = action === "zoom" ? "zoom all" : action;
    this.viewer.execute(command);
  }

  executeCommand(command: string): unknown {
    if (!this.viewer) throw new Error("Patinae is not initialized");
    return this.viewer.execute(command);
  }

  evaluateSelection(expression: string): number[] {
    if (!this.viewer?.evaluateSelection) throw new Error("This Patinae build cannot evaluate selections.");
    const result = this.viewer.evaluateSelection(expression);
    return result.objects.flatMap((object) => object.atom_indices);
  }

  getObjectList(): ViewerObject[] {
    return this.viewer?.getObjectList?.().map((object) => ({
      name: object.name,
      type: object.object_type,
      atomCount: object.atom_count,
      enabled: object.enabled,
    })) ?? [];
  }

  setObjectEnabled(name: string, enabled: boolean): void {
    if (!this.viewer) return;
    this.viewer.execute(`${enabled ? "enable" : "disable"} ${name}`);
  }

  getSelectionList(): ViewerSelection[] {
    return this.viewer?.getSelectionList?.() ?? [];
  }

  getCommandCatalog(): ViewerCommand[] {
    return this.viewer?.getCommandCatalog?.().map((command) => ({
      name: command.name,
      aliases: command.aliases,
      description: command.description,
      usage: command.usage,
      help: command.help,
      webSafe: command.web_safe,
      unavailableReason: command.unavailable_reason,
    })) ?? [];
  }

  listMeasurements(): ViewerMeasurement[] {
    return flattenMeasurements(this.viewer?.getMeasurementList?.() ?? []);
  }

  async addMeasurement(request: ViewerMeasurementRequest): Promise<ViewerMeasurement[]> {
    if (!this.viewer?.addMeasurements && !this.viewer?.addDistanceMeasurements) {
      throw new Error("This Patinae build does not support measurements.");
    }
    const required = request.kind === "distance" ? 2 : request.kind === "angle" ? 3 : 4;
    if (request.atomIndices.length !== required) {
      throw new Error(`Select exactly ${required} atoms for a ${request.kind} measurement.`);
    }
    const name = request.label?.replace(/[^A-Za-z0-9_-]/g, "_") || `${request.kind}_${Date.now()}`;
    if (this.viewer.addMeasurements) {
      return flattenMeasurements(this.viewer.addMeasurements([{
        kind: request.kind,
        name,
        selections: request.atomIndices.map((index) => `index ${index}`),
        color: rgba(request.color ?? 0xffd54f),
        label: request.label,
      }]));
    }
    if (request.kind !== "distance") throw new Error(`This Patinae build cannot create ${request.kind} measurements.`);
    return flattenMeasurements(this.viewer.addDistanceMeasurements!([{
        name,
        selection1: `index ${request.atomIndices[0]}`,
        selection2: `index ${request.atomIndices[1]}`,
      }]));
  }

  async clearMeasurements(): Promise<void> {
    const names = this.viewer?.getMeasurementList?.().map((measurement) => measurement.name) ?? [];
    if (names.length) this.viewer?.removeMeasurements?.(names);
  }

  async showInteractions(interactions: ViewerInteraction[]): Promise<void> {
    if (!this.viewer?.addMeasurements && !this.viewer?.addDistanceMeasurements) {
      throw new Error("This Patinae build does not support interaction overlays.");
    }
    await this.clearMeasurements();
    if (this.viewer.addMeasurements) {
      this.viewer.addMeasurements(interactions.map((interaction) => ({
        kind: "distance",
        name: interaction.id,
        selections: [`index ${interaction.atom1}`, `index ${interaction.atom2}`],
        color: rgba(interaction.color),
        label: interaction.label,
      })));
    } else {
      this.viewer.addDistanceMeasurements!(interactions.map((interaction) => ({
        name: interaction.id,
        selection1: `index ${interaction.atom1}`,
        selection2: `index ${interaction.atom2}`,
      })));
    }
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
