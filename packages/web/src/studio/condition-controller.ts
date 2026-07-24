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
  private readonly feedback = element<HTMLParagraphElement>("condition-feedback");
  private schema: StudioSchema | null = null;
  private hasSession = false;

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
    this.enabledInput.addEventListener("change", () => {
      this.renderValue();
      this.updateSelectionUi(this.hasSession);
    });
    this.applyButton.addEventListener("click", () => {
      this.feedback.textContent = this.enabledInput.checked
        ? "Applying condition…"
        : "Removing condition from the selection…";
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
    this.hasSession = hasSession;
    const pair = this.currentDefinition()?.kind === "pair";
    const count = this.options.selection.size;
    this.applyButton.disabled = !hasSession || (pair ? count !== 2 : count === 0);
    this.applyButton.textContent = this.enabledInput.checked ? "Apply to selection" : "Remove from selection";
    this.feedback.textContent = !hasSession
      ? "Open a structure to edit conditions."
      : pair
        ? count === 2
          ? "Ready: the selected atoms define one pair."
          : `Select exactly two atoms (${count} selected).`
        : count
          ? `Ready to update ${count} selected atom${count === 1 ? "" : "s"}.`
          : "Select one or more atoms.";
  }

  private conditionChanged(): void {
    const definition = this.currentDefinition();
    if (!definition) return;
    this.scopeSelect.value =
      definition.level === "chain" ? "chain" : definition.level === "atom" ? "atom" : "residue";
    this.scopeSelect.disabled = definition.kind === "pair";
    this.renderValue();
    this.updateSelectionUi(this.hasSession);
  }

  private renderValue(): void {
    const definition = this.currentDefinition();
    this.conditionValue.replaceChildren();
    if (!definition || definition.kind === "mask") return;

    const label = document.createElement("label");
    label.textContent =
      definition.kind === "vector3"
        ? "Centroid (x, y, z)"
        : definition.kind === "pair"
          ? "Pair distance (Å)"
          : "Value";
    if (definition.kind === "enum") {
      const select = document.createElement("select");
      select.id = "condition-value-input";
      for (const [name, value] of Object.entries(definition.values ?? {})) {
        if (value !== definition.default) select.add(new Option(name, String(value)));
      }
      label.appendChild(select);
    } else if (definition.kind === "integer" || definition.kind === "pair") {
      const input = document.createElement("input");
      input.id = "condition-value-input";
      input.type = "number";
      input.step = definition.kind === "pair" ? "0.1" : "1";
      if (definition.kind === "pair") input.min = "0.01";
      input.value =
        typeof definition.default === "number"
          ? String(definition.default)
          : definition.kind === "pair" ? "4" : "0";
      input.disabled = !this.enabledInput.checked;
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
    const definition = this.currentDefinition();
    if (!definition || !this.enabledInput.checked || definition.kind === "mask") return null;
    if (definition.kind === "vector3") {
      return ["x", "y", "z"].map((axis) =>
        Number(element<HTMLInputElement>(`condition-value-${axis}`).value)
      );
    }
    return Number(element<HTMLInputElement | HTMLSelectElement>("condition-value-input").value);
  }

  private currentDefinition() {
    return this.schema?.conditions[this.conditionSelect.value];
  }
}
