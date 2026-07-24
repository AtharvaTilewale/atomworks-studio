import { Viewer as MolstarViewer } from "molstar/lib/apps/viewer/app";
import { OrderedSet } from "molstar/lib/mol-data/int";
import {
  StructureElement,
  StructureProperties,
} from "molstar/lib/mol-model/structure/structure";
import {
  clearStructureOverpaint,
  setStructureOverpaint,
} from "molstar/lib/mol-plugin-state/helpers/structure-overpaint";
import { Color } from "molstar/lib/mol-util/color";
import "molstar/lib/mol-plugin-ui/skin/dark.scss";

import {
  ANNOTATION_PALETTE,
  annotationRenderResult,
  normalizedAnnotationGroups,
  type AnnotationGroup,
  type AnnotationRenderResult,
  type ViewerAdapter,
  type ViewerAdapterContext,
  type ViewerCameraAction,
  type ViewerInteraction,
  type ViewerMeasurement,
  type ViewerMeasurementRequest,
} from "./contract";

export class MolstarAdapter implements ViewerAdapter {
  readonly backend = "molstar" as const;
  readonly capabilities = {
    representations: false,
    colors: false,
    background: true,
    commands: false,
    picking: true,
    softwarePicking: false,
    selectionExpressions: false,
    selectionList: false,
    commandCatalog: false,
    measurements: true,
    interactionOverlays: true,
  } as const;
  private viewer: MolstarViewer | null = null;
  private selectedAtomIndices: number[] = [];
  private measurements: ViewerMeasurement[] = [];

  constructor(private readonly context: ViewerAdapterContext) {}

  async initialize(): Promise<void> {
    this.viewer = await MolstarViewer.create(this.context.container, {
      extensions: [],
      layoutIsExpanded: false,
      layoutShowControls: false,
      layoutShowSequence: false,
      layoutShowLog: false,
      layoutShowLeftPanel: false,
      viewportShowControls: true,
      viewportShowSelectionMode: false,
      viewportBackgroundColor: "#02080d",
      preferWebgl1: false,
    });
    this.viewer.plugin.managers.interactivity.setProps({ granularity: "element" });
    this.viewer.subscribe(this.viewer.plugin.behaviors.interaction.click, ({ current }) => {
      if (!StructureElement.Loci.is(current.loci) || current.loci.elements.length === 0) return;
      const group = current.loci.elements[0];
      if (OrderedSet.size(group.indices) === 0) return;
      const elementIndex = group.unit.elements[OrderedSet.getAt(group.indices, 0)];
      const location = StructureElement.Location.create(current.loci.structure, group.unit, elementIndex);
      const atomIndex = StructureProperties.atom.sourceIndex(location);
      this.context.onPick({
        atomIndex,
        label: [
          StructureProperties.chain.auth_asym_id(location),
          StructureProperties.residue.auth_seq_id(location),
          StructureProperties.atom.label_atom_id(location),
        ].join("/"),
      });
    });
  }

  async loadCif(cif: string, name: string): Promise<void> {
    if (!this.viewer) throw new Error("Mol* is not initialized");
    await this.viewer.plugin.clear();
    await this.viewer.loadStructureFromData(cif, "mmcif", { dataLabel: name });
  }

  private currentStructure() {
    if (!this.viewer) throw new Error("Mol* is not initialized");
    const reference = this.viewer.plugin.managers.structure.hierarchy.current.structures[0];
    const structure = reference?.cell.obj?.data;
    if (!reference || !structure) throw new Error("Mol* has no loaded structure");
    return { reference, structure };
  }

  private lociFor(atomIndices: number[]) {
    const { structure } = this.currentStructure();
    return StructureElement.Schema.toLoci(structure, { items: { atom_index: atomIndices } });
  }

  async showAnnotation(groups: AnnotationGroup[]): Promise<AnnotationRenderResult> {
    if (!this.viewer) throw new Error("Mol* is not initialized");
    const normalized = normalizedAnnotationGroups(groups);
    const { reference } = this.currentStructure();
    await clearStructureOverpaint(this.viewer.plugin, reference.components);
    if (normalized.length === 0) {
      this.clearSelection();
      return annotationRenderResult(normalized);
    }

    try {
      for (const [index, group] of normalized.entries()) {
        const loci = this.lociFor(group.atomIndices);
        const selectedCount = StructureElement.Loci.size(loci);
        if (selectedCount !== group.atomIndices.length) {
          throw new Error(
            `Mol* selected ${selectedCount} atoms for ${group.label}; ` +
              `AtomWorks reported ${group.atomIndices.length}.`,
          );
        }
        const color = group.color ?? ANNOTATION_PALETTE[index % ANNOTATION_PALETTE.length];
        await setStructureOverpaint(
          this.viewer.plugin,
          reference.components,
          Color(color.value),
          async (structure) =>
            StructureElement.Schema.toLoci(structure, {
              items: { atom_index: group.atomIndices },
            }),
        );
      }
    } catch (error) {
      await clearStructureOverpaint(this.viewer.plugin, reference.components);
      throw error;
    }

    const result = annotationRenderResult(normalized);
    const loci = this.lociFor([...new Set(normalized.flatMap((group) => group.atomIndices))]);
    if (StructureElement.Loci.size(loci) !== result.atomCount) {
      throw new Error("Mol* annotation union does not match AtomWorks atom indices");
    }
    this.viewer.plugin.managers.interactivity.lociSelects.select({ loci });
    this.viewer.plugin.managers.camera.focusLoci(loci);
    return result;
  }

  async clearAnnotation(): Promise<void> {
    if (!this.viewer) return;
    const reference = this.viewer.plugin.managers.structure.hierarchy.current.structures[0];
    if (reference) await clearStructureOverpaint(this.viewer.plugin, reference.components);
  }

  setSelection(atomIndices: number[]): void {
    if (!this.viewer) return;
    this.clearSelection();
    if (!atomIndices.length) return;
    this.selectedAtomIndices = [...new Set(atomIndices)];
    const loci = this.lociFor(this.selectedAtomIndices);
    this.viewer.plugin.managers.interactivity.lociSelects.select({ loci });
  }

  clearSelection(): void {
    if (!this.viewer) return;
    this.viewer.plugin.managers.interactivity.lociSelects.deselectAll();
    this.viewer.plugin.managers.interactivity.lociHighlights.clearHighlights();
    this.viewer.plugin.managers.structure.focus.clear();
    this.selectedAtomIndices = [];
  }

  setBackground(color: "black" | "white"): void {
    this.viewer?.plugin.canvas3d?.setProps({
      renderer: { backgroundColor: Color(color === "black" ? 0x000000 : 0xffffff) },
    });
  }

  camera(action: ViewerCameraAction): void {
    if (!this.viewer) return;
    if (action === "center" && this.selectedAtomIndices.length) {
      this.viewer.plugin.managers.camera.focusLoci(this.lociFor(this.selectedAtomIndices));
      return;
    }
    this.viewer.plugin.managers.camera.reset();
  }

  listMeasurements(): ViewerMeasurement[] {
    return this.measurements;
  }

  async addMeasurement(request: ViewerMeasurementRequest): Promise<ViewerMeasurement[]> {
    if (!this.viewer) throw new Error("Mol* is not initialized");
    const expected = request.kind === "distance" ? 2 : request.kind === "angle" ? 3 : 4;
    if (request.atomIndices.length !== expected) {
      throw new Error(`Select exactly ${expected} atoms for a ${request.kind} measurement.`);
    }
    const loci = request.atomIndices.map((atomIndex) => this.lociFor([atomIndex]));
    const color = Color(request.color ?? 0xffd54f);
    const options = {
      customText: request.label,
      visualParams: { linesColor: color, textColor: color },
    };
    const manager = this.viewer.plugin.managers.structure.measurement;
    if (request.kind === "distance") await manager.addDistance(loci[0], loci[1], options);
    else if (request.kind === "angle") await manager.addAngle(loci[0], loci[1], loci[2], options);
    else await manager.addDihedral(loci[0], loci[1], loci[2], loci[3], options);
    const measurement: ViewerMeasurement = {
      id: `${request.kind}_${Date.now()}`,
      label: request.label ?? request.kind,
      kind: request.kind,
      atomIndices: request.atomIndices,
    };
    this.measurements.push(measurement);
    return [measurement];
  }

  async clearMeasurements(): Promise<void> {
    if (!this.viewer) return;
    const state = this.viewer.plugin.managers.structure.measurement.state;
    const cells = [
      ...state.labels,
      ...state.distances,
      ...state.angles,
      ...state.dihedrals,
      ...state.orientations,
      ...state.planes,
    ];
    if (cells.length) {
      const update = this.viewer.plugin.state.data.build();
      for (const cell of cells) update.delete(cell.transform.ref);
      await update.commit();
    }
    this.measurements = [];
  }

  async showInteractions(interactions: ViewerInteraction[]): Promise<void> {
    await this.clearMeasurements();
    for (const interaction of interactions) {
      await this.addMeasurement({
        kind: "distance",
        atomIndices: [interaction.atom1, interaction.atom2],
        label: interaction.label,
        color: interaction.color,
      });
    }
  }

  destroy(): void {
    this.viewer?.dispose();
    this.viewer = null;
    this.measurements = [];
  }
}

export function createMolstarAdapter(context: ViewerAdapterContext): ViewerAdapter {
  return new MolstarAdapter(context);
}
