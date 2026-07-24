import { element } from "../dom";
import type { SelectionController } from "../selection-controller";
import type { StudioSchema } from "../types";

export interface ConditionControllerOptions {
  selection: SelectionController;
  mutate: (path: string, body?: unknown) => Promise<void>;
}

export class ConditionController {
  private readonly conditionSelect = element<HTMLSelectElement>("condition");
  private readonly scopeSelect = element<HTMLSelectElement>("scope");
  private readonly enabledInput = element<HTMLInputElement>("enabled");
  private readonly conditionValue = element<HTMLDivElement>("condition-value");
  private readonly applyButton = element<HTMLButtonElement>("apply-condition");
  private schema: StudioSchema | null = null;

  constructor(private readonly options: ConditionControllerOptions) {}

  initialize(schema: StudioSchema): void {
    this.schema = schema;
    for (const [name, definition] of Object.entries(schema.conditions)) {
      this.conditionSelect.add(new Option(definition.label, name));
    }
    this.conditionChanged();
  }

  bind(): void {
    this.conditionSelect.addEventListener("change", () => this.conditionChanged());
    this.applyButton.addEventListener("click", () => {
      void this.options.mutate("/conditions", {
        condition: this.conditionSelect.value,
        atom_indices: [...this.options.selection.values],
        enabled: this.enabledInput.checked,
        value: this.selectedValue(),
        scope: this.scopeSelect.value,
      });
    });
  }

  updateSelectionUi(hasSession: boolean): void {
    this.applyButton.disabled = !hasSession || this.options.selection.size === 0;
  }

  private conditionChanged(): void {
    const definition = this.schema?.conditions[this.conditionSelect.value];
    if (!definition) return;
    this.scopeSelect.value =
      definition.level === "chain" ? "chain" : definition.level === "atom" ? "atom" : "residue";
    this.renderValue();
  }

  private renderValue(): void {
    const definition = this.schema?.conditions[this.conditionSelect.value];
    this.conditionValue.replaceChildren();
    if (!definition || definition.kind === "mask") return;

    const label = document.createElement("label");
    label.textContent = definition.kind === "vector3" ? "Centroid (x, y, z)" : "Value";
    if (definition.kind === "enum") {
      const select = document.createElement("select");
      select.id = "condition-value-input";
      for (const [name, value] of Object.entries(definition.values ?? {})) {
        if (value !== definition.default) select.add(new Option(name, String(value)));
      }
      label.appendChild(select);
    } else if (definition.kind === "integer") {
      const input = document.createElement("input");
      input.id = "condition-value-input";
      input.type = "number";
      input.value = "0";
      label.appendChild(input);
    } else {
      const wrapper = document.createElement("div");
      wrapper.className = "inline-fields";
      for (const axis of ["x", "y", "z"]) {
        const input = document.createElement("input");
        input.id = `condition-value-${axis}`;
        input.type = "number";
        input.step = "0.1";
        input.value = "0";
        wrapper.appendChild(input);
      }
      label.appendChild(wrapper);
    }
    this.conditionValue.appendChild(label);
  }

  private selectedValue(): number | number[] | null {
    const definition = this.schema?.conditions[this.conditionSelect.value];
    if (!definition || !this.enabledInput.checked || definition.kind === "mask") return null;
    if (definition.kind === "vector3") {
      return ["x", "y", "z"].map((axis) =>
        Number(element<HTMLInputElement>(`condition-value-${axis}`).value)
      );
    }
    return Number(element<HTMLInputElement | HTMLSelectElement>("condition-value-input").value);
  }
}
