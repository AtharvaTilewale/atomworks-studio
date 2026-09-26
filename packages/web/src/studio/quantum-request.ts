// Request building and result formatting for the AtomWorks-Quantum panel; no DOM access.

export const QUANTUM_REQUEST_SCHEMA = "atomworks-quantum.job/1";

export type QuantumCalculation = "singlepoint" | "optimize" | "optimize-frequencies" | "find_ts" | "md";

type CalculationRecipe = {
  label: string;
  hint: string;
  // aq stage tasks, run in order; the first stage takes the force threshold and step count it uses.
  tasks: readonly string[];
  fmax: boolean;
  steps: boolean;
};

export const QUANTUM_CALCULATIONS: Record<QuantumCalculation, CalculationRecipe> = {
  singlepoint: {
    label: "Single point",
    hint: "Energy and forces of the current structure.",
    tasks: ["singlepoint"],
    fmax: false,
    steps: false,
  },
  optimize: {
    label: "Optimize",
    hint: "Minimize the energy until the largest atomic force is below the threshold.",
    tasks: ["optimize"],
    fmax: true,
    steps: true,
  },
  "optimize-frequencies": {
    label: "Optimize + frequencies",
    hint: "Optimize, then compute harmonic frequencies at the minimum.",
    tasks: ["optimize", "frequencies"],
    fmax: true,
    steps: true,
  },
  find_ts: {
    label: "Transition state search",
    hint: "Search for a saddle point from the current structure and check it by its frequencies; needs aq's ts extra.",
    tasks: ["find_ts"],
    fmax: true,
    steps: true,
  },
  md: {
    label: "Molecular dynamics",
    hint: "NVT molecular dynamics from the current structure with aq's default thermostat.",
    tasks: ["md"],
    fmax: false,
    steps: true,
  },
};

export type QuantumModel = {
  name: string;
  backend: string;
  devices: string[];
  charge: boolean;
  spin: boolean;
};

export type QuantumStage = { task: string; fmax?: number; steps?: number };

export type QuantumDefaults = Pick<QuantumJobRequest, "charge" | "multiplicity" | "calculator" | "stages">;

/** The parts of aq's request JSON Schema (`aq job schema`) the panel reads: each stage task's field defaults. */
export type QuantumRequestSchema = {
  $defs?: Record<string, { properties?: Record<string, { default?: unknown }> }>;
  properties?: { stages?: { items?: { discriminator?: { mapping?: Record<string, string> } } } };
};

/** GET /api/providers/quantum/jobs/schema: the form data, or why jobs cannot run. */
export type QuantumSchema = { provider: string; label: string } & (
  | { enabled: false; reason: string }
  | {
    enabled: true;
    tasks: string[];
    models: QuantumModel[];
    defaults: QuantumDefaults;
    request_schema: QuantumRequestSchema;
  }
);

export type QuantumJobRequest = {
  schema: typeof QUANTUM_REQUEST_SCHEMA;
  charge: "formal" | number;
  multiplicity: number;
  calculator: string;
  stages: QuantumStage[];
};

export type QuantumForm = {
  calculation: QuantumCalculation;
  model: string;
  // Only a total charge: aq's "formal" needs hydrogen annotations that structures exported from Studio lack.
  charge: number;
  multiplicity: number;
  fmax: number;
  steps: number;
};

export type QuantumOutput = {
  id: string;
  label: string;
  kind: string;
  rank?: number;
  metrics: Record<string, unknown>;
  units?: Record<string, string>;
  artifacts?: Array<{ name: string; label: string }>;
};

export function isQuantumCalculation(value: string): value is QuantumCalculation {
  return Object.hasOwn(QUANTUM_CALCULATIONS, value);
}

/** Calculations whose aq tasks the provider offers. */
export function availableCalculations(tasks: readonly string[]): QuantumCalculation[] {
  const offered = new Set(tasks);
  return (Object.keys(QUANTUM_CALCULATIONS) as QuantumCalculation[]).filter((calculation) =>
    QUANTUM_CALCULATIONS[calculation].tasks.every((task) => offered.has(task))
  );
}

/** The form fields a provider's default request sets; its calculator device is left to aq ("auto"). */
export function formDefaults(defaults: QuantumDefaults): Partial<QuantumForm> {
  const form: Partial<QuantumForm> = { model: defaults.calculator.split("@")[0], multiplicity: defaults.multiplicity };
  if (typeof defaults.charge === "number") form.charge = defaults.charge;
  const tasks = defaults.stages.map((stage) => stage.task).join(" ");
  const calculation = (Object.keys(QUANTUM_CALCULATIONS) as QuantumCalculation[]).find(
    (candidate) => QUANTUM_CALCULATIONS[candidate].tasks.join(" ") === tasks,
  );
  if (calculation) {
    form.calculation = calculation;
    const { fmax, steps } = defaults.stages[0];
    if (fmax !== undefined) form.fmax = fmax;
    if (steps !== undefined) form.steps = steps;
  }
  return form;
}

/** aq's default force threshold and step count for a stage task, from its request schema. */
export function stageDefaults(schema: QuantumRequestSchema, task: string): Pick<QuantumStage, "fmax" | "steps"> {
  const reference = schema.properties?.stages?.items?.discriminator?.mapping?.[task] ?? "";
  const fields = schema.$defs?.[reference.replace("#/$defs/", "")]?.properties ?? {};
  const defaults: Pick<QuantumStage, "fmax" | "steps"> = {};
  for (const key of ["fmax", "steps"] as const) {
    const value = fields[key]?.default;
    if (typeof value === "number") defaults[key] = value;
  }
  return defaults;
}

/** Build an atomworks-quantum.job/1 request, or throw an Error that names the invalid field. */
export function buildQuantumRequest(form: QuantumForm): QuantumJobRequest {
  const recipe = QUANTUM_CALCULATIONS[form.calculation];
  if (!form.model) throw new Error("Choose a model.");
  if (!Number.isInteger(form.charge)) {
    throw new Error("Total charge must be a whole number.");
  }
  if (!Number.isInteger(form.multiplicity) || form.multiplicity < 1) {
    throw new Error("Multiplicity must be a whole number of at least 1.");
  }
  if (recipe.fmax && !(Number.isFinite(form.fmax) && form.fmax > 0)) {
    throw new Error("Force threshold must be greater than 0 eV/Å.");
  }
  if (recipe.steps && (!Number.isInteger(form.steps) || form.steps < 1)) {
    throw new Error("Steps must be a whole number of at least 1.");
  }
  const [first, ...rest] = recipe.tasks;
  const firstStage: QuantumStage = { task: first };
  if (recipe.fmax) firstStage.fmax = form.fmax;
  if (recipe.steps) firstStage.steps = form.steps;
  return {
    schema: QUANTUM_REQUEST_SCHEMA,
    charge: form.charge,
    multiplicity: form.multiplicity,
    calculator: form.model,
    stages: [firstStage, ...rest.map((task) => ({ task }))],
  };
}

/** One-line description of a model's backend, devices, and the electronic states it accepts. */
export function modelNote(model: QuantumModel): string {
  const states = model.charge && model.spin
    ? "any charge and multiplicity"
    : model.charge
    ? "singlets only"
    : model.spin
    ? "neutral only"
    : "neutral singlets only";
  return `${model.backend} · ${model.devices.join(", ")} · ${states}`;
}

export function formatEnergy(output: QuantumOutput): string | null {
  const energy = output.metrics.energy;
  return typeof energy === "number" ? `${energy.toFixed(4)} ${output.units?.energy ?? "eV"}` : null;
}

/** Download name of an artifact: its manifest name plus the file extension, e.g. "vibrations.result.json". */
export function artifactFileName(artifact: { name: string; label: string }): string {
  const dot = artifact.label.lastIndexOf(".");
  return dot > 0 ? `${artifact.name}${artifact.label.slice(dot)}` : artifact.name;
}

/**
 * Whether a job's status and outputs are final. A running job that is cancelled is marked cancelled at once, but
 * the stages it finished arrive only when its processes are gone, which completed_at marks.
 */
export function jobSettled(job: { status: string; completed_at?: number | null }): boolean {
  if (job.status === "queued" || job.status === "running") return false;
  return job.status !== "cancelled" || job.completed_at != null;
}

export function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}
