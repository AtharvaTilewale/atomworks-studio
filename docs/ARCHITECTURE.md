# Architecture

The FastAPI package owns annotated-CIF sessions and exposes versioned editor
operations. It depends on AtomWorks contracts and mutation primitives, but not
on RFD4 at import time. Optional provider packages register condition,
validation, and design capabilities.

The browser, PWA, VS Code extension, and Patinae desktop plugin are clients of
that single API. Browser viewer adapters implement the same structure,
selection, representation, camera, measurement, interaction, object, and
teardown contract. They do not own document truth.

The PyMOL plugin is deliberately outside that client/server boundary. It runs
locally in PyMOL, keeps its authoritative `AtomArrayPlus` in process, and reads
and writes annotated CIF files directly. It depends on AtomWorks and
`atomworks-studio-editing`, not on the FastAPI or web packages. Interoperability
with the website and RFD4 happens through the saved CIF format, not through a
running Studio service.

Mutable session state is process-local and bounded. Production deployment must
run one API worker until a transactional shared store and distributed locking
are implemented. GPU work is a provider subprocess; the CPU editor remains
usable when no provider is installed.

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
