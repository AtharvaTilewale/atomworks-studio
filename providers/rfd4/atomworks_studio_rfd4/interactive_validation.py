"""Editor diagnostics and process-isolated, canonical RFD4 pre-save validation.

Full validation delegates to RFD4's own reporting functions: the same fixed-seed
pipeline and index checks used by its PyMOL plugin, with their message order and
check codes preserved. The private pipe carries an exact array, not a CIF that
could round coordinates, drop custom annotations, or repair an invalid input.
"""

import json
import os
import pickle
import re
import shlex
import subprocess
import sys
from pathlib import Path

import numpy as np
from atomworks.io.utils.atom_array_plus import AtomArrayPlus

from atomworks_studio_rfd4.model import C_HB, HBondEnum, source_root


def validate_rfd4(array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]:
    """Return quick diagnostics, or the upstream plugin's complete model checks."""
    if full_pipeline:
        return validate_model_pipeline(array, include_index_checks=True)
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
    return issues


def validate_model_pipeline(array: AtomArrayPlus, *, include_index_checks: bool = False) -> list[dict[str, str]]:
    """Run the optional model pipeline without loading it into the editor process."""
    command = shlex.split(os.environ.get("ANNOTATION_STUDIO_RFD4_VALIDATION_PYTHON", sys.executable))
    command.extend([str(Path(__file__).with_name("_validate.py")), str(source_root().parent)])
    if include_index_checks:
        command.append("--pre-save")
    try:
        result = subprocess.run(
            command,
            input=pickle.dumps(array, protocol=5),
            capture_output=True,
            timeout=120,
            check=True,
        )
        messages = json.loads(result.stdout)
        if not isinstance(messages, list) or any(not isinstance(message, str) for message in messages):
            raise ValueError("RFD4 validation returned an invalid report")
        issues = []
        for message in messages:
            match = re.fullmatch(r"\[([^\]]+)\]\s*(.*)", message, flags=re.DOTALL)
            if match is None:
                raise ValueError("RFD4 validation returned an untagged report")
            issues.append({"severity": "error", "check": match[1], "message": match[2]})
        return issues
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        message = (
            error.stderr.decode(errors="replace").strip()
            if isinstance(error, subprocess.CalledProcessError) and error.stderr
            else str(error)
        )
        return [{"severity": "error", "check": "pipeline_error", "message": message}]
