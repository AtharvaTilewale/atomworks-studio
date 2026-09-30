import { postJson } from "../api";
import { applyCapabilities, closeToolbarMenus, element } from "../dom";
import type { SelectionController } from "../selection-controller";
import type { SelectionSyntax, StructureSummary } from "../types";
import type {
  MolecularViewer,
  ViewerColor,
  ViewerMode,
  ViewerRepresentation,
  ViewerTarget,
} from "../viewers";
import { VIEWER_STORAGE_KEY } from "./persistence";

const FALLBACK_SAFE_PATINAE_COMMANDS = new Set([
  "show",
  "hide",
  "color",
  "zoom",
  "center",
  "orient",
  "enable",
  "disable",
  "toggle",
]);

const STUDIO_UNSAFE_PATINAE_COMMANDS = new Set([
  "alter", "bond", "create", "delete", "extract", "fuse", "load", "load_traj",
  "pseudoatom", "remove", "rotate", "transform_selection", "translate", "unbond",
]);

export interface ViewerToolbarControllerOptions {
  viewer: () => MolecularViewer;
  selection: SelectionController;
  getSessionId: () => string | null;
  getSummary: () => StructureSummary | null;
  requestedMode: () => ViewerMode | null;
  runViewerUpdate: (operation: () => Promise<void>) => Promise<void>;
  log: (message: string) => void;
}

export class ViewerToolbarController {
  private readonly viewerModeSelect = element<HTMLSelectElement>("viewer-mode");
  private readonly selectionSyntax = element<HTMLSelectElement>("selection-syntax");
  private readonly viewerCommand = element<HTMLInputElement>("viewer-command");
  private readonly viewerActionTarget = element<HTMLSelectElement>("viewer-action-target");
  private readonly representationExclusive = element<HTMLInputElement>("representation-exclusive");
  private readonly studioMenu = element<HTMLDetailsElement>("atomworks-studio-menu");
  private readonly studioPopover = element<HTMLElement>("atomworks-studio-popover");
  private readonly workspace = element<HTMLElement>("workspace");

  constructor(private readonly options: ViewerToolbarControllerOptions) {}

  bind(): void {
    for (const menu of document.querySelectorAll<HTMLDetailsElement>(".viewer-menu")) {
      menu.addEventListener("toggle", () => {
        // A studio tab (data-studio-tab) shows only the popover panels with the same data-studio-panel.
        const studioTab = menu.dataset.studioTab;
        if (menu.open) {
          closeToolbarMenus(menu);
          if (studioTab) this.showStudioTab(studioTab, menu.querySelector("summary")?.textContent?.trim() ?? "");
        } else if (
          studioTab === this.workspace.dataset.studioTab
          && !document.querySelector(".studio-menu[open]")
        ) {
          delete this.workspace.dataset.studioTab;
        }
      });
    }
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-representation]")) {
      button.addEventListener("click", () => {
        try {
          this.options.viewer().setRepresentation(
            button.dataset.representation as ViewerRepresentation,
            this.viewerActionTarget.value as ViewerTarget,
            this.representationExclusive.checked,
          );
        } catch (error) {
          this.options.log(`Display action failed: ${String(error)}`);
        }
      });
    }
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-color]")) {
      button.addEventListener("click", () => {
        try {
          this.options.viewer().setColor(
            button.dataset.color as ViewerColor,
            this.viewerActionTarget.value as ViewerTarget,
          );
        } catch (error) {
          this.options.log(`Color action failed: ${String(error)}`);
        }
      });
    }
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-camera]")) {
      button.addEventListener("click", () => {
        try {
          this.options.viewer().camera(button.dataset.camera as "reset" | "zoom" | "center" | "orient");
        } catch (error) {
          this.options.log(`View action failed: ${String(error)}`);
        }
      });
    }
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-background]")) {
      button.addEventListener("click", () => {
        try {
          this.options.viewer().setBackground(button.dataset.background as "black" | "white");
        } catch (error) {
          this.options.log(`Background action failed: ${String(error)}`);
        }
      });
    }
    const selectionExpressions: Record<string, string> = {
      all: "is_polymer | ~is_polymer",
      polymer: "is_polymer",
      ligand: "~is_polymer",
    };
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-selection]")) {
      button.addEventListener("click", () => {
        const preset = button.dataset.selection ?? "";
        if (preset === "none") {
          this.options.selection.clear();
          return;
        }
        void this.selectByExpression(selectionExpressions[preset], `Select ${preset}`)
          .catch((error) => this.options.log(`Selection failed: ${String(error)}`));
      });
    }
    element("command-search").addEventListener("input", () => this.renderCommandCatalog());
    element<HTMLFormElement>("viewer-command-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const command = this.viewerCommand.value;
      void this.executeCommand(command)
        .then(() => {
          this.viewerCommand.value = "";
        })
        .catch((error) => this.options.log(`Command failed: ${String(error)}`));
    });
    for (const [buttonId, drawerId] of [
      ["open-objects", "objects-drawer"],
      ["open-selections", "selections-drawer"],
      ["open-measurements", "measurements-drawer"],
    ] as const) {
      element(buttonId).addEventListener("click", () => this.openDrawer(drawerId));
    }
    this.viewerModeSelect.addEventListener("change", () => {
      const selected = this.parseMode(this.viewerModeSelect.value);
      if (selected) void this.switchRenderer(selected);
    });
    for (const button of document.querySelectorAll<HTMLButtonElement>(".renderer-prompt-choice")) {
      button.addEventListener("click", () => {
        const selected = this.parseMode(button.dataset.renderer ?? null);
        if (!selected) return;
        this.viewerModeSelect.value = selected;
        element("renderer-prompt").hidden = true;
        void this.switchRenderer(selected);
      });
    }
  }

  refreshCapabilities(): void {
    const capabilities = this.options.viewer().capabilities;
    applyCapabilities(capabilities);
    element("style-menu").hidden = !capabilities.representations && !capabilities.colors;
    element("picking-fallback").hidden = !capabilities.softwarePicking;
    element<HTMLSelectElement>("pick-scope").disabled = !capabilities.picking;
    this.selectionSyntax.querySelector<HTMLOptionElement>('option[value="patinae"]')!.disabled =
      !capabilities.commands;
    if (!capabilities.commands && this.selectionSyntax.value === "patinae") {
      this.selectionSyntax.value = "expression";
    }
    this.renderCommandCatalog();
  }

  renderDrawers(): void {
    const viewer = this.options.viewer();
    const objects = element("object-list");
    const viewerObjects = viewer.getObjectList();
    objects.replaceChildren();
    if (viewerObjects.length) {
      for (const object of viewerObjects) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "drawer-object";
        button.textContent = `${object.enabled === false ? "○" : "●"} ${object.name} · ${object.type ?? "object"}`
          + (object.atomCount === undefined ? "" : ` · ${object.atomCount.toLocaleString()} atoms`);
        button.title = object.enabled === false ? "Show object" : "Hide object";
        button.addEventListener("click", () => {
          viewer.setObjectEnabled(object.name, object.enabled === false);
          this.renderDrawers();
        });
        objects.append(button);
      }
    } else {
      const summary = this.options.getSummary();
      objects.textContent = summary
        ? `${summary.name} · ${summary.atom_count.toLocaleString()} atoms · chains ${summary.chain_ids.join(", ") || "—"}`
        : "Open a structure to list objects.";
    }
    const selectionList = element("selection-list");
    const selections = viewer.getSelectionList();
    selectionList.replaceChildren();
    if (selections.length) {
      for (const selection of selections) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "drawer-object";
        button.textContent = `${selection.name}${selection.expression ? ` · ${selection.expression}` : ""}`;
        button.addEventListener("click", () => {
          try {
            this.options.selection.replace(viewer.evaluateSelection(selection.name), true);
          } catch (error) {
            this.options.log(`Named selection failed: ${String(error)}`);
          }
        });
        selectionList.append(button);
      }
    } else {
      selectionList.textContent = `${this.options.selection.size} atoms in active selection`;
    }
    const measurements = viewer.listMeasurements();
    element("measurement-list").textContent = measurements.length
      ? measurements.map((item) =>
        `${item.label ?? item.kind ?? "Measurement"}${item.value === undefined ? "" : ` · ${item.value}${item.unit ?? ""}`}`
      ).join("\n")
      : "No measurements.";
  }

  openDrawer(drawerId: "objects-drawer" | "selections-drawer" | "measurements-drawer"): void {
    closeToolbarMenus();
    this.studioMenu.open = true;
    element<HTMLDetailsElement>(drawerId).open = true;
    this.renderDrawers();
  }

  async switchRenderer(mode: ViewerMode): Promise<void> {
    await this.options.runViewerUpdate(async () => {
      const viewer = this.options.viewer();
      const previousMode = (viewer.initialized ? viewer.backend : this.options.requestedMode()) ?? "molstar";
      try {
        await viewer.switchBackend(mode);
        localStorage.setItem(VIEWER_STORAGE_KEY, mode);
        const url = new URL(window.location.href);
        url.searchParams.set("viewer", mode);
        history.replaceState(null, "", url);
        this.refreshCapabilities();
        this.renderDrawers();
        this.options.log(`Switched renderer to ${mode === "patinae" ? "Patinae" : "Mol*"} without reloading.`);
      } catch (error) {
        this.viewerModeSelect.value = previousMode;
        this.options.log(`Renderer switch failed: ${String(error)}`);
      }
    });
  }

  private parseMode(value: string | null): ViewerMode | null {
    return value === "patinae" || value === "molstar" ? value : null;
  }

  private showStudioTab(tab: string, label: string): void {
    this.workspace.dataset.studioTab = tab;
    this.studioPopover.setAttribute("aria-label", label);
    for (const panel of this.studioPopover.querySelectorAll<HTMLElement>("[data-studio-panel]")) {
      panel.hidden = panel.dataset.studioPanel !== tab;
    }
  }

  private async executeCommand(
    command: string,
    syntax = this.selectionSyntax.value as SelectionSyntax,
  ): Promise<void> {
    const trimmed = command.trim();
    if (!trimmed) return;
    if (/[\n\r;]/.test(trimmed)) {
      throw new Error("Run one command at a time; command separators are not allowed.");
    }
    const sessionId = this.options.getSessionId();
    const atomworksMatch = /^(awselect|awselect-path)\s+(.+)$/i.exec(trimmed);
    if (syntax !== "patinae" || atomworksMatch) {
      if (!sessionId) throw new Error("Open a CIF before selecting atoms.");
      const querySyntax = atomworksMatch
        ? atomworksMatch[1].toLowerCase() === "awselect-path" ? "path" : "expression"
        : syntax;
      const response = await postJson<{ atom_indices: number[]; count: number }>(
        `/api/sessions/${sessionId}/selections/query`,
        { expression: atomworksMatch?.[2] ?? trimmed, syntax: querySyntax },
      );
      this.options.selection.replace(response.atom_indices, true);
      this.options.log(`AtomWorks ${querySyntax} query selected ${response.count} atoms.`);
      return;
    }
    const viewer = this.options.viewer();
    const verb = trimmed.split(/\s+/, 1)[0].toLowerCase();
    const catalog = viewer.getCommandCatalog();
    const metadata = catalog.find((entry) =>
      entry.name.toLowerCase() === verb
      || entry.aliases?.some((alias) => alias.toLowerCase() === verb)
    );
    const allowed = catalog.length
      ? metadata?.webSafe !== false && metadata !== undefined && !STUDIO_UNSAFE_PATINAE_COMMANDS.has(metadata.name)
      : FALLBACK_SAFE_PATINAE_COMMANDS.has(verb);
    if (!allowed) {
      throw new Error(
        STUDIO_UNSAFE_PATINAE_COMMANDS.has(metadata?.name ?? verb)
          ? `Command '${verb}' would desynchronize the annotated CIF. Use AtomWorks Studio editing controls instead.`
          : metadata?.unavailableReason
          ? `Command '${verb}' is unavailable in the browser: ${metadata.unavailableReason}`
          : `Unsupported viewer command '${verb}'. Browse the web-safe command catalog under •••.`,
      );
    }
    const result = viewer.executeCommand(trimmed) as {
      messages?: Array<{ level: string; text: string }>;
    };
    const messages = result?.messages?.map((message) => message.text.trim()).filter(Boolean) ?? [];
    this.options.log(messages.join("\n") || `Patinae: ${trimmed}`);
  }

  private async selectByExpression(expression: string | undefined, label: string): Promise<void> {
    const sessionId = this.options.getSessionId();
    if (!sessionId) throw new Error("Open a CIF before selecting atoms.");
    if (!expression) throw new Error("Unknown selection preset.");
    const response = await postJson<{ atom_indices: number[]; count: number }>(
      `/api/sessions/${sessionId}/selections/query`,
      { expression, syntax: "expression" },
    );
    this.options.selection.replace(response.atom_indices, true);
    this.options.log(`${label}: ${response.count} atoms selected.`);
  }

  private renderCommandCatalog(): void {
    const viewer = this.options.viewer();
    const container = element("command-catalog");
    if (!viewer.initialized) {
      container.textContent = "Choose Patinae to browse commands.";
      return;
    }
    const query = element<HTMLInputElement>("command-search").value.trim().toLowerCase();
    const commands = viewer.getCommandCatalog().filter((command) =>
      !query
      || command.name.toLowerCase().includes(query)
      || command.description?.toLowerCase().includes(query)
    );
    container.replaceChildren();
    if (!commands.length) {
      container.textContent = viewer.capabilities.commandCatalog
        ? "No matching commands."
        : "Choose Patinae to browse commands.";
      return;
    }
    for (const command of commands) {
      const button = document.createElement("button");
      button.type = "button";
      const studioUnsafe = STUDIO_UNSAFE_PATINAE_COMMANDS.has(command.name);
      button.disabled = command.webSafe === false || studioUnsafe;
      const name = document.createElement("strong");
      name.textContent = command.name;
      const description = document.createElement("small");
      description.textContent = studioUnsafe
        ? "Use AtomWorks Studio so the annotated CIF remains canonical."
        : command.webSafe === false
        ? command.unavailableReason ?? "Desktop only"
        : command.usage ?? command.description ?? "";
      button.append(name, description);
      button.addEventListener("click", () => {
        this.selectionSyntax.value = "patinae";
        this.viewerCommand.value = `${command.name} `;
        this.viewerCommand.focus();
        closeToolbarMenus();
      });
      container.append(button);
    }
  }
}
