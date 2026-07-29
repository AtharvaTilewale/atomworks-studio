"""Tools for atom and segment selection on ``AtomArray`` and ``AtomArrayStack``.

Provides helpers to compute segment boundaries and apply expressive selection syntax to structures.

Key public objects:
- :py:class:`~atomworks_studio_editing.selection.AtomSelection`
- :py:class:`~atomworks_studio_editing.selection.AtomSelectionStack`

See individual docstrings for usage and examples.
"""

__all__ = ["AtomSelection", "AtomSelectionStack"]

import re
from functools import reduce
from itertools import product
from typing import Any

import numpy as np
from biotite.structure import AtomArray, AtomArrayStack


class AtomSelection:
    """Represent a selection of atoms in a molecular structure."""

    def __init__(
        self,
        chain_id: str = "*",
        res_name: str = "*",
        res_id: int | str = "*",
        atom_name: str = "*",
        transformation_id: int | str = "*",
    ):
        """Initialize a selection.

        Args:
          chain_id: Chain identifier or ``"*"`` for any. Defaults to ``"*"``.
          res_name: Residue name or ``"*"`` for any. Defaults to ``"*"``.
          res_id: Residue index (integer) or ``"*"`` for any. Defaults to ``"*"``.
          atom_name: Atom name or ``"*"`` for any. Defaults to ``"*"``.
          transformation_id: Transformation id or ``"*"`` for any. Defaults to ``"*"``.
        """
        self.chain_id = chain_id
        self.res_name = res_name
        self.atom_name = atom_name
        self.res_id = int(res_id) if res_id != "*" else res_id
        self.transformation_id = str(transformation_id)

    def __str__(self) -> str:
        parts = [self.chain_id, self.res_name, str(self.res_id), self.atom_name, str(self.transformation_id)]

        # Remove trailing '*' values
        while parts and parts[-1] == "*":
            parts.pop()

        return "/".join(parts)

    def __repr__(self) -> str:
        return str(self)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            # Convert the string to an AtomSelection for comparison
            other = self.from_selection_str(other)

        if not isinstance(other, AtomSelection):
            return False

        return (
            self.chain_id == other.chain_id
            and self.res_name == other.res_name
            and self.res_id == other.res_id
            and self.atom_name == other.atom_name
            and self.transformation_id == other.transformation_id
        )

    @classmethod
    def from_selection_str(cls, selection_string: str) -> "AtomSelection":
        """Create a selection from ``CHAIN/RES/RESID/ATOM/TRANSFORM`` syntax.

        ``"*"`` acts as a wildcard for any field. Trailing fields may be omitted
        and default to ``"*"``.

        Examples:
            >>> # Selects the CA atom of the ALA residue at chain A, residue index 1
            >>> AtomSelection.from_selection_str("A/ALA/1/CA")

            >>> # Selects the CB atom of the ALA residue in any chain at any residue index
            >>> AtomSelection.from_selection_str("*/ALA/*/CB")

            >>> # Selects all atoms of the ALA residue at chain A
            >>> AtomSelection.from_selection_str("A/ALA/")

            >>> # Selects the CA atom of the ALA residue at chain A, residue index 1, transformation index 1
            >>> AtomSelection.from_selection_str("A/ALA/1/CA/1")

        """
        selection = parse_selection_string(selection_string)
        return cls(
            chain_id=selection.chain_id,
            res_name=selection.res_name,
            res_id=selection.res_id,
            atom_name=selection.atom_name,
            transformation_id=selection.transformation_id,
        )

    @classmethod
    def from_pymol_str(cls, pymol_string: str) -> "AtomSelection":
        """Create a selection from a PyMOL atom label string.

        PyMOL strings are of the form ``CHAIN/RES`RESID/ATOM`` and do not support
        ``transformation_id``. ``"*"`` may be used as a wildcard.

        PyMOL strings do not support transformation_id.

        We introduce to default PyMOL syntax the "*" operator as a wildcard to select all atoms in a given granularity.

        Example:
            >>> # Selects the OD2 atom of the ASP residue at chain A, residue index 37
            >>> AtomSelection.from_pymol_str("A/ASP`37/OD2")
        """
        selection = parse_pymol_string(pymol_string)
        return cls(
            chain_id=selection.chain_id,
            res_name=selection.res_name,
            res_id=selection.res_id,
            atom_name=selection.atom_name,
        )

    def get_mask(self, atom_array: AtomArray) -> np.ndarray:
        """Create a boolean mask using this AtomSelection on an AtomArray."""
        return get_mask_from_atom_selection(atom_array, self)

    def get_idxs(self, atom_array: AtomArray) -> np.ndarray:
        """Get the indices of atoms selected by this AtomSelection."""
        return np.where(self.get_mask(atom_array))[0]


def parse_selection_string(selection_string: str) -> AtomSelection:
    """Parse ``CHAIN/RES/RESID/ATOM/TRANSFORM`` into an :py:class:`AtomSelection`.

    ``"*"`` acts as a wildcard for any field. Trailing fields may be omitted
    and default to ``"*"``.

    See Also:
      :py:meth:`~atomworks_studio_editing.selection.AtomSelection.from_selection_str`
    """
    granularity_tiers = ["chain_id", "res_name", "res_id", "atom_name", "transformation_id"]
    values = selection_string.split("/")

    # Create a dictionary with available tiers and values
    selection_dict = {tier: value for tier, value in zip(granularity_tiers, values, strict=False) if value != "*"}

    return AtomSelection(**selection_dict)


def parse_pymol_string(pymol_string: str) -> AtomSelection:
    """Parse a PyMOL string ``CHAIN/RES`RESID/ATOM`` into an :py:class:`AtomSelection`.

    PyMOL selection strings are of the form: CHAIN_ID/RES_NAME`RES_ID/ATOM_NAME

    PyMOL selection strings do not support transformation_id.

    See Also:
      :py:meth:`~atomworks_studio_editing.selection.AtomSelection.from_pymol_str`
    """
    # Replace backtick with slash to standardize the format
    standardized_string = pymol_string.replace("`", "/")
    return parse_selection_string(standardized_string)


def get_mask_from_selection_string(atom_array: AtomArray, selection_string: str) -> np.ndarray:
    """Create a boolean mask from an AtomArray sequence selection string.

    Selection strings follow ``CHAIN/RES/RESID/ATOM/TRANSFORM`` with ``"*"`` as a
    wildcard for any field. Trailing fields may be omitted.

    Example:
        >>> atom_array = AtomArray(...)
        >>> mask = get_mask_from_selection_string(atom_array, "A/ALA/1/CA")
        [False, True, False, False, ...]

    See Also:
      :py:func:`~atomworks_studio_editing.selection.parse_selection_string`
    """
    return get_mask_from_atom_selection(atom_array, parse_selection_string(selection_string))


def get_mask_from_atom_selection(atom_array: AtomArray, atom_selection: AtomSelection) -> np.ndarray:
    """Create a boolean mask from an :py:class:`AtomSelection`.

    See Also:
      :py:func:`~atomworks_studio_editing.selection.parse_selection_string`
    """
    # TODO: Refactor using AtomArray query syntax
    mask = np.ones(atom_array.array_length(), dtype=bool)

    # ... add the masks
    if atom_selection.chain_id and atom_selection.chain_id != "*":
        mask &= atom_array.chain_id == atom_selection.chain_id

    if atom_selection.res_name and atom_selection.res_name != "*":
        mask &= atom_array.res_name == atom_selection.res_name

    if atom_selection.res_id and atom_selection.res_id != "*":
        mask &= atom_array.res_id == atom_selection.res_id

    if atom_selection.atom_name and atom_selection.atom_name != "*":
        mask &= atom_array.atom_name == atom_selection.atom_name

    if atom_selection.transformation_id and atom_selection.transformation_id != "*":
        mask &= atom_array.transformation_id == atom_selection.transformation_id

    if not np.any(mask):
        raise ValueError(f"No atoms found for selection: {atom_selection}")

    return mask


class AtomSelectionStack:
    """Manage multiple :py:class:`AtomSelection` objects as a unioned query.

    Supports ranges and comma-separated tokens via :py:meth:`from_query` and
    contiguous ranges via :py:meth:`from_contig`.

    See Also:
      :py:meth:`~atomworks_studio_editing.selection.AtomSelectionStack.from_query`,
      :py:meth:`~atomworks_studio_editing.selection.AtomSelectionStack.from_contig`
    """

    def __init__(self, selections: list[AtomSelection]):
        """Initialize a stack of selections.

        Args:
          selections: Sequence of selections to be unioned.
        """
        self.selections = selections

    @classmethod
    def from_contig(cls, contig: str) -> "AtomSelectionStack":
        """Create a stack from contiguous residue ranges.

        Contig strings specify inclusive residue index ranges, e.g. ``"A1-2"``
        or ``"A1-2, B3-10"``.

        Args:
          contig: Contiguous residue selection string like ``"A1-2, B3-10"``.

        Examples:
            >>> # Selects residues 1..2 in chain A
            >>> AtomSelectionStack.from_contig("A1-2")
            >>> # Selects residues 1..2 in chain A and 3..10 in chain B
            >>> AtomSelectionStack.from_contig("A1-2, B3-10")

        See Also:
          :py:meth:`~atomworks_studio_editing.selection.AtomSelectionStack.from_query`
        """
        # First define a regex that matches the elements of the contig string
        CONTIG_REGEX = re.compile(r"([A-Za-z]+)(\d+)-(\d+)")  # noqa
        selections = []
        for selection in contig.replace(" ", "").split(","):
            match = CONTIG_REGEX.match(selection)
            if not match:
                raise ValueError(f"Invalid contig string: {selection}")
            chain_id, start, stop = match.groups()
            # Create a new AtomSelection for each match
            for i in range(int(start), int(stop) + 1):
                # Create a new AtomSelection for each residue in the range
                atom_selection = AtomSelection(chain_id=chain_id, res_id=i)
                selections.append(atom_selection)
        return cls(selections)

    @classmethod
    def from_query(cls, query: str | list[str]) -> "AtomSelectionStack":
        """Create a stack from extended query syntax with ranges.

        Extended syntax overview:
        - **Chains**: ``A`` (all atoms in chain A), ``A/ALA`` (all ALA in chain A)
        - **Ranges (``res_id`` only)**: ``A/*/5-10`` selects residues 5..10 in chain A

        Grammar per field (``CHAIN/RES/RESID/ATOM/TRANSFORM``):
        - ``"*"`` wildcard
        - Exact value, e.g. ``"A"``, ``"ALA"``, ``"CA"``
        - Range (``res_id`` only): ``"5-10"`` (inclusive)

        Notes:
        - Fields are in order: CHAIN_ID/RES_NAME/RES_ID/ATOM_NAME/TRANSFORMATION_ID
        - Wildcard is "*". Missing trailing fields default to "*".
        - Multiple comma-separated tokens are combined by union.

        Multiple tokens may be provided as a comma-separated string or ``list[str]``.

        Examples:
            >>> # Selects residues 5..10 in chain A
            >>> AtomSelectionStack.from_query("A/*/5-10")
            >>> # Selects residues 5..10 in chain A and 3..10 in chain B
            >>> AtomSelectionStack.from_query("A/*/5-10, B/*/3-10")
            >>> # Selects residues 5..10 in chain A and 3..10 in chain B
            >>> AtomSelectionStack.from_query(["A/*/5-10", "B/*/3-10"])
        """
        tokens = cls._parse_query_tokens(query)
        selections: list[AtomSelection] = []

        for token in tokens:
            field_values = cls._parse_token_fields(token)
            token_selections = cls._build_selections_from_fields(field_values)
            selections.extend(token_selections)

        return cls(selections)

    @classmethod
    def _parse_query_tokens(cls, query: str | list[str]) -> list[str]:
        """Parse query input into individual tokens."""
        if isinstance(query, str):
            return [tok.strip() for tok in query.split(",") if tok.strip()]
        else:
            return [tok.strip() for tok in query if tok and tok.strip()]

    @classmethod
    def _parse_token_fields(cls, token: str) -> dict[str, list[Any]]:
        """Parse a single token into field values."""
        parts = token.split("/")

        # Ensure five fields with '*' defaults
        while len(parts) < 5:
            parts.append("*")
        chain_val, res_name_val, res_id_val, atom_name_val, trans_id_val = parts[:5]

        return {
            "chain_id": cls._parse_field_value(chain_val, is_res_id=False),
            "res_name": cls._parse_field_value(res_name_val, is_res_id=False),
            "res_id": cls._parse_field_value(res_id_val, is_res_id=True),
            "atom_name": cls._parse_field_value(atom_name_val, is_res_id=False),
            "transformation_id": cls._parse_field_value(trans_id_val, is_res_id=False),
        }

    @classmethod
    def _parse_field_value(cls, value: str, *, is_res_id: bool = False) -> list[Any]:
        """Parse a field value into a list of options.

        For ``res_id``, values are integers; for others, strings.
        """
        v = value.strip()
        if v == "*" or v == "":
            return ["*"]

        return cls._extract_field_options(v, is_res_id=is_res_id)

    @classmethod
    def _extract_field_options(cls, value: str, *, is_res_id: bool = False) -> list[Any]:
        """Extract options from a field value (ranges or scalars)."""
        # Range syntax: 5-10 (res_id only)
        if is_res_id and re.fullmatch(r"-?\d+-?\d+", value):
            start_s, stop_s = value.split("-", 1)
            start_i, stop_i = int(start_s), int(stop_s)
            step = 1 if start_i <= stop_i else -1
            return list(range(start_i, stop_i + step, step))

        # Scalar value
        return [int(value)] if is_res_id and value not in ("*", "") else [value]

    @classmethod
    def _build_selections_from_fields(cls, field_values: dict[str, list[Any]]) -> list[AtomSelection]:
        """Build selections from parsed field values, expanding sets and ranges."""
        # Extract field values directly as lists
        chain_vals = field_values["chain_id"]
        resn_vals = field_values["res_name"]
        resi_vals = field_values["res_id"]
        atom_vals = field_values["atom_name"]
        tran_vals = field_values["transformation_id"]

        # Build selections via Cartesian product
        selections = [
            AtomSelection(
                chain_id=c if c != "*" else "*",
                res_name=r if r != "*" else "*",
                res_id=i if i != "*" else "*",
                atom_name=a if a != "*" else "*",
                transformation_id=t if t != "*" else "*",
            )
            for c, r, i, a, t in product(chain_vals, resn_vals, resi_vals, atom_vals, tran_vals)
        ]

        return selections

    def get_mask(self, atom_array: AtomArray | AtomArrayStack) -> np.ndarray:
        """Create a boolean mask by unioning all selections."""
        if not self.selections:
            return np.zeros(atom_array.array_length(), dtype=bool)

        masks = [selection.get_mask(atom_array) for selection in self.selections]
        return reduce(np.logical_or, masks)

    def get_center_of_mass(self, atom_array: AtomArray | AtomArrayStack) -> np.ndarray:
        """Return the center of mass of the selected atoms.

        Returns:
          For :py:class:`~biotite.structure.AtomArray`: ``(3,)`` array.
          For :py:class:`~biotite.structure.AtomArrayStack`: ``(n_models,)`` array of means.

        Raises:
          ValueError: If no atoms are selected.
        """
        mask = self.get_mask(atom_array)
        if not np.any(mask):
            raise ValueError("No atoms selected by the AtomSelectionStack.")

        if isinstance(atom_array, AtomArray):
            return atom_array.coord[mask].mean(axis=0)
        elif isinstance(atom_array, AtomArrayStack):
            return atom_array.coord[:, mask].mean(axis=1)
        else:
            raise ValueError(f"Cannot get center of mass for {type(atom_array)}!")

    def get_principal_components(self, atom_array: AtomArray | AtomArrayStack) -> np.ndarray:
        """Return principal axes (eigenvectors) of the selected atoms via SVD.

        Returns:
          ``(3, 3)`` array for :py:class:`~biotite.structure.AtomArray`.
          ``(n_models, 3, 3)`` array for :py:class:`~biotite.structure.AtomArrayStack`.

        Raises:
          ValueError: If no atoms are selected.
        """
        mask = self.get_mask(atom_array)
        if not np.any(mask):
            raise ValueError("No atoms selected by the AtomSelectionStack.")

        if isinstance(atom_array, AtomArray):
            coords = atom_array.coord[mask]  # (N_atoms, 3)
            coords_centered = coords - coords.mean(axis=0)
            # SVD for principal axes
            _, _, vh = np.linalg.svd(coords_centered, full_matrices=False)
            return vh.T  # (3, 3), columns are principal axes
        elif isinstance(atom_array, AtomArrayStack):
            coords = atom_array.coord[:, mask, :]  # (n_models, N_atoms, 3)
            pcs = []
            for model_coords in coords:
                model_centered = model_coords - model_coords.mean(axis=0)
                _, _, vh = np.linalg.svd(model_centered, full_matrices=False)
                pcs.append(vh.T)  # (3, 3)
            return np.stack(pcs, axis=0)  # (n_models, 3, 3)
        else:
            raise ValueError(f"Cannot get principal components for {type(atom_array)}!")
