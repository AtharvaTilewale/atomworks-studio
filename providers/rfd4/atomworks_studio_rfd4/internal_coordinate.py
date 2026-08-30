"""Mask/group-only authoring for RFD4 internal-coordinate conditioning."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from rfproteina.conditions import set_internal_coord_conditioning


def set_internal_coordinate_conditioning(
    array: AtomArrayPlus,
    atom_indices: Iterable[int],
    enabled: bool,
) -> None:
    """Set mask-authored internal-coordinate conditioning on selected atoms.

    Editors intentionally do not write ``C_INTCRD`` values.  RFD4's
    featurization pipeline derives them from the current coordinates, centers
    and rotates each coordinate group, and overwrites any stale values.
    """
    mask = np.zeros(len(array), dtype=bool)
    mask[list(atom_indices)] = True
    set_internal_coord_conditioning(array, mask, enabled, require_nonempty=enabled)
