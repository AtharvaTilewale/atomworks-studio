// AtomWorks-Quantum job panel; main.ts imports it lazily when /api/providers lists quantum as enabled.
import { api, postJson, versionedApiPath } from "../api";
import { element } from "../dom";
import type { StructureSummary } from "../types";
import {
  QUANTUM_CALCULATIONS,
  artifactFileName,
  availableCalculations,
  buildQuantumRequest,
  formDefaults,
  formatDuration,
  formatEnergy,
  isQuantumCalculation,
  jobSettled,
  modelNote,
  stageDefaults,
  type QuantumCalculation,
  type QuantumJobRequest,
  type QuantumModel,
  type QuantumOutput,
  type QuantumRequestSchema,
  type QuantumSchema,
} from "./quantum-request";

export type OpenedQuantumDocument = QuantumOutput & {
  session_id: string;
  editor_path: string;
  structure: StructureSummary;
};

type AlignedQuantumDocument = OpenedQuantumDocument & {
  alignment: { matched_atom_count: number; rmsd_before: number; rmsd_after: number };
};

type QuantumJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  error?: string | null;
  completed_at?: number | null;
  log_tail?: string;
  input: OpenedQuantumDocument;
  outputs: QuantumOutput[];
  progress?: { phase: string; elapsed_seconds: number };
};

type QuantumControllerOptions = {
  getSessionId: () => string | null;
  openDocument: (document: OpenedQuantumDocument) => Promise<void>;
  log: (message: string) => void;
};

const JOB_STORAGE_KEY = "annotation-studio-quantum-job";

export async function initializeQuantumController(
  options: QuantumControllerOptions,
): Promise<{ updateSession: () => void }> {
  const menu = element<HTMLDetailsElement>("quantum-menu");
  const availability = element<HTMLParagraphElement>("quantum-availability");
  const calculation = element<HTMLSelectElement>("quantum-calculation");
  const calculationNote = element<HTMLParagraphElement>("quantum-calculation-note");
  const model = element<HTMLSelectElement>("quantum-model");
  const modelNoteText = element<HTMLParagraphElement>("quantum-model-note");
  const charge = element<HTMLInputElement>("quantum-charge");
  const multiplicity = element<HTMLInputElement>("quantum-multiplicity");
  const fmaxRow = element<HTMLLabelElement>("quantum-fmax-row");
  const fmax = element<HTMLInputElement>("quantum-fmax");
  const stepsRow = element<HTMLLabelElement>("quantum-steps-row");
  const steps = element<HTMLInputElement>("quantum-steps");
  const submit = element<HTMLButtonElement>("submit-quantum");
  const cancel = element<HTMLButtonElement>("cancel-quantum");
  const status = element<HTMLDivElement>("quantum-job-status");
  const progress = element<HTMLDivElement>("quantum-progress");
  const progressPhase = element<HTMLElement>("quantum-progress-phase");
  const progressElapsed = element<HTMLElement>("quantum-progress-elapsed");
  const progressTrack = element<HTMLDivElement>("quantum-progress-track");
  const progressLog = element<HTMLPreElement>("quantum-progress-log");
  const results = element<HTMLDivElement>("quantum-results");

  let enabled = false;
  let models: QuantumModel[] = [];
  let requestSchema: QuantumRequestSchema = {};
  let stageTask = "";
  let activeJob = false;
  let currentJobId = "";
  let activeOutputId = "";
  let pollGeneration = 0;
  let pollController: AbortController | null = null;

  const jobPath = (jobId: string, suffix = ""): string =>
    `/api/providers/quantum/jobs/${encodeURIComponent(jobId)}${suffix}`;
  const outputPath = (output: QuantumOutput, suffix: string): string =>
    jobPath(currentJobId, `/outputs/${encodeURIComponent(output.id)}${suffix}`);

  function updateSession(): void {
    submit.disabled = activeJob || !enabled || options.getSessionId() === null;
    cancel.disabled = !activeJob || !currentJobId;
  }

  function updateForm(): void {
    const recipe = isQuantumCalculation(calculation.value) ? QUANTUM_CALCULATIONS[calculation.value] : null;
    // A new first stage task starts from aq's own force threshold and step count for it.
    if (recipe && recipe.tasks[0] !== stageTask) {
      stageTask = recipe.tasks[0];
      const defaults = stageDefaults(requestSchema, stageTask);
      if (defaults.fmax !== undefined) fmax.value = String(defaults.fmax);
      if (defaults.steps !== undefined) steps.value = String(defaults.steps);
    }
    calculationNote.textContent = recipe?.hint ?? "";
    fmaxRow.hidden = !recipe?.fmax;
    stepsRow.hidden = !recipe?.steps;
    const selected = models.find((candidate) => candidate.name === model.value);
    modelNoteText.textContent = selected ? modelNote(selected) : "";
  }

  async function open(output: QuantumOutput, align: boolean): Promise<void> {
    status.textContent = `${align ? "Aligning" : "Opening"} ${output.label}…`;
    if (align) {
      const aligned = await api<AlignedQuantumDocument>(outputPath(output, "/align-to-input"), { method: "POST" });
      await options.openDocument(aligned);
      status.textContent =
        `Aligned ${aligned.label} on ${aligned.alignment.matched_atom_count} common atoms`
        + ` · RMSD ${aligned.alignment.rmsd_before.toFixed(3)} → ${aligned.alignment.rmsd_after.toFixed(3)} Å`;
    } else {
      const opened = output.kind === "input"
        ? output as OpenedQuantumDocument
        : await api<OpenedQuantumDocument>(outputPath(output, "/open"), { method: "POST" });
      await options.openDocument(opened);
      status.textContent = `Opened ${opened.label}.`;
    }
    activeOutputId = output.id;
    for (const row of results.querySelectorAll<HTMLElement>(".quantum-result")) {
      row.classList.toggle("active", row.dataset.output === output.id);
    }
  }

  function actionButton(label: string, action: () => Promise<void>): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary compact";
    button.textContent = label;
    button.addEventListener("click", () => {
      button.disabled = true;
      void action()
        .catch((error) => {
          status.textContent = `Could not ${label.toLowerCase()}: ${String(error)}`;
        })
        .finally(() => {
          button.disabled = false;
        });
    });
    return button;
  }

  function resultRow(output: QuantumOutput): HTMLElement {
    const row = document.createElement("article");
    row.className = "quantum-result";
    row.classList.toggle("active", output.id === activeOutputId);
    row.dataset.output = output.id;
    const title = document.createElement("div");
    title.className = "quantum-result-title";
    const kind = document.createElement("span");
    kind.className = "document-kind";
    kind.textContent = output.kind === "input" ? "input" : `#${output.rank ?? "–"}`;
    const label = document.createElement("strong");
    label.textContent = output.label;
    title.append(kind, label);
    const facts = document.createElement("div");
    facts.className = "quantum-result-facts";
    const energy = formatEnergy(output);
    if (energy) facts.append(Object.assign(document.createElement("span"), { textContent: energy }));
    if (typeof output.metrics.converged === "boolean") {
      const converged = output.metrics.converged;
      facts.append(Object.assign(document.createElement("span"), {
        className: `pass-badge pass-badge-${converged}`,
        textContent: converged ? "converged" : "not converged",
      }));
    }
    if (typeof output.metrics.n_imaginary === "number") {
      facts.append(Object.assign(document.createElement("span"), {
        textContent: `${output.metrics.n_imaginary} imaginary`,
      }));
    }
    const actions = document.createElement("div");
    actions.className = "quantum-result-actions";
    actions.append(actionButton("Open", () => open(output, false)));
    if (output.kind !== "input") actions.append(actionButton("Align to input", () => open(output, true)));
    for (const artifact of output.artifacts ?? []) {
      const link = document.createElement("a");
      link.className = "artifact-link";
      link.href = versionedApiPath(outputPath(output, `/artifacts/${encodeURIComponent(artifact.name)}`));
      link.download = artifactFileName(artifact);
      link.textContent = artifactFileName(artifact);
      link.title = `Download ${artifact.label}`;
      actions.append(link);
    }
    row.append(title);
    if (facts.childElementCount) row.append(facts);
    row.append(actions);
    return row;
  }

  function renderJob(job: QuantumJob): void {
    if (currentJobId !== job.id) activeOutputId = "";
    currentJobId = job.id;
    const running = job.status === "queued" || job.status === "running";
    const phase = running
      ? job.progress?.phase ?? (job.status === "queued" ? "Queued" : "Starting aq")
      : `${job.status[0].toUpperCase()}${job.status.slice(1)}`;
    const count = `${job.outputs.length} result${job.outputs.length === 1 ? "" : "s"}`;
    status.textContent = job.status === "failed" ? `Failed: ${job.error ?? "unknown error"} · ${count}` : `${phase} · ${count}`;
    // Finished jobs keep their elapsed time and log; only the activity bar is for running jobs.
    progress.hidden = false;
    progressTrack.hidden = !running;
    progressPhase.textContent = phase;
    progressElapsed.textContent = formatDuration(job.progress?.elapsed_seconds ?? 0);
    progressLog.textContent = job.log_tail?.trim() || "Waiting for aq output…";
    progressLog.scrollTop = progressLog.scrollHeight;
    results.replaceChildren(resultRow(job.input), ...job.outputs.map(resultRow));
  }

  // A cancelled job is polled until its finished stages are collected (see jobSettled).
  async function poll(jobId: string): Promise<void> {
    const generation = ++pollGeneration;
    pollController?.abort();
    const controller = new AbortController();
    pollController = controller;
    try {
      while (generation === pollGeneration) {
        const job = await api<QuantumJob>(jobPath(jobId), { signal: controller.signal });
        if (generation !== pollGeneration) return;
        renderJob(job);
        if (jobSettled(job)) {
          activeJob = false;
          updateSession();
          sessionStorage.removeItem(JOB_STORAGE_KEY);
          options.log(`Quantum job ${job.id} ${job.status}.`);
          return;
        }
        const delay = document.visibilityState === "hidden" ? 15_000 : job.status === "queued" ? 5_000 : 2_000;
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    } catch (error) {
      if (generation === pollGeneration) throw error;
    } finally {
      if (pollController === controller) pollController = null;
    }
  }

  function monitor(jobId: string): void {
    void poll(jobId).catch((error) => {
      activeJob = false;
      updateSession();
      status.textContent = `Could not monitor quantum job: ${String(error)}`;
    });
  }

  for (const input of [calculation, model]) input.addEventListener("change", updateForm);
  submit.addEventListener("click", () => {
    const sessionId = options.getSessionId();
    if (!sessionId || !isQuantumCalculation(calculation.value)) return;
    let body: QuantumJobRequest;
    try {
      body = buildQuantumRequest({
        calculation: calculation.value,
        model: model.value,
        charge: charge.valueAsNumber,
        multiplicity: multiplicity.valueAsNumber,
        fmax: fmax.valueAsNumber,
        steps: steps.valueAsNumber,
      });
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
      return;
    }
    activeJob = true;
    // Stop collecting a cancelled job's stages: its last poll would mark this job as inactive.
    pollGeneration += 1;
    pollController?.abort();
    updateSession();
    status.textContent = "Submitting…";
    void postJson<QuantumJob>(`/api/sessions/${sessionId}/providers/quantum/jobs`, body).then((job) => {
      sessionStorage.setItem(JOB_STORAGE_KEY, job.id);
      options.log(`Submitted quantum job ${job.id}.`);
      renderJob(job);
      monitor(job.id);
    }).catch((error) => {
      activeJob = false;
      status.textContent = `Submission failed: ${String(error)}`;
      options.log(`Quantum submission failed: ${String(error)}`);
    }).finally(updateSession);
  });
  cancel.addEventListener("click", () => {
    if (!currentJobId || !activeJob) return;
    cancel.disabled = true;
    status.textContent = "Cancelling…";
    void api<QuantumJob>(jobPath(currentJobId, "/cancel"), { method: "POST" }).then((job) => {
      activeJob = false;
      sessionStorage.removeItem(JOB_STORAGE_KEY);
      renderJob(job);
      monitor(job.id);
    }).catch((error) => {
      status.textContent = `Could not cancel quantum job: ${String(error)}`;
    }).finally(updateSession);
  });

  function showSchema(schema: QuantumSchema): void {
    if (!schema.enabled) {
      availability.textContent = `${schema.label} cannot run jobs here: ${schema.reason}`;
      availability.dataset.state = "offline";
      return;
    }
    enabled = true;
    models = schema.models;
    requestSchema = schema.request_schema;
    const offered = new Set(availableCalculations(schema.tasks));
    calculation.replaceChildren(...(Object.keys(QUANTUM_CALCULATIONS) as QuantumCalculation[]).map((value) => {
      const option = new Option(QUANTUM_CALCULATIONS[value].label, value);
      option.disabled = !offered.has(value);
      if (option.disabled) option.textContent += " · not offered";
      return option;
    }));
    model.replaceChildren(...models.map((candidate) => new Option(candidate.name, candidate.name)));
    const defaults = formDefaults(schema.defaults);
    if (defaults.calculation && offered.has(defaults.calculation)) calculation.value = defaults.calculation;
    if (defaults.model && models.some((candidate) => candidate.name === defaults.model)) model.value = defaults.model;
    if (defaults.charge !== undefined) charge.value = String(defaults.charge);
    if (defaults.multiplicity) multiplicity.value = String(defaults.multiplicity);
    // aq's defaults for the chosen calculation first, then the provider's own default request.
    updateForm();
    if (defaults.fmax !== undefined) fmax.value = String(defaults.fmax);
    if (defaults.steps !== undefined) steps.value = String(defaults.steps);
    availability.textContent =
      `${schema.label} is ready with ${models.length} installed model${models.length === 1 ? "" : "s"}.`;
    availability.dataset.state = "ready";
  }

  try {
    showSchema(await api<QuantumSchema>("/api/providers/quantum/jobs/schema"));
  } catch (error) {
    availability.textContent = `Could not inspect the quantum service: ${String(error)}`;
    availability.dataset.state = "error";
  }
  menu.hidden = false;
  updateForm();
  updateSession();
  const savedJob = sessionStorage.getItem(JOB_STORAGE_KEY);
  if (enabled && savedJob) {
    activeJob = true;
    currentJobId = savedJob;
    updateSession();
    monitor(savedJob);
  }
  return { updateSession };
}
