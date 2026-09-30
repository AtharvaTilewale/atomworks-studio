"""Exercise the installed PyMOL editor and canonical CPU save validation."""

import sys
from pathlib import Path


def main() -> None:
    import pymol

    pymol.finish_launching(["pymol", "-cqk"])
    from atomworks_studio_editing.document import StudioDocument
    from atomworks_studio_rfd4 import CONDITION_PROVIDER, VALIDATION_PROVIDER
    from atomworks_studio_rfd4.pymol.editors import register

    register()
    root = Path(__file__).resolve().parents[1]
    document = StudioDocument.from_cif(
        (root / "tests/fixtures/ppi_pdl1.cif").read_text(),
        condition_provider=CONDITION_PROVIDER,
        validation_provider=VALIDATION_PROVIDER,
    )
    from atomworks_studio_rfd4.interactive_validation import validate_rfd4

    issues = validate_rfd4(document.array, full_pipeline=True)
    if issues:
        raise RuntimeError(f"RFD4 installation failed the valid-input check: {issues}")
    document.array.coord[0] += 1000
    issues = validate_rfd4(document.array, full_pipeline=True)
    if not issues or any(issue["check"] in {"pipeline_error", "hook_error"} for issue in issues):
        raise RuntimeError(f"RFD4 installation failed the invalid-input check: {issues}")
    print("PyMOL editors loaded; canonical RFD4 validation accepts valid and rejects invalid structures.")


if __name__ == "__main__":
    # Embedded PyMOL's shutdown hooks can hide a Python failure from the shell.
    import os
    import traceback

    status = 0
    try:
        main()
    except Exception:
        traceback.print_exc()
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
