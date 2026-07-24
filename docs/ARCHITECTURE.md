# Architecture

The FastAPI package owns annotated-CIF sessions and exposes versioned editor
operations. It depends on AtomWorks contracts and mutation primitives, but not
on RFD4 at import time. Optional provider packages register condition,
validation, and design capabilities.

The browser, PWA, VS Code extension, and Patinae desktop plugin are clients of
that single API. Browser viewer adapters implement the same structure,
selection, representation, camera, measurement, interaction, object, and
teardown contract. They do not own document truth.

Mutable session state is process-local and bounded. Production deployment must
run one API worker until a transactional shared store and distributed locking
are implemented. GPU work is a provider subprocess; the CPU editor remains
usable when no provider is installed.

RFD4 and AF3 use an explicit external checkout selected by
`ANNOTATION_STUDIO_RFD4_ROOT`. Local and Brev launchers share its inference
runtime helper, persistent caches, one-job GPU ownership, and daemon socket.
