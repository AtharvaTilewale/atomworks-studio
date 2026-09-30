# Architecture

The editing package owns annotated-CIF documents, inspection, mutation operations,
and transactional undo. FastAPI owns sessions and exposes those operations over
HTTP. The document package imports neither FastAPI nor renderer/model providers. Optional provider packages register condition,
validation, and design capabilities.

RFD4 owns ConditionSpec JSON/YAML and contig compilation, including inference
input semantics. Studio owns interactive editing and visualization of the
resulting annotated structures. There is no Studio copy of the compiler or
JSON round-trip editor. See [ConditionSpec ownership](INPUTS.md).

The browser, PWA, VS Code extension, and Patinae desktop plugin are clients of
that single API. Browser viewer adapters implement the same structure,
selection, representation, camera, measurement, interaction, object, and
teardown contract. They do not own document truth.

The PyMOL plugin is deliberately outside that client/server boundary. It runs
locally in PyMOL, keeps a shared `StudioDocument` per object in process, and reads
and writes annotated CIF files directly. Renderer identity maps and coordinate
synchronization remain in its bridge. It depends on AtomWorks and
`atomworks-studio-editing`, not on the FastAPI or web packages. Interoperability
with the website and RFD4 happens through the saved CIF format, not through a
running Studio service.

By default, mutable session state is process-local and bounded, and GPU work is
a provider subprocess. This remains appropriate for a trusted workstation or
single authenticated pilot VM, and the CPU editor remains usable when no model
provider is installed.

When `ANNOTATION_STUDIO_DATABASE_URL` and
`ANNOTATION_STUDIO_OBJECT_STORE_URL` are set, PostgreSQL becomes authoritative
for owner-scoped document snapshots (including bounded undo history), quotas,
jobs, worker capabilities, attempts, cancellation, and expiring leases. CIF
inputs/outputs and logs live in S3-compatible storage. API replicas are then
stateless and GPU workers scale independently, one process per GPU. See
[`deploy/durable`](../deploy/durable/README.md) for the trust boundary and
operations contract. The authentication proxy must remove the configured
identity header before injecting its stable user subject; the API hides another
user's session or job as not found.

RFD4 and AF3 use an explicit external checkout selected by
`ANNOTATION_STUDIO_RFD4_ROOT`. Local and Brev launchers share Studio's vendored
inference runtime helper, persistent caches, one-job GPU ownership, and daemon
socket.

## Browser GPU boundary

Patinae's WebGPU device is a renderer, not an inference runtime. A future local
browser model would be a separate optional design provider with its own model
manifest, operator implementation, memory qualification, numerical-parity
suite, cancellation, and output import. It must not make document editing or
rendering depend on model support.

The current RFD4 runtime is not a candidate for a transparent PyTorch-to-WebGPU
switch: its two pinned checkpoints alone exceed 8 GB, and its CUDA-oriented
PyTorch/compiled execution stack has no browser backend. A browser port is a
separate model-runtime project. Keep remote GPU execution as the production
path; prototype browser inference only after selecting a smaller model and
proving full-model parity on representative browser/GPU combinations.

See [the extraction inventory](REFACTOR.md) for ownership and acceptance gates.
