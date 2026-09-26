import assert from "node:assert/strict";
import test from "node:test";

import {
  artifactFileName,
  availableCalculations,
  buildQuantumRequest,
  formDefaults,
  formatEnergy,
  jobSettled,
  modelNote,
  stageDefaults,
  type QuantumForm,
} from "../src/studio/quantum-request.ts";

const form: QuantumForm = {
  calculation: "optimize-frequencies",
  model: "gfn2-xtb",
  charge: 0,
  multiplicity: 1,
  fmax: 0.02,
  steps: 500,
};

test("optimize + frequencies passes fmax and steps to the optimization only", () => {
  assert.deepEqual(buildQuantumRequest(form), {
    schema: "atomworks-quantum.job/1",
    charge: 0,
    multiplicity: 1,
    calculator: "gfn2-xtb",
    stages: [{ task: "optimize", fmax: 0.02, steps: 500 }, { task: "frequencies" }],
  });
});

test("each calculation sends only the parameters its stage takes", () => {
  const stages = (calculation: QuantumForm["calculation"]) =>
    buildQuantumRequest({ ...form, calculation }).stages;
  assert.deepEqual(stages("singlepoint"), [{ task: "singlepoint" }]);
  assert.deepEqual(stages("optimize"), [{ task: "optimize", fmax: 0.02, steps: 500 }]);
  assert.deepEqual(stages("find_ts"), [{ task: "find_ts", fmax: 0.02, steps: 500 }]);
  assert.deepEqual(stages("md"), [{ task: "md", steps: 500 }]);
});

test("the total charge and multiplicity are sent as given", () => {
  const request = buildQuantumRequest({ ...form, charge: -1, multiplicity: 2 });
  assert.equal(request.charge, -1);
  assert.equal(request.multiplicity, 2);
});

test("invalid fields are refused with the field name", () => {
  assert.throws(() => buildQuantumRequest({ ...form, model: "" }), /Choose a model/);
  assert.throws(() => buildQuantumRequest({ ...form, charge: 0.5 }), /Total charge must be a whole number/);
  assert.throws(() => buildQuantumRequest({ ...form, charge: Number.NaN }), /Total charge/);
  assert.throws(() => buildQuantumRequest({ ...form, multiplicity: 0 }), /Multiplicity/);
  assert.throws(() => buildQuantumRequest({ ...form, fmax: 0 }), /Force threshold/);
  assert.throws(() => buildQuantumRequest({ ...form, steps: 1.5 }), /Steps/);
  // Parameters the calculation does not use are not checked.
  assert.doesNotThrow(() => buildQuantumRequest({ ...form, calculation: "singlepoint", fmax: Number.NaN, steps: 0 }));
});

test("calculations are offered only when the provider offers all of their tasks", () => {
  assert.deepEqual(
    availableCalculations(["singlepoint", "optimize", "frequencies", "md"]),
    ["singlepoint", "optimize", "optimize-frequencies", "md"],
  );
  assert.deepEqual(availableCalculations(["optimize"]), ["optimize"]);
});

test("results show energy with its unit, model notes, and unique artifact names", () => {
  assert.equal(formatEnergy({ id: "00-optimize", label: "", kind: "optimize", metrics: { energy: -137.97123 } }), "-137.9712 eV");
  assert.equal(
    formatEnergy({ id: "a", label: "", kind: "optimize", metrics: { energy: -5 }, units: { energy: "Ha" } }),
    "-5.0000 Ha",
  );
  assert.equal(formatEnergy({ id: "a", label: "", kind: "protonate", metrics: {} }), null);
  assert.equal(
    modelNote({ name: "mace-off23-small", backend: "mace", devices: ["cpu", "cuda"], charge: false, spin: false }),
    "mace · cpu, cuda · neutral singlets only",
  );
  assert.equal(
    modelNote({ name: "gfn2-xtb", backend: "tblite", devices: ["cpu"], charge: true, spin: true }),
    "tblite · cpu · any charge and multiplicity",
  );
  assert.equal(artifactFileName({ name: "result", label: "result.json" }), "result.json");
  assert.equal(artifactFileName({ name: "vibrations.result", label: "result.json" }), "vibrations.result.json");
  assert.equal(artifactFileName({ name: "log", label: "log" }), "log");
});

test("the provider's default request fills the form", () => {
  assert.deepEqual(
    formDefaults({ charge: 0, multiplicity: 1, calculator: "gfn2-xtb", stages: [{ task: "singlepoint" }] }),
    { model: "gfn2-xtb", charge: 0, multiplicity: 1, calculation: "singlepoint" },
  );
  assert.deepEqual(
    formDefaults({
      charge: "formal",
      multiplicity: 2,
      calculator: "mace-off23-small@cuda",
      stages: [{ task: "optimize", fmax: 0.05, steps: 200 }, { task: "frequencies" }],
    }),
    { model: "mace-off23-small", multiplicity: 2, calculation: "optimize-frequencies", fmax: 0.05, steps: 200 },
    "a default of formal charges leaves the total charge field as it is",
  );
  // Stages the panel has no calculation for leave the calculation unchanged.
  assert.equal(
    formDefaults({ charge: 0, multiplicity: 1, calculator: "gfn2-xtb", stages: [{ task: "protonate" }] }).calculation,
    undefined,
  );
});

test("fmax and steps start from aq's defaults for the stage task", () => {
  // The shape of `aq job schema`: a discriminated stage union with one $defs entry per task.
  const schema = {
    $defs: {
      FindTsStage: { properties: { task: { default: "find_ts" }, fmax: { default: 0.01 }, steps: { default: 1000 } } },
      MdStage: { properties: { temperature: { default: 300 }, steps: { default: 1000 } } },
      SinglepointStage: { properties: { task: { default: "singlepoint" } } },
    },
    properties: {
      stages: {
        items: {
          discriminator: {
            mapping: {
              find_ts: "#/$defs/FindTsStage",
              md: "#/$defs/MdStage",
              singlepoint: "#/$defs/SinglepointStage",
            },
          },
        },
      },
    },
  };
  assert.deepEqual(stageDefaults(schema, "find_ts"), { fmax: 0.01, steps: 1000 });
  assert.deepEqual(stageDefaults(schema, "md"), { steps: 1000 });
  assert.deepEqual(stageDefaults(schema, "singlepoint"), {});
  assert.deepEqual(stageDefaults(schema, "optimize"), {});
  assert.deepEqual(stageDefaults({}, "optimize"), {});
});

test("a cancelled job settles only once its outputs are collected", () => {
  assert.equal(jobSettled({ status: "queued", completed_at: null }), false);
  assert.equal(jobSettled({ status: "running", completed_at: null }), false);
  assert.equal(jobSettled({ status: "cancelled", completed_at: null }), false);
  assert.equal(jobSettled({ status: "cancelled", completed_at: 1_700_000_000 }), true);
  assert.equal(jobSettled({ status: "completed", completed_at: 1_700_000_000 }), true);
  assert.equal(jobSettled({ status: "failed" }), true);
});
