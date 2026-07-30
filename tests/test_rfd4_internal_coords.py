"""Conformance checks for the lightweight RFD4 internal-coordinate adapter."""

from pathlib import Path

import numpy as np
import pytest
from annotation_studio.core import StudioDocument
from annotation_studio.rfd4_provider import CONDITION_PROVIDER, VALIDATION_PROVIDER
from atomworks.ml.conditions import C_CRD
from atomworks_studio_rfd4.internal_coords import convert_to_internal_coordinates
from rfproteina.conditions import C_CRDGRP, C_INTCRD

FIXTURE = Path(__file__).parent / "fixtures" / "SAM_hbonds.cif"


def _array():
    document = StudioDocument.from_cif(
        FIXTURE.read_text(),
        FIXTURE.stem,
        condition_provider=CONDITION_PROVIDER,
        validation_provider=VALIDATION_PROVIDER,
    )
    return document.array


def test_internal_coordinate_adapter_preserves_upstream_invariants() -> None:
    array = _array()
    resolved = np.flatnonzero(np.isfinite(array.coord).all(axis=1))
    selected = resolved[: min(8, len(resolved))]
    assert len(selected) > 1

    original_coords = array.coord.copy()
    absolute_mask = np.zeros(len(array), dtype=bool)
    absolute_mask[selected] = True
    C_CRD.set_mask(array, absolute_mask)
    C_CRD.set_annotation(array, original_coords.copy())
    previous_groups = C_CRDGRP.annotation(array, default="generate").copy()

    convert_to_internal_coordinates(array, selected)

    assert not C_CRD.mask(array)[selected].any()
    assert np.isnan(C_CRD.annotation(array)[selected]).all()
    assert C_INTCRD.mask(array)[selected].all()

    groups = C_CRDGRP.annotation(array)
    assert np.unique(groups[selected]).size == 1
    assert groups[selected[0]] > previous_groups.max()

    internal_coords = C_INTCRD.annotation(array)[selected]
    np.testing.assert_allclose(internal_coords.mean(axis=0), 0, atol=1e-5)
    np.testing.assert_allclose(
        np.linalg.norm(internal_coords[:, None] - internal_coords[None, :], axis=-1),
        np.linalg.norm(original_coords[selected, None] - original_coords[None, selected], axis=-1),
        atol=1e-4,
    )
    np.testing.assert_array_equal(array.coord, original_coords)


def test_internal_coordinate_adapter_rejects_a_fully_unresolved_selection() -> None:
    array = _array()
    selected = np.array([0, 1])
    array.coord[selected] = np.nan

    with pytest.raises(ValueError, match="no selected atoms have resolved coordinates"):
        convert_to_internal_coordinates(array, selected)
