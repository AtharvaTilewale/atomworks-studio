"""Internal-coordinate conditioning as a lightweight Studio operation.

Reimplements ``rfproteina.transforms.utils.conditioning_utils.convert_to_internal_coords``
WITHOUT importing that module, whose import chain pulls the full RFD4 training runtime
(lightning / rootutils). The logic here is numpy plus atomworks' ``random_rigid_augmentation``
-- both importable in the CPU editor -- so internal-coordinate conditioning works in the web
API just like any other document operation, no GPU/model runtime required.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import torch
from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from atomworks.ml.conditions import C_CRD
from atomworks.ml.utils.geometry import random_rigid_augmentation
from rfproteina.conditions import C_CRDGRP, C_INTCRD


def convert_to_internal_coordinates(array: AtomArrayPlus, atom_indices: Sequence[int]) -> None:
    """Move the selected atoms from absolute to internal-coordinate conditioning, in place.

    Mirrors rfproteina's ``convert_to_internal_coords``: clears the absolute-coord mask
    (``C_CRD``) on the selection, assigns it a fresh coordinate group (``C_CRDGRP``), and
    stores its centered + randomly rotated ground-truth coordinates as the internal-coord
    annotation (``C_INTCRD``). Atoms with non-finite coordinates are skipped. The random
    rotation only strips the absolute frame -- internal geometry (which the model conditions
    on) is rotation-invariant -- so this matches the model's expectation exactly.
    """
    mask = np.zeros(len(array), dtype=bool)
    mask[list(atom_indices)] = True
    mask &= np.isfinite(array.coord).all(axis=1)
    if not mask.any():
        # Same contract as the PyMOL editor's warning: internal coordinates are derived from
        # the atoms' own positions, so a selection with no resolved coordinates cannot convert.
        raise ValueError("no selected atoms have resolved coordinates to convert to internal coordinates")

    # Absolute coord conditioning is turned off for the new internal-coord region.
    crd_mask = C_CRD.mask(array, default="generate")
    C_CRD.set_mask(array, crd_mask & ~mask)
    if C_CRD.full_name in array.get_annotation_categories():
        crd_annotation = C_CRD.annotation(array, default="raise")
        crd_annotation[mask, :] = np.nan
        C_CRD.set_annotation(array, crd_annotation)

    # Fresh coordinate group so the region is treated as internally consistent.
    groups = C_CRDGRP.annotation(array, default="generate")
    max_group = groups.max()
    groups[mask] = max_group + 1 if max_group > 0 else 1
    C_CRDGRP.set_annotation(array, groups)

    # Turn on the internal-coord mask for the region.
    intcrd_mask = C_INTCRD.mask(array, default="generate")
    C_INTCRD.set_mask(array, intcrd_mask | mask)

    # Store the centered + randomly rotated ground-truth coordinates.
    intcrd_annotation = C_INTCRD.annotation(array, default="generate")
    coords = array.coord[mask, :]
    centered = coords - coords.mean(axis=0, keepdims=True)
    rotated = random_rigid_augmentation(torch.from_numpy(centered)[None], batch_size=1, s=0)[0].numpy()
    intcrd_annotation[mask, :] = rotated
    C_INTCRD.set_annotation(array, intcrd_annotation)
