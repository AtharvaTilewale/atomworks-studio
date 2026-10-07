# Use Studio in a workflow

Use the editing library for Python code and the HTTP API when another process
owns the document. Neither interface needs a browser, Qt, a model runtime, or a
GPU. Both currently install AtomWorks 3's ML dependencies, including Torch.

## Python primitives

Install `atomworks-studio-editing` for base AtomWorks conditions. Add
`atomworks-annotation-studio-rfd4` for the same hotspot, group, and other condition
definitions that the full Studio installation uses. Until these packages are
published, install the wheels from a verified build with `pip --find-links`.

```python
from pathlib import Path
from atomworks_studio_editing.document import StudioDocument
from atomworks_studio_editing.providers import load_providers

providers = load_providers()
document = StudioDocument.from_cif(
    Path("input.cif").read_text(),
    condition_provider=providers.condition_provider,
    validation_provider=providers.validation_provider,
)
indices = document.query_indices("sel('*/*/*/CA')").tolist()
document.set_condition("sequence", indices)
Path("annotated.cif").write_text(document.to_cif())
```

`StudioDocument(array, ...)` also accepts an AtomWorks `AtomArrayPlus` directly.
Use one document per request or workflow. Mutations validate and create Undo
steps; `document.undo()` reverses the last one. `document.protonate(ph=7.4)`
updates hydrogens and formal charges. `to_cif()` serializes the current array.
Call `document.validate(full_pipeline=True)` and inspect its returned issues
when preparing model inputs; serialization alone is not a validation check.

The executable [annotation example](https://github.com/baker-laboratory/atomworks-studio/blob/main/examples/annotate.py)
uses these same calls. From the repository:

```bash
python examples/annotate.py examples/ppi_pdl1.cif annotated.cif \
  --query "sel('*/*/*/CA')" --condition hotspot
```

## HTTP and embedded editing

Start `atomworks-studio`. Create a session by uploading an annotated CIF to
`POST /api/v1/sessions` as multipart field `file`. The response contains a
`session_id` and an `editor_path`, such as `/?session=…&embed=1`. Open that path
on the same Studio server to edit the uploaded structure.

```bash
curl --fail -F file=@input.cif http://127.0.0.1:8000/api/v1/sessions
```

For that session, use:

| Method and path after `/api/v1/sessions/{session_id}` | Use |
| --- | --- |
| `POST /selections/query` | Resolve an AtomWorks expression or selection path. |
| `POST /conditions` | Apply an annotation to explicit atom indices. |
| `POST /protonate` | Assign hydrogens and formal charges at a given pH. |
| `POST /undo` | Revert the last edit. |
| `GET /cif` | Serialize the current annotated CIF. |
| `POST /export` | Validate and serialize one snapshot; return the CIF and validation issues. |

The running server exposes request schemas at `/docs` and `/openapi.json`.
For an iframe, allow the parent's exact origin with
`ANNOTATION_STUDIO_FRAME_ANCESTORS`. Sessions are bounded, expire, and use one
server process. Export files before submitting a long-running inference job.

## Hugging Face Spaces

The [Docker Space example](https://github.com/baker-laboratory/atomworks-studio/tree/main/examples/space)
serves the existing editor on port 7860, including this guide. It uses built
wheels, so the deployed container needs no Node, Rust, PyMOL, or separate frontend
build. It is an editor-only CPU recipe; it does not execute inference.
[Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker) can host a
custom server and use GPU hardware.

For a first model-enabled Space, put a small application beside Studio in the
**model's deployment repository**:

1. Let the user prepare a structure in the embedded editor.
2. Export its annotated CIF through the API and freeze that input for the run.
3. Validate the model-specific options and run the model with bounded concurrency.
4. Upload an output CIF to Studio and open the returned `editor_path` for inspection.

That deployment owns checkpoints, CUDA dependencies, model validation, progress,
cancellation, and output retention. Keep those out of the editing package.
For one GPU, start with a single queued worker and an explicit busy state.
Keep inference off Studio's API event loop so editing remains responsive.
Use the model's documented inference entry point and argument list, without a
shell command assembled from user input.

The earlier development code contains subprocess cancellation and result
collection that can be adapted after the model is chosen. Restoring the full
job-provider framework, GPU provisioning, and durable database into Studio is
unnecessary for this deployment. Preserve that code in development history.
A deployed model should have a real end-to-end inference test; a successful
Studio edit/export test does not establish model compatibility.

A GPU Docker Space is the most direct fit for the current FastAPI application.
[ZeroGPU](https://huggingface.co/docs/hub/spaces-zerogpu) currently requires Gradio;
it is not a hardware switch for this Docker recipe. For intermittent long jobs,
[Hugging Face Jobs](https://huggingface.co/docs/hub/jobs-overview) is another
execution option, with input/output persistence and status polling owned by the
model application. No Space, GPU allocation, or paid job is created by Studio.

### RFD4-Proteina deployment

The first target is [RosettaCommons/RFD4-Proteina](https://github.com/RosettaCommons/RFD4-Proteina).
At the inspected commit `66e4394d38a04a0b901a76507cd5528af2d7f845`, its
`rfd4-generate` command accepts an annotated CIF directly:

```bash
rfd4-generate inputs=/runs/job/input.cif num_replicates=1 \
  batch_size=1 out_dir=/runs/job/output
```

Use server-generated job paths. The runner should invoke this as an argument
list from the RFD4 checkout, capture its log and return code, and collect output
CIFs only after successful completion. Keep cancellation of the process group
and an execution timeout from the old runner. A single worker serializes GPU
use; the model process may load weights afresh for each run initially.

Use the public AtomWorks update in [RFD4 PR #5](https://github.com/RosettaCommons/RFD4-Proteina/pull/5)
(or a release containing it). Its manifest pins `atomworks[ml,openbabel]==3.0.0`,
matching Studio. At inspection, that PR was open and `main` still referenced the
older AtomWorks Git branch; do not mistake the two revisions for the same setup.

For this version, **one Python environment is the simplest starting point**.
Dependency resolution for RFD4 PR #5 at `e7b374c372ede770ac8d847adf8d72cdc02b4af8`
and Studio succeeded for Linux x86-64/Python 3.12.10, selecting AtomWorks 3.0.0,
Biotite 1.6.0, and Torch 2.13.0 with CUDA 13.2. This checks base package constraints,
not GPU execution or optional TMol/refolding extras. Install Studio into the
model's compatible environment; do not use the CPU Torch image above for inference.

Keep editing and generation in separate processes even when they share the
Python environment. The Space serves one public application port and its small
deployment app owns Run, progress, cancellation, and result loading. Studio's
own UI stays unchanged. The CIF/HTTP boundary also allows moving inference to
another machine later without changing the editor.

Follow RFD4's [installation instructions](https://github.com/RosettaCommons/RFD4-Proteina/blob/66e4394d38a04a0b901a76507cd5528af2d7f845/docs/installation.rst)
for its CUDA environment and checkpoint download (about 8 GB). The current
setup targets CUDA 13, so validate the Space GPU driver's compatibility before
choosing hardware. Checkpoint disk size does not establish the required VRAM;
measure a short monomer, then the intended PPI and enzyme workloads. Start with
plain generation; optional refolding oracles use additional containers and are
not part of this first Space recipe.

The upstream repository is private at the time of this inspection. A Space
build needs authorized source access until it is published; never put a GitHub
token into a Docker build argument or image layer. No model-enabled image or
GPU inference run is claimed by the editor-only recipe. The deployment still
needs GPU validation and a checked comparison of its outputs before release.

## NIM and other inference services

Use Studio before and after inference: export an input, call the service from
your backend, then import its structure output. Keep endpoint credentials on
the server. NIM APIs are model-specific; convert the input to the documented
schema and check which annotations the model actually consumes. Arbitrary
annotated CIF fields are not universally supported.

For example, consult the [OpenFold3 request examples](https://docs.nvidia.com/nim/bionemo/openfold3/latest/example-requests.html)
or [Boltz-2 inference guide](https://docs.nvidia.com/nim/bionemo/boltz2/latest/inference.html)
for the chosen service. Studio does not need an NVIDIA or Hugging Face SDK just
to prepare structures or inspect their outputs.
