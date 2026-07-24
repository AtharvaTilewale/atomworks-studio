import { api } from "../api";
import { AnnotationOverlayState } from "../annotation-overlay";
import { element } from "../dom";
import type { SelectionController } from "../selection-controller";
import type { AnnotationSummary, AnnotationValue } from "../types";
import type { MolecularViewer } from "../viewers";

export interface AnnotationControllerOptions {
  viewer: () => MolecularViewer;
  selection: SelectionController;
  getSessionId: () => string | null;
  log: (message: string) => void;
}

export class AnnotationController {
  private readonly list = element<HTMLDivElement>("annotation-list");
  private readonly detail = element<HTMLDivElement>("annotation-detail");
  private readonly search = element<HTMLInputElement>("annotation-search");
  private readonly overlay = new AnnotationOverlayState();

  constructor(private readonly options: AnnotationControllerOptions) {}

  bind(): void {
    this.search.addEventListener("input", () => this.filterRows());
    element("clear-annotation-view").addEventListener("click", () => {
      void this.overlay.clear(this.options.viewer(), this.options.selection.values).then(() => {
        element<HTMLButtonElement>("clear-annotation-view").disabled = true;
        this.detail.textContent = "Annotation colors cleared. Click an annotation or pick an atom.";
      }).catch((error) => this.options.log(`Could not clear annotation colors: ${String(error)}`));
    });
  }

  render(
    annotations: AnnotationSummary[],
    storedFields: AnnotationSummary[],
    scalarFields: AnnotationSummary[],
  ): void {
    this.list.replaceChildren();
    if (!annotations.length && !storedFields.length && !scalarFields.length) {
      this.list.className = "annotation-list muted";
      this.list.textContent = "No explicitly stored, non-default annotations.";
      this.detail.textContent = "Pick an atom in the molecular viewer to inspect it.";
      return;
    }
    this.list.className = "annotation-list";
    for (const annotation of annotations) {
      this.appendRow(annotation.label, `${annotation.count} ${annotation.unit}`, (row) => {
        void this.inspectAnnotation(annotation.name, row);
      });
    }
    this.appendFields("All serialized fields", storedFields, "annotation-fields");
    this.appendFields("Output confidence", scalarFields, "scalar-fields");
    this.detail.textContent = "Click an annotation to color it, or pick an atom for per-atom details.";
    this.filterRows();
  }

  async display(annotation: AnnotationSummary): Promise<void> {
    const groups = annotation.values
      .filter((value) => value.atom_indices?.length)
      .map((value) => ({ label: value.label, atomIndices: value.atom_indices!, color: value.color }));
    await this.overlay.show(this.options.viewer(), groups, this.options.selection.values);
    element<HTMLButtonElement>("clear-annotation-view").disabled = groups.length === 0;
  }

  async inspectAtom(atomIndex: number): Promise<void> {
    const sessionId = this.options.getSessionId();
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
        atom.formal_charge
          ? `Formal charge: ${atom.formal_charge > 0 ? "+" : ""}${atom.formal_charge}`
          : "",
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
      this.detail.textContent = lines.join("\n");
      for (const row of this.list.querySelectorAll(".annotation-row")) row.classList.remove("active");
    } catch (error) {
      this.detail.textContent = `Could not inspect atom: ${String(error)}`;
    }
  }

  async restore(): Promise<void> {
    await this.overlay.restore(this.options.viewer(), this.options.selection.values);
  }

  reset(): void {
    this.overlay.reset();
  }

  setDetail(message: string): void {
    this.detail.textContent = message;
  }

  private appendFields(
    headingText: string,
    fields: AnnotationSummary[],
    endpoint: "annotation-fields" | "scalar-fields",
  ): void {
    if (!fields.length) return;
    const heading = document.createElement("div");
    heading.className = "annotation-fields-heading";
    heading.textContent = endpoint === "annotation-fields"
      ? `${headingText} (${fields.length})`
      : headingText;
    this.list.appendChild(heading);
    for (const field of fields) {
      const count = endpoint === "annotation-fields"
        ? `${field.values.length} value${field.values.length === 1 ? "" : "s"}`
        : `${field.count} ${field.unit}`;
      this.appendRow(field.label, count, (row) => {
        void this.inspectStoredField(field.name, row, endpoint);
      }, endpoint === "annotation-fields");
    }
  }

  private appendRow(
    labelText: string,
    countText: string,
    onClick: (row: HTMLButtonElement) => void,
    field = false,
  ): void {
    const row = document.createElement("button");
    row.className = `annotation-row${field ? " annotation-field-row" : ""}`;
    const label = document.createElement("span");
    label.textContent = labelText;
    const count = document.createElement("span");
    count.textContent = countText;
    row.append(label, count);
    row.addEventListener("click", () => onClick(row));
    this.list.appendChild(row);
  }

  private filterRows(): void {
    const query = this.search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of this.list.querySelectorAll<HTMLElement>(".annotation-row")) {
      row.hidden = Boolean(query) && !row.textContent?.toLocaleLowerCase().includes(query);
      if (!row.hidden) visible += 1;
    }
    this.search.setAttribute("aria-description", `${visible} matching annotations`);
  }

  private formatValue(value: AnnotationValue): string {
    const suffix = Array.isArray(value.value)
      ? ` = (${value.value.map((item) => item.toFixed(3)).join(", ")})`
      : value.value === undefined ? "" : ` = ${value.value}`;
    return `${value.label}${suffix}: ${value.count.toLocaleString()} atom${value.count === 1 ? "" : "s"}`;
  }

  private activateRow(row: HTMLButtonElement): void {
    for (const other of this.list.querySelectorAll(".annotation-row")) {
      other.classList.toggle("active", other === row);
    }
  }

  private async inspectAnnotation(name: string, row: HTMLButtonElement): Promise<void> {
    const sessionId = this.options.getSessionId();
    if (!sessionId) return;
    try {
      const result = await api<{ annotation: AnnotationSummary | null }>(
        `/api/sessions/${sessionId}/annotations/${encodeURIComponent(name)}`,
      );
      if (!result.annotation) {
        this.detail.textContent = "This annotation is no longer active.";
        return;
      }
      this.activateRow(row);
      this.detail.textContent = [
        `${result.annotation.label}: ${result.annotation.count} ${result.annotation.unit}`,
        ...result.annotation.values.map((value) => this.formatValue(value)),
      ].join("\n");
      await this.display(result.annotation);
    } catch (error) {
      this.detail.textContent = `Could not inspect annotation: ${String(error)}`;
    }
  }

  private async inspectStoredField(
    name: string,
    row: HTMLButtonElement,
    endpoint: "annotation-fields" | "scalar-fields",
  ): Promise<void> {
    const sessionId = this.options.getSessionId();
    if (!sessionId) return;
    try {
      const result = await api<{ annotation: AnnotationSummary }>(
        `/api/sessions/${sessionId}/${endpoint}/${encodeURIComponent(name)}`,
      );
      this.activateRow(row);
      this.detail.textContent = [
        `${result.annotation.label}: ${result.annotation.count} ${result.annotation.unit}`,
        ...result.annotation.values.map((value) => this.formatValue(value)),
        result.annotation.truncated ? "Additional distinct values omitted from this preview." : "",
      ].filter(Boolean).join("\n");
      await this.display(result.annotation);
    } catch (error) {
      this.detail.textContent = `Could not inspect stored field: ${String(error)}`;
    }
  }
}
