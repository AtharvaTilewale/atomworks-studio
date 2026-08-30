"""RFD4-specific providers for the otherwise model-neutral Studio API."""

from __future__ import annotations

import os
from pathlib import Path

from atomworks.io.utils.atom_array_plus import AtomArrayPlus

from atomworks_studio_rfd4._bootstrap import install_lightweight_rfproteina_stub

# The stub below is what lets rfproteina.conditions import without RFD4's torch-heavy
# package init on main. RFPROTEINA_LIGHTWEIGHT_IMPORT is set only as a harmless fallback
# for RFD4 revisions that still honor it (main ignores it).
os.environ.setdefault("RFPROTEINA_LIGHTWEIGHT_IMPORT", "1")
install_lightweight_rfproteina_stub()

# Imports below intentionally follow the runtime setup above (env + rfproteina stub).
from annotation_studio.contracts import (  # noqa: E402
    ConditionDefinition,
    ConditionOperation,
    ConditionProvider,
    ValidationProvider,
)
from annotation_studio.design_jobs import ProcessDesignProvider  # noqa: E402
from rfproteina.conditions_reporting import sync_index_groups  # noqa: E402
from rfproteina.utils import standard_annotations as _rfd4_standard_annotations  # noqa: E402, F401

from .condition_contract import CONDITION_DEFINITIONS, CONDITION_DEFINITIONS_BY_NAME  # noqa: E402
from .interactive_validation import validate_rfd4  # noqa: E402
from .internal_coordinate import set_internal_coordinate_conditioning  # noqa: E402


class RFD4ConditionProvider(ConditionProvider):
    """Expose RFD4's shared annotation editing contract to Studio."""

    definitions: tuple[ConditionDefinition, ...] = CONDITION_DEFINITIONS
    definitions_by_name = CONDITION_DEFINITIONS_BY_NAME

    def after_condition_update(self, name: str, array: AtomArrayPlus) -> None:
        if name == "index":
            sync_index_groups(array)


class RFD4ValidationProvider(ValidationProvider):
    """RFD4 semantic and optional full-pipeline validation."""

    def validate(self, array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]:
        return validate_rfd4(array, full_pipeline=full_pipeline)


CONDITION_PROVIDER = RFD4ConditionProvider()
VALIDATION_PROVIDER = RFD4ValidationProvider()

# Named coupled operations exposed by the API. Internal-coordinate values are
# deliberately left for RFD4 featurization to materialize.
CONDITION_OPERATIONS: dict[str, ConditionOperation] = {"internal_coordinate": set_internal_coordinate_conditioning}


def create_design_provider(project_root: Path) -> ProcessDesignProvider:
    """Create the process-isolated RFD4 design provider."""

    return ProcessDesignProvider.from_environment(project_root)
