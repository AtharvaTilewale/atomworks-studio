import { api } from "../api";
import { element } from "../dom";
import type { SelectionController } from "../selection-controller";
import type { StructureSummary } from "../types";

export interface StructureEditControllerOptions {
  selection: SelectionController;
  getSessionId: () => string | null;
  mutate: (path: string, body?: unknown) => Promise<void>;
  log: (message: string) => void;
}

export class StructureEditController {
  constructor(private readonly options: StructureEditControllerOptions) {}

  initializeBondTypes(bondTypes: string[]): void {
    const select = element<HTMLSelectElement>("bond-type");
    for (const name of bondTypes) select.add(new Option(name, name));
  }

  bind(): void {
    element("apply-charge").addEventListener("click", () => {
      const action = element<HTMLSelectElement>("charge-action").value;
      const charge = Number(element<HTMLInputElement>("charge").value);
      void this.options.mutate("/charges", {
        atom_indices: [...this.options.selection.values],
        value: action === "value" ? charge : null,
        delta: action === "delta" ? charge : null,
      });
    });
    for (const action of ["add", "remove"] as const) {
      element(`${action}-bond`).addEventListener("click", () => {
        const [atom1, atom2] = [...this.options.selection.values];
        void this.options.mutate("/bonds", {
          atom1,
          atom2,
          action,
          bond_type: element<HTMLSelectElement>("bond-type").value,
        });
      });
    }
    element("delete-atoms").addEventListener("click", () => {
      void this.options.mutate("/delete", {
        atom_indices: [...this.options.selection.values],
        close_residue_gaps: element<HTMLInputElement>("close-residue-gaps").checked,
      });
    });
    element("chain-id").addEventListener("input", () => {
      this.updateSelectionUi(this.options.getSessionId() !== null);
    });
    element("assign-chain").addEventListener("click", () => {
      void this.options.mutate("/chains", {
        atom_indices: [...this.options.selection.values],
        chain_id: element<HTMLInputElement>("chain-id").value.trim(),
      });
    });
    element("reset-residues").addEventListener("click", () => {
      void this.options.mutate("/reset-residue-ids");
    });
    element("undo").addEventListener("click", () => void this.options.mutate("/undo"));
    element("segment-new-chain").addEventListener("change", () => {
      const checked = element<HTMLInputElement>("segment-new-chain").checked;
      element("segment-chain-options").hidden = !checked;
      element<HTMLInputElement>("segment-before").disabled = checked;
      this.updateSelectionUi(this.options.getSessionId() !== null);
    });
    element("insert-segment").addEventListener("click", () => {
      const newChain = element<HTMLInputElement>("segment-new-chain").checked;
      const chainId = element<HTMLInputElement>("segment-chain-id").value.trim();
      void this.options.mutate("/segments", {
        minimum: Number(element<HTMLInputElement>("segment-min").value),
        maximum: Number(element<HTMLInputElement>("segment-max").value),
        atom_index: newChain ? null : [...this.options.selection.values][0],
        before: element<HTMLInputElement>("segment-before").checked,
        new_chain: newChain,
        chain_id: newChain && chainId ? chainId : null,
        chain_type: newChain ? element<HTMLSelectElement>("segment-chain-type").value : null,
      });
    });
    element("realize-segments").addEventListener("click", () => {
      void this.options.mutate("/segments/realize", {});
    });
    element("collapse-segments").addEventListener("click", () => {
      void this.options.mutate("/segments/collapse");
    });
    element("validate").addEventListener("click", () => void this.validate());
  }

  updateSelectionUi(hasSession: boolean): void {
    const count = this.options.selection.size;
    element<HTMLButtonElement>("apply-charge").disabled = !hasSession || count === 0;
    element<HTMLButtonElement>("delete-atoms").disabled = !hasSession || count === 0;
    element<HTMLButtonElement>("assign-chain").disabled =
      !hasSession || count === 0 || !element<HTMLInputElement>("chain-id").value.trim();
    const newSegmentChain = element<HTMLInputElement>("segment-new-chain").checked;
    element<HTMLButtonElement>("insert-segment").disabled =
      !hasSession || (!newSegmentChain && count !== 1);
    element<HTMLButtonElement>("add-bond").disabled = !hasSession || count !== 2;
    element<HTMLButtonElement>("remove-bond").disabled = !hasSession || count !== 2;
  }

  private async validate(): Promise<void> {
    const sessionId = this.options.getSessionId();
    if (!sessionId) return;
    try {
      const result = await api<{ issues: StructureSummary["validation"] }>(
        `/api/sessions/${sessionId}/validate?full=true`,
        { method: "POST" },
      );
      this.options.log(
        result.issues.length
          ? result.issues.map((issue) => `${issue.check}: ${issue.message}`).join("\n")
          : "Validation passed.",
      );
    } catch (error) {
      this.options.log(`Validation failed: ${String(error)}`);
    }
  }
}
