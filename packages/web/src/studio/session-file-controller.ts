import { api, apiBlob } from "../api";
import { element } from "../dom";
import { STUDIO_EXAMPLES, type StudioExample } from "../examples";
import type { SessionResponse } from "../types";
import { inspect_cif } from "../wasm/annotation_studio_core.js";

export interface SessionFileControllerOptions {
  getSessionId: () => string | null;
  getCurrentName: () => string;
  onOpened: (response: SessionResponse) => Promise<void>;
  onCombined: (response: SessionResponse, filename: string) => Promise<void>;
  applyExamplePreset: (example: StudioExample) => string[];
  log: (message: string) => void;
}

export class SessionFileController {
  private readonly fileInput = element<HTMLInputElement>("file-input");
  private readonly combineInput = element<HTMLInputElement>("combine-input");
  private openGeneration = 0;
  private openQueue: Promise<void> = Promise.resolve();

  constructor(private readonly options: SessionFileControllerOptions) {}

  bind(): void {
    this.fileInput.addEventListener("change", () => {
      const file = this.fileInput.files?.[0];
      if (file) void this.openFile(file);
    });
    this.combineInput.addEventListener("change", () => void this.combineFile());
    element("download").addEventListener("click", () => void this.download());
    for (const button of document.querySelectorAll<HTMLButtonElement>(".example-load")) {
      button.addEventListener("click", () => {
        const example = STUDIO_EXAMPLES.find((candidate) => candidate.id === button.dataset.example);
        if (example) void this.loadExample(example, button);
      });
    }
  }

  async loadPendingExample(id: string): Promise<void> {
    const example = STUDIO_EXAMPLES.find((candidate) => candidate.id === id);
    const button = document.querySelector<HTMLButtonElement>(
      `.example-load[data-example="${id}"]`,
    );
    if (example && button) await this.loadExample(example, button);
  }

  private openFile(file: File): Promise<boolean> {
    const generation = ++this.openGeneration;
    const request = this.openQueue.then(() => this.performOpen(file, generation));
    this.openQueue = request.then(() => undefined, () => undefined);
    return request;
  }

  private async performOpen(file: File, generation: number): Promise<boolean> {
    if (generation !== this.openGeneration) return false;
    try {
      const cif = await file.text();
      inspect_cif(cif);
      const form = new FormData();
      form.append("file", file);
      this.appendParseOptions(form);
      const response = await api<SessionResponse>("/api/sessions", { method: "POST", body: form });
      if (generation !== this.openGeneration) return false;
      await this.options.onOpened(response);
      this.options.log("Native AtomWorks session ready.");
      return true;
    } catch (error) {
      if (generation === this.openGeneration) this.options.log(`Open failed: ${String(error)}`);
      return false;
    }
  }

  private appendParseOptions(form: FormData): void {
    form.append("parse_preset", element<HTMLSelectElement>("parse-preset").value);
    form.append("assembly", element<HTMLInputElement>("parse-assembly").value.trim() || "asym");
    const model = element<HTMLInputElement>("parse-model").value;
    if (model) form.append("model", model);
    form.append("hydrogen_policy", element<HTMLSelectElement>("parse-hydrogens").value);
    form.append("remove_waters", String(element<HTMLInputElement>("parse-remove-waters").checked));
  }

  private async loadExample(
    example: StudioExample,
    activeButton: HTMLButtonElement,
  ): Promise<void> {
    const buttons = [...document.querySelectorAll<HTMLButtonElement>(".example-load")];
    for (const button of buttons) button.disabled = true;
    const originalLabel = activeButton.textContent;
    activeButton.textContent = "Loading…";
    try {
      const loaded = await this.openFile(
        new File([example.cif], example.filename, { type: "chemical/x-mmcif" }),
      );
      if (!loaded) return;
      const selectedMetrics = this.options.applyExamplePreset(example);
      this.options.log(
        `Loaded ${example.name}.`
        + (selectedMetrics.length
          ? ` Suggested metrics selected: ${selectedMetrics.join(", ")}.`
          : ""),
      );
    } finally {
      activeButton.textContent = originalLabel;
      for (const button of buttons) button.disabled = false;
    }
  }

  private async combineFile(): Promise<void> {
    const file = this.combineInput.files?.[0];
    const targetSession = this.options.getSessionId();
    if (!file || !targetSession) return;
    try {
      const cif = await file.text();
      inspect_cif(cif);
      const form = new FormData();
      form.append("file", file);
      this.appendParseOptions(form);
      const incoming = await api<SessionResponse>("/api/sessions", { method: "POST", body: form });
      const combined = await api<SessionResponse>(`/api/sessions/${targetSession}/combine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_ids: [incoming.session_id] }),
      });
      if (this.options.getSessionId() !== targetSession) return;
      await this.options.onCombined(combined, file.name);
    } catch (error) {
      this.options.log(`Combine failed: ${String(error)}`);
    } finally {
      this.combineInput.value = "";
    }
  }

  private async download(): Promise<void> {
    const sessionId = this.options.getSessionId();
    if (!sessionId) return;
    try {
      const blob = await apiBlob(`/api/sessions/${sessionId}/cif`);
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = `${this.options.getCurrentName()}_annotated.cif`;
      anchor.click();
      URL.revokeObjectURL(anchor.href);
    } catch (error) {
      this.options.log(`Download failed: ${String(error)}`);
    }
  }
}
