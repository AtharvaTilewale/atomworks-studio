"""Shared RFD4 validation used by headless and PyMOL editors.

Vendored into atomworks-studio from RFD4-Proteina ``rfproteina.interactive_validation``
(RFD4 commit 0e9050ca, before its upstream removal; the submodule now tracks main).
Imports live ``rfproteina.conditions`` symbols; re-verify parity when bumping the RFD4 submodule.
"""

import numpy as np
from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from rfproteina.conditions import C_HB, HBondEnum


def validate_rfd4(array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]:
    """Return model-specific semantic and optional design-pipeline issues."""
    issues: list[dict[str, str]] = []
    hbond = C_HB.annotation(array, default="generate")
    if np.any(hbond == HBondEnum.BOTH):
        issues.append(
            {
                "severity": "error",
                "check": "hbond_both_not_supported",
                "message": "HBond BOTH is not currently supported by the model",
            }
        )
    if full_pipeline:
        from rfproteina.datapipes.pipeline import build_design_validation_pipeline

        try:
            build_design_validation_pipeline()({"atom_array": array.copy()})
        except Exception as error:  # pipeline aggregates domain-specific violations
            issues.append({"severity": "error", "check": "design_pipeline", "message": str(error)})
    return issues
