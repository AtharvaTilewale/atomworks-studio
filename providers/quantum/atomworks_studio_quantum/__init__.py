"""AtomWorks-Quantum job provider for Studio.

Studio never imports atomworks-quantum: each job is a directory with ``request.json`` and ``input.cif``
that ``aq job run`` (ANNOTATION_STUDIO_QUANTUM_COMMAND, in aq's own environment) turns into
``outputs/manifest.json``. The provider defines no document conditions.
"""

from __future__ import annotations

from pathlib import Path

from .jobs import PROVIDER_LABEL, QuantumJobProvider

__all__ = ["PROVIDER_LABEL", "create_job_provider"]


def create_job_provider(project_root: Path) -> QuantumJobProvider:
    """Create the quantum job provider from the ANNOTATION_STUDIO_QUANTUM_* environment."""

    del project_root
    return QuantumJobProvider.from_environment()
