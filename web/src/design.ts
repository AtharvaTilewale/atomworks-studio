type StructureSummary = {
  name: string;
  atom_count: number;
};

export type DesignDocument = {
  id: string;
  label: string;
  kind: "input" | "generated" | "refolded";
  rank?: number;
  metrics: Record<string, unknown>;
  session_id?: string;
  editor_path?: string;
  structure?: StructureSummary;
  alignment?: {
    matched_atom_count: number;
    rmsd_before: number;
    rmsd_after: number;
  };
};

type DesignJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  error?: string | null;
  log_tail?: string;
  input: DesignDocument;
  outputs: DesignDocument[];
  metric_summary: Record<string, { min: number; max: number; mean: number }>;
  progress?: {
    phase: string;
    elapsed_seconds: number;
    log_updated_at: number | null;
    indeterminate: boolean;
  };
};

export type OpenedDesignDocument = DesignDocument & {
  session_id: string;
  editor_path: string;
  structure: StructureSummary;
};

type DesignSchema = {
  enabled: boolean;
  provider: string;
  capabilities?: {
    af3?: boolean;
  };
  metrics_tags: string[];
  defaults?: {
    num_designs?: number;
    batch_size?: number;
    steps?: number;
    metrics_tags?: string[];
    search?: {
      step_checkpoints?: number[];
      n_branch?: number;
      beam_width?: number;
      reward_metrics_tags?: string[];
      weights?: Record<string, number>;
    };
  };
};

type SearchPreset = {
  rewardTag: string;
  rewardMetric: string;
  rewardWeight: number;
};

type RewardMetricPreset = {
  value: string;
  label: string;
  weight: number;
};

type DesignControllerOptions = {
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
  getSessionId: () => string | null;
  openDocument: (document: OpenedDesignDocument) => Promise<void>;
  compareDocuments: (documents: OpenedDesignDocument[]) => Promise<void>;
  clearComparison: () => Promise<void>;
  log: (message: string) => void;
};

const METRIC_META: Record<string, { label: string; hint: string; refolding?: true }> = {
  design:                       { label: "Design",         hint: "Bond lengths, clashes, secondary structure" },
  interchain_interactions:      { label: "Interactions",   hint: "H-bonds, π-stacks, salt bridges at the interface" },
  "3d_coordinate_conditioning": { label: "Motif RMSD",    hint: "All-atom RMSD of 3D-conditioned motif atoms" },
  unindexed:                    { label: "Unindexed RMSD", hint: "RMSD for insertion-point / unindexed motifs" },
  hotspot_conditioning:         { label: "Hotspot",        hint: "Hotspot atom conditioning adherence" },
  rasa_conditioning:            { label: "RASA",           hint: "Relative accessible surface area adherence" },
  ppi_refolding:                { label: "PPI",            hint: "Backbone + binder RMSD, iPAE, PTM · protein–protein", refolding: true },
  ame_refolding:                { label: "Active site",    hint: "Backbone + catalytic motif RMSD · MCSA / AME",        refolding: true },
  disco_refolding:              { label: "DISCO",          hint: "Backbone RMSD + ligand centroid distance",            refolding: true },
  sm_binder_refolding:          { label: "SM binder",      hint: "Backbone + ligand RMSD, iPAE · small-molecule",       refolding: true },
  motif_scaffold_refolding:     { label: "Motif scaffold", hint: "Backbone + motif CA and all-atom RMSD",               refolding: true },
  unconditional_refolding:      { label: "Unconditional",  hint: "Backbone RMSD only",                                  refolding: true },
};

const REWARD_METRIC_PRESETS: Record<string, RewardMetricPreset[]> = {
  design: [
    { value: "clash_metrics.frac_atoms_clashing_1p5A", label: "Clashing atoms (minimize)", weight: -1 },
    { value: "bond_length_metrics.bond_length.frac_unreasonable", label: "Unreasonable bonds (minimize)", weight: -1 },
  ],
  interchain_interactions: [
    { value: "interchain_interactions.hbond_atom_pair_count_coi", label: "Interface H-bond pairs (maximize)", weight: 1 },
    { value: "interchain_interactions.hydrophobic_atom_pair_count_coi", label: "Hydrophobic contact pairs (maximize)", weight: 1 },
    { value: "interchain_interactions.saltbridge_residue_pair_count_coi", label: "Salt-bridge residue pairs (maximize)", weight: 1 },
  ],
  hotspot_conditioning: [
    { value: "hotspot_obedience.hotspot_obedience", label: "Hotspot obedience (maximize)", weight: 1 },
  ],
  rasa_conditioning: [
    { value: "rasa_adherence.buried_conditioning_adherence", label: "Buried RASA adherence (maximize)", weight: 1 },
    { value: "rasa_adherence.exposed_conditioning_adherence", label: "Exposed RASA adherence (maximize)", weight: 1 },
  ],
  "3d_coordinate_conditioning": [
    { value: "design_motif_allatom_rmsd.rmsd", label: "Motif all-atom RMSD (minimize)", weight: -1 },
  ],
  unindexed: [
    { value: "design_unindexed_motif_allatom_rmsd.rmsd", label: "Insertion RMSD (minimize)", weight: -1 },
  ],
  unconditional_refolding: [
    { value: "oracle.ranking_score", label: "AF3 ranking score (maximize)", weight: 1 },
    { value: "oracle.overall_ptm", label: "AF3 overall pTM (maximize)", weight: 1 },
    { value: "oracle.plddt_coi", label: "AF3 chain pLDDT (maximize; requires COI)", weight: 1 },
    { value: "refolded_backbone_rmsd.rmsd", label: "AF3 backbone RMSD (minimize)", weight: -1 },
  ],
  ppi_refolding: [
    { value: "oracle_ppi.ptm_coi", label: "AF3 binder pTM (maximize)", weight: 1 },
    { value: "oracle_ppi.min_ipae_coi", label: "AF3 minimum interface PAE (minimize)", weight: -1 },
    { value: "refolded_target_aligned_binder_rmsd.rmsd", label: "AF3 target-aligned binder RMSD (minimize)", weight: -1 },
  ],
  ame_refolding: [
    { value: "oracle.plddt_coi", label: "AF3 chain pLDDT (maximize)", weight: 1 },
    { value: "refolded_motif_allatom_rmsd.rmsd", label: "AF3 catalytic motif RMSD (minimize)", weight: -1 },
  ],
  disco_refolding: [
    { value: "oracle.plddt_coi", label: "AF3 chain pLDDT (maximize)", weight: 1 },
    { value: "max_ligand_centroid_distance.ligand_centroid_distance", label: "AF3 ligand-centroid distance (minimize)", weight: -1 },
  ],
  sm_binder_refolding: [
    { value: "oracle.iptm_coi", label: "AF3 interface pTM (maximize)", weight: 1 },
    { value: "oracle.min_ipae_coi", label: "AF3 minimum interface PAE (minimize)", weight: -1 },
    { value: "refolded_backbone_aligned_ligand_rmsd.rmsd", label: "AF3 aligned ligand RMSD (minimize)", weight: -1 },
  ],
  motif_scaffold_refolding: [
    { value: "oracle.plddt_coi", label: "AF3 chain pLDDT (maximize)", weight: 1 },
    { value: "refolded_motif_allatom_rmsd.rmsd", label: "AF3 motif all-atom RMSD (minimize)", weight: -1 },
    { value: "refolded_motif_ca_rmsd.rmsd", label: "AF3 motif Cα RMSD (minimize)", weight: -1 },
  ],
};

const byId = <T extends HTMLElement>(id: string): T => {
  const value = document.getElementById(id);
  if (!value) throw new Error(`Missing element #${id}`);
  return value as T;
};

const availability = byId<HTMLParagraphElement>("design-availability");
const mode = byId<HTMLSelectElement>("design-mode");
const count = byId<HTMLInputElement>("design-count");
const batch = byId<HTMLInputElement>("design-batch");
const steps = byId<HTMLInputElement>("design-steps");
const metrics = byId<HTMLSelectElement>("design-metrics");
const metricsPicker = byId<HTMLDivElement>("metrics-picker");
const searchOptions = byId<HTMLDivElement>("search-options");
const searchBranches = byId<HTMLInputElement>("search-branches");
const searchBeam = byId<HTMLInputElement>("search-beam");
const searchCheckpoints = byId<HTMLInputElement>("search-checkpoints");
const searchRewardTag = byId<HTMLSelectElement>("search-reward-tag");
const searchReward = byId<HTMLInputElement>("search-reward");
const searchRewardOptions = byId<HTMLDataListElement>("search-reward-options");
const searchWeight = byId<HTMLInputElement>("search-weight");
const searchAf3Note = byId<HTMLParagraphElement>("search-af3-note");
const configFile = byId<HTMLInputElement>("design-config-file");
const configText = byId<HTMLTextAreaElement>("design-config");
const submit = byId<HTMLButtonElement>("submit-design");
const status = byId<HTMLDivElement>("design-job-status");
const progress = byId<HTMLDivElement>("design-progress");
const progressPhase = byId<HTMLElement>("design-progress-phase");
const progressElapsed = byId<HTMLElement>("design-progress-elapsed");
const progressLog = byId<HTMLPreElement>("design-progress-log");
const documents = byId<HTMLDivElement>("design-documents");
const metricSummary = byId<HTMLDivElement>("design-metric-summary");
const previousDesign = byId<HTMLButtonElement>("previous-design");
const nextDesign = byId<HTMLButtonElement>("next-design");
const alignDesign = byId<HTMLButtonElement>("align-design");
const compareDesigns = byId<HTMLButtonElement>("compare-designs");
const clearDesignComparison = byId<HTMLButtonElement>("clear-design-comparison");
const designPosition = byId<HTMLSpanElement>("design-position");
let currentMetricRanges: DesignJob["metric_summary"] = {};

function numberValue(input: HTMLInputElement): number {
  return Number(input.value);
}

function selectedValues(select: HTMLSelectElement): string[] {
  return [...select.selectedOptions].map((option) => option.value);
}

function metricLabel(key: string): string {
  return key
    .replace(/^oracle\./, "")
    .replaceAll("_", " ")
    .replaceAll(".", " · ");
}

function metricOptionLabel(key: string): string {
  const label = metricLabel(key);
  return `${label[0]?.toUpperCase() ?? ""}${label.slice(1)}`;
}

function formatValue(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function buildMetricsPicker(tags: string[], defaults: string[]): void {
  // Populate hidden select
  metrics.replaceChildren();
  for (const tag of tags) {
    const option = new Option(metricOptionLabel(tag), tag);
    option.selected = defaults.includes(tag);
    metrics.add(option);
  }

  // Build visual picker
  metricsPicker.replaceChildren();
  const nonRefolding = tags.filter((tag) => !METRIC_META[tag]?.refolding);
  const refolding = tags.filter((tag) => METRIC_META[tag]?.refolding);

  function makeGroup(groupTags: string[], groupLabel: string, extraClass?: string): void {
    if (!groupTags.length) return;
    const group = document.createElement("div");
    group.className = extraClass ? `metrics-group ${extraClass}` : "metrics-group";
    const labelEl = document.createElement("div");
    labelEl.className = "metrics-group-label";
    labelEl.textContent = groupLabel;
    const chips = document.createElement("div");
    chips.className = "metrics-chips";
    for (const tag of groupTags) {
      const meta = METRIC_META[tag];
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = defaults.includes(tag) ? "metric-chip selected" : "metric-chip";
      chip.dataset.tag = tag;
      chip.textContent = meta?.label ?? metricOptionLabel(tag);
      if (meta?.hint) chip.title = meta.hint;
      chip.addEventListener("click", () => {
        const isSelected = chip.classList.toggle("selected");
        const option = [...metrics.options].find((o) => o.value === tag);
        if (option) option.selected = isSelected;
      });
      chips.append(chip);
    }
    group.append(labelEl, chips);
    metricsPicker.append(group);
  }

  makeGroup(nonRefolding, "Design & conditioning");
  if (refolding.length) makeGroup(refolding, "AF3 refolding", "metrics-group-refolding");
}

function updateRewardMetricOptions(selectDefault = true): void {
  const presets = REWARD_METRIC_PRESETS[searchRewardTag.value] ?? [];
  searchRewardOptions.replaceChildren(
    ...presets.map((preset) => {
      const option = document.createElement("option");
      option.value = preset.value;
      option.label = preset.label;
      return option;
    }),
  );
  searchAf3Note.hidden = !METRIC_META[searchRewardTag.value]?.refolding;
  if (selectDefault && presets.length) {
    searchReward.value = presets[0].value;
    searchWeight.value = String(presets[0].weight);
  }
}

function renderMetrics(values: Record<string, unknown>): void {
  metricSummary.replaceChildren();
  const entries = Object.entries(values)
    .filter(([, value]) => typeof value === "number" || typeof value === "boolean")
    .sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) {
    metricSummary.textContent = "No metrics were requested for this structure.";
    metricSummary.className = "metric-summary muted";
    return;
  }
  metricSummary.className = "metric-summary";
  const heading = document.createElement("div");
  heading.className = "metric-summary-heading";
  heading.textContent = "Output metrics · bars show this output within the job range";
  metricSummary.append(heading);

  // Pass/fail badges
  const passEntries = entries.filter(([key]) => key.endsWith(".pass") || key === "pass_criteria");
  const nonPassEntries = entries.filter(([key]) => !key.endsWith(".pass") && key !== "pass_criteria");
  if (passEntries.length) {
    const badgeRow = document.createElement("div");
    badgeRow.className = "pass-badges";
    for (const [key, value] of passEntries) {
      const badge = document.createElement("div");
      const passed = Boolean(value);
      badge.className = `pass-badge pass-badge-${passed}`;
      badge.textContent = `${passed ? "✓" : "✗"} ${metricLabel(key)}`;
      badgeRow.append(badge);
    }
    metricSummary.append(badgeRow);
  }

  const appendMetric = (parent: HTMLElement, [key, value]: [string, unknown]): void => {
    const row = document.createElement("div");
    row.className = "metric-row";
    const label = document.createElement("span");
    label.textContent = metricLabel(key);
    const visual = document.createElement("span");
    visual.className = "metric-visual";
    const range = currentMetricRanges[key];
    if (typeof value === "number" && range) {
      const span = range.max - range.min;
      const position = span > 0 ? Math.max(0, Math.min(1, (value - range.min) / span)) : 0.5;
      visual.style.setProperty("--metric-position", `${position * 100}%`);
      visual.title = `job range ${formatValue(range.min)}–${formatValue(range.max)} · mean ${formatValue(range.mean)}`;
    } else {
      visual.classList.add("metric-visual-empty");
    }
    const formatted = document.createElement("strong");
    formatted.textContent = formatValue(value);
    row.append(label, visual, formatted);
    parent.append(row);
  };
  const importantPattern =
    /(search\.reward|plddt|iptm|ptm|ipae|rmsd|ddg|tmol|pass_criteria|interchain|hotspot|clash)/i;
  const important = nonPassEntries.filter(([key]) => importantPattern.test(key));
  const prioritized = [...important, ...nonPassEntries.filter((entry) => !important.includes(entry))];
  const highlighted = prioritized.slice(0, 12);
  for (const entry of highlighted) appendMetric(metricSummary, entry);
  const highlightedKeys = new Set(highlighted.map(([key]) => key));
  const remaining = nonPassEntries.filter(([key]) => !highlightedKeys.has(key));
  if (remaining.length) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `All other numeric metrics (${remaining.length})`;
    details.append(summary);
    for (const entry of remaining) appendMetric(details, entry);
    metricSummary.append(details);
  }
}

function requestBody(): Record<string, unknown> {
  const designMode = mode.value as "generate" | "search";
  const body: Record<string, unknown> = {
    mode: designMode,
    num_designs: numberValue(count),
    batch_size: numberValue(batch),
    steps: numberValue(steps),
    metrics_tags: selectedValues(metrics),
    config_yaml: configText.value.trim() || null,
  };
  if (designMode === "search") {
    const checkpoints = searchCheckpoints.value
      .split(",")
      .map((value) => Number(value.trim()))
      .filter(Number.isFinite);
    body.search = {
      step_checkpoints: checkpoints,
      n_branch: numberValue(searchBranches),
      beam_width: numberValue(searchBeam),
      reward_metrics_tags: [searchRewardTag.value],
      weights: { [searchReward.value.trim()]: numberValue(searchWeight) },
    };
  }
  return body;
}

export async function initializeDesignController(options: DesignControllerOptions): Promise<{
  updateSession: () => void;
  applyPreset: (metricsTags: string[], search?: SearchPreset) => string[];
}> {
  let enabled = false;
  let activeJob = false;
  let pollGeneration = 0;
  const autoOpened = new Set<string>();
  let activeDocuments: DesignDocument[] = [];
  let activeButtons: HTMLButtonElement[] = [];
  let activeDocumentIndex = -1;
  const comparedDocumentKeys = new Set<string>();
  let comparisonActive = false;

  const documentKey = (document: DesignDocument): string => `${document.kind}:${document.id}`;

  function updateDocumentControls(): void {
    const hasDocuments = activeDocuments.length > 0;
    previousDesign.disabled = !hasDocuments || activeDocuments.length < 2;
    nextDesign.disabled = !hasDocuments || activeDocuments.length < 2;
    alignDesign.disabled =
      !hasDocuments
      || activeDocumentIndex < 0
      || activeDocuments[activeDocumentIndex]?.kind === "input";
    compareDesigns.disabled = comparedDocumentKeys.size < 2;
    compareDesigns.textContent = `Compare selected (${comparedDocumentKeys.size})`;
    clearDesignComparison.disabled = !comparisonActive;
    designPosition.textContent = hasDocuments && activeDocumentIndex >= 0
      ? `${activeDocumentIndex + 1} / ${activeDocuments.length}`
      : "No results";
  }

  function updateSession(): void {
    submit.disabled = activeJob || !enabled || options.getSessionId() === null;
  }

  function applyPreset(metricsTags: string[], search?: SearchPreset): string[] {
    const requested = new Set(metricsTags);
    const selected: string[] = [];
    for (const option of metrics.options) {
      option.selected = requested.has(option.value);
      if (option.selected) selected.push(option.value);
    }
    // Sync chip visual states
    for (const chip of metricsPicker.querySelectorAll<HTMLButtonElement>(".metric-chip[data-tag]")) {
      chip.classList.toggle("selected", requested.has(chip.dataset.tag ?? ""));
    }
    if (search) {
      if ([...searchRewardTag.options].some((option) => option.value === search.rewardTag)) {
        searchRewardTag.value = search.rewardTag;
      }
      updateRewardMetricOptions(false);
      searchReward.value = search.rewardMetric;
      searchWeight.value = String(search.rewardWeight);
    }
    return selected;
  }

  async function resolveDocument(document: DesignDocument): Promise<OpenedDesignDocument> {
    const opened = document.session_id
      ? document
      : await options.request<OpenedDesignDocument>(
          `/api/design-jobs/${encodeURIComponent(currentJobId)}/outputs/${encodeURIComponent(document.id)}/open`,
          { method: "POST" },
        );
    if (!opened.session_id || !opened.structure || !opened.editor_path) {
      throw new Error("design output did not include an editor session");
    }
    return opened as OpenedDesignDocument;
  }

  async function open(document: DesignDocument, button: HTMLButtonElement): Promise<void> {
    const opened = await resolveDocument(document);
    for (const other of documents.querySelectorAll(".document-button")) other.classList.remove("active");
    button.classList.add("active");
    activeDocumentIndex = activeDocuments.findIndex(
      (candidate) => candidate.id === document.id && candidate.kind === document.kind,
    );
    if (activeDocumentIndex >= 0) activeDocuments[activeDocumentIndex] = opened;
    comparisonActive = false;
    updateDocumentControls();
    await options.openDocument(opened);
    renderMetrics(opened.metrics);
  }

  function openSafely(document: DesignDocument, button: HTMLButtonElement): void {
    void open(document, button).catch((error) => {
      status.textContent = `Could not open design output: ${String(error)}`;
    });
  }

  let currentJobId = "";

  async function compareSelectedDocuments(): Promise<void> {
    const selected = activeDocuments.filter((document) => comparedDocumentKeys.has(documentKey(document)));
    if (selected.length < 2) return;
    status.textContent = `Loading ${selected.length} structures for comparison…`;
    const opened = await Promise.all(selected.map(resolveDocument));
    for (const document of opened) {
      const index = activeDocuments.findIndex((candidate) => documentKey(candidate) === documentKey(document));
      if (index >= 0) activeDocuments[index] = document;
    }
    await options.compareDocuments(opened);
    comparisonActive = true;
    updateDocumentControls();
    status.textContent =
      `Comparing ${opened.map((document) => document.label).join(" · ")}. `
      + "Input is cyan; outputs use magenta, orange, and green.";
  }

  function cycleDocument(offset: number): void {
    if (activeDocuments.length < 2) return;
    const next = (activeDocumentIndex + offset + activeDocuments.length) % activeDocuments.length;
    activeButtons[next]?.click();
  }

  async function alignActiveDocument(): Promise<void> {
    const document = activeDocuments[activeDocumentIndex];
    const button = activeButtons[activeDocumentIndex];
    if (!document || !button || document.kind === "input") return;
    status.textContent = `Aligning ${document.label} to the submitted input…`;
    const aligned = await options.request<OpenedDesignDocument & { alignment: NonNullable<DesignDocument["alignment"]> }>(
      `/api/design-jobs/${encodeURIComponent(currentJobId)}/outputs/${encodeURIComponent(document.id)}/align-to-input`,
      { method: "POST" },
    );
    activeDocuments[activeDocumentIndex] = aligned;
    await options.openDocument(aligned);
    renderMetrics({
      ...aligned.metrics,
      "alignment.matched_atom_count": aligned.alignment.matched_atom_count,
      "alignment.rmsd_before": aligned.alignment.rmsd_before,
      "alignment.rmsd_after": aligned.alignment.rmsd_after,
    });
    status.textContent =
      `Aligned ${aligned.label} on ${aligned.alignment.matched_atom_count} common backbone atoms`
      + ` · RMSD ${aligned.alignment.rmsd_before.toFixed(3)} → ${aligned.alignment.rmsd_after.toFixed(3)} Å`;
  }

  function renderJob(job: DesignJob): void {
    if (currentJobId && currentJobId !== job.id) {
      comparedDocumentKeys.clear();
      comparisonActive = false;
    }
    currentJobId = job.id;
    currentMetricRanges = job.metric_summary;
    const running = job.status === "queued" || job.status === "running";
    status.textContent =
      job.status === "failed"
        ? `Failed: ${job.error ?? "unknown error"}${job.log_tail ? `\n${job.log_tail}` : ""}`
        : `${job.progress?.phase ?? `${job.status[0].toUpperCase()}${job.status.slice(1)}`}`
          + ` · ${job.outputs.length} output(s)`;
    progress.hidden = !running;
    progressPhase.textContent = job.progress?.phase ?? (job.status === "queued" ? "Waiting for GPU" : "Starting RFD4");
    progressElapsed.textContent = formatDuration(job.progress?.elapsed_seconds ?? 0);
    progressLog.textContent = job.log_tail?.trim() || "Waiting for RFD4 output…";
    progressLog.scrollTop = progressLog.scrollHeight;
    documents.replaceChildren();
    const allDocuments = [job.input, ...job.outputs];
    const previousActive = activeDocuments[activeDocumentIndex];
    activeDocuments = allDocuments;
    activeButtons = [];
    activeDocumentIndex = previousActive
      ? allDocuments.findIndex(
          (candidate) => candidate.id === previousActive.id && candidate.kind === previousActive.kind,
        )
      : 0;
    for (const designDocument of allDocuments) {
      const row = document.createElement("div");
      row.className = "document-row";
      const compare = document.createElement("input");
      compare.type = "checkbox";
      compare.className = "document-compare";
      compare.title = `Include ${designDocument.label} in the structure comparison`;
      compare.setAttribute("aria-label", `Compare ${designDocument.label}`);
      compare.checked = comparedDocumentKeys.has(documentKey(designDocument));
      const button = document.createElement("button");
      button.className = "document-button";
      if (allDocuments[activeDocumentIndex] === designDocument) button.classList.add("active");
      const kind = document.createElement("span");
      kind.className = "document-kind";
      kind.textContent = designDocument.kind === "input" ? "input" : `#${designDocument.rank ?? "–"}`;
      const label = document.createElement("span");
      label.textContent = designDocument.label;
      const score = document.createElement("span");
      const primaryMetric =
        designDocument.metrics["search.reward"] ?? designDocument.metrics["oracle.plddt_coi"];
      score.textContent =
        primaryMetric === undefined
          ? designDocument.structure
            ? `${designDocument.structure.atom_count} atoms`
            : "generated"
          : formatValue(primaryMetric);
      button.append(kind, label, score);
      button.addEventListener("click", () => openSafely(designDocument, button));
      compare.addEventListener("change", () => {
        const key = documentKey(designDocument);
        if (compare.checked) comparedDocumentKeys.add(key);
        else comparedDocumentKeys.delete(key);
        row.classList.toggle("compared", compare.checked);
        updateDocumentControls();
      });
      row.classList.toggle("compared", compare.checked);
      row.append(compare, button);
      documents.append(row);
      activeButtons.push(button);
    }
    updateDocumentControls();
    if (job.status === "completed" && job.outputs.length && !autoOpened.has(job.id)) {
      autoOpened.add(job.id);
      const firstOutput = documents.querySelectorAll<HTMLButtonElement>(".document-button")[1];
      if (firstOutput) openSafely(job.outputs[0], firstOutput);
    }
  }

  async function poll(jobId: string): Promise<void> {
    const generation = ++pollGeneration;
    while (generation === pollGeneration) {
      const job = await options.request<DesignJob>(`/api/design-jobs/${encodeURIComponent(jobId)}`);
      renderJob(job);
      if (job.status === "completed" || job.status === "failed") {
        activeJob = false;
        updateSession();
        sessionStorage.removeItem("annotation-studio-design-job");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  function monitor(jobId: string): void {
    void poll(jobId).catch((error) => {
      activeJob = false;
      updateSession();
      status.textContent = `Could not monitor design job: ${String(error)}`;
    });
  }

  previousDesign.addEventListener("click", () => cycleDocument(-1));
  nextDesign.addEventListener("click", () => cycleDocument(1));
  alignDesign.addEventListener("click", () => {
    alignDesign.disabled = true;
    void alignActiveDocument().catch((error) => {
      status.textContent = `Could not align output: ${String(error)}`;
    }).finally(updateDocumentControls);
  });
  compareDesigns.addEventListener("click", () => {
    compareDesigns.disabled = true;
    void compareSelectedDocuments().catch((error) => {
      status.textContent = `Could not compare structures: ${String(error)}`;
    }).finally(updateDocumentControls);
  });
  clearDesignComparison.addEventListener("click", () => {
    clearDesignComparison.disabled = true;
    void options.clearComparison().then(() => {
      comparisonActive = false;
      updateDocumentControls();
      status.textContent = "Structure comparison cleared.";
    }).catch((error) => {
      status.textContent = `Could not clear comparison: ${String(error)}`;
      updateDocumentControls();
    });
  });

  mode.addEventListener("change", () => {
    searchOptions.hidden = mode.value !== "search";
    if (mode.value === "search") {
      searchCheckpoints.value = `0,${Math.floor(numberValue(steps) / 2)},${numberValue(steps)}`;
      updateRewardMetricOptions(false);
    }
  });
  searchRewardTag.addEventListener("change", () => updateRewardMetricOptions());
  searchReward.addEventListener("change", () => {
    const preset = (REWARD_METRIC_PRESETS[searchRewardTag.value] ?? [])
      .find((candidate) => candidate.value === searchReward.value);
    if (preset) searchWeight.value = String(preset.weight);
  });
  steps.addEventListener("change", () => {
    if (mode.value === "search") {
      searchCheckpoints.value = `0,${Math.floor(numberValue(steps) / 2)},${numberValue(steps)}`;
    }
  });
  configFile.addEventListener("change", () => {
    const file = configFile.files?.[0];
    if (file) void file.text().then((text) => {
      configText.value = text;
    });
  });
  submit.addEventListener("click", () => {
    const sessionId = options.getSessionId();
    if (!sessionId) return;
    activeJob = true;
    submit.disabled = true;
    status.textContent = "Submitting…";
    void options.request<DesignJob>(`/api/sessions/${sessionId}/design-jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody()),
    }).then((job) => {
      sessionStorage.setItem("annotation-studio-design-job", job.id);
      options.log(`Submitted RFD4 design job ${job.id}.`);
      renderJob(job);
      monitor(job.id);
    }).catch((error) => {
      activeJob = false;
      status.textContent = `Submission failed: ${String(error)}`;
      options.log(`Design submission failed: ${String(error)}`);
    }).finally(updateSession);
  });

  try {
    const schema = await options.request<DesignSchema>("/api/design/schema");
    enabled = schema.enabled;
    searchRewardTag.replaceChildren();
    const defaults = schema.defaults?.metrics_tags?.length
      ? schema.defaults.metrics_tags
      : schema.metrics_tags.includes("design") ? ["design"] : [];
    buildMetricsPicker(schema.metrics_tags, defaults);
    for (const tag of schema.metrics_tags) {
      const option = new Option(metricOptionLabel(tag), tag);
      if (METRIC_META[tag]?.refolding && schema.capabilities?.af3 === false) {
        option.disabled = true;
        option.textContent += " · AF3 unavailable";
      }
      searchRewardTag.add(option);
    }
    if (schema.defaults?.num_designs) count.value = String(schema.defaults.num_designs);
    if (schema.defaults?.batch_size) batch.value = String(schema.defaults.batch_size);
    if (schema.defaults?.steps) steps.value = String(schema.defaults.steps);
    const searchDefaults = schema.defaults?.search;
    if (searchDefaults?.n_branch) searchBranches.value = String(searchDefaults.n_branch);
    if (searchDefaults?.beam_width) searchBeam.value = String(searchDefaults.beam_width);
    if (searchDefaults?.step_checkpoints?.length) {
      searchCheckpoints.value = searchDefaults.step_checkpoints.join(",");
    }
    const defaultRewardTag = searchDefaults?.reward_metrics_tags?.[0];
    if (defaultRewardTag && [...searchRewardTag.options].some((option) => option.value === defaultRewardTag)) {
      searchRewardTag.value = defaultRewardTag;
    }
    updateRewardMetricOptions(!Object.keys(searchDefaults?.weights ?? {}).length);
    const defaultReward = Object.entries(searchDefaults?.weights ?? {})[0];
    if (defaultReward) {
      searchReward.value = defaultReward[0];
      searchWeight.value = String(defaultReward[1]);
    }
    availability.textContent = enabled
      ? `${schema.provider} GPU queue is ready at ${window.location.host}.`
      : "GPU generation is not configured on this deployment; editing remains available.";
  } catch (error) {
    availability.textContent = `Could not inspect the GPU service: ${String(error)}`;
  }
  updateSession();

  const savedJob = sessionStorage.getItem("annotation-studio-design-job");
  if (enabled && savedJob) {
    activeJob = true;
    updateSession();
    monitor(savedJob);
  }
  return { updateSession, applyPreset };
}
