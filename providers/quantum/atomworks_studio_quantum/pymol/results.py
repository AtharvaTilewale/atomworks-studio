"""Find the files of an atomworks-quantum result directory and summarize its result, without PyMOL.

aq writes one directory per result (``Result.save``, or one pipeline stage): ``result.json`` (the scalar
summary, calculator, versions, and under ``fields`` the kind of every other field), ``structure.cif`` (the final
structure), ``arrays.npz``, and a multi-model ``<field>.cif`` per non-empty trajectory field (``trajectory``,
``images``, ``forward``, ``reverse``, ``points``). ``Result.save`` overwrites files but never deletes any, so
``fields``, not the CIFs present, says which trajectories belong to the result. Nested results, such as the
verification of a transition state, are subdirectories of the same form. A pipeline preparation stage
(protonate, relax, ...) writes ``structure.cif`` and ``arrays.npz``, and a ``result.json`` of type ``AtomArray``
without fields; a failed stage has only ``error.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Units of the result.json scalars that have one, as in the manifest of ``aq job run``.
_UNITS = {
    "energy": "eV",
    "fmax": "eV/Å",
    "elapsed": "s",
    "zero_point_energy": "eV",
    "barrier": "eV",
    "reaction_energy": "eV",
}
# result.json keys that the summary's first two lines show, or that are not results.
_HEADER_KEYS = frozenset({"type", "formula", "model", "charge", "multiplicity", "format", "stage_hash"})
# Where the error message looks for result directories below a directory that is not one: results saved side by
# side or the <NN>-<name> stages of a pipeline input, the stages of a pipeline output (<input>/<NN>-<name>) and
# those of a job directory (run/input/<NN>-<name>). Only these layouts, so a mistaken argument such as ~ does not
# walk a large tree while PyMOL waits.
_RESULT_LAYOUTS = ("*", "*/[0-9][0-9]*-*", "run/*/[0-9][0-9]*-*")
_LISTED = 5


@dataclass(frozen=True)
class ResultDirectory:
    """The files of one aq result directory.

    Attributes:
        path: The directory, absolute.
        structure: ``structure.cif``, the final structure.
        trajectories: The multi-model trajectory CIFs by field name, e.g. ``{"trajectory": <path>}``.
        document: The parsed ``result.json``.
    """

    path: Path
    structure: Path
    trajectories: dict[str, Path]
    document: dict[str, Any]

    @property
    def electronic_state(self) -> tuple[int, int] | None:
        """Total charge and multiplicity; None for a preparation stage, whose structure has none."""
        if self.document.get("type") == "AtomArray":
            return None
        return int(self.document["charge"]), int(self.document["multiplicity"])


def find_result(path: str | Path) -> ResultDirectory:
    """Return the files of the aq result directory ``path``.

    Raises:
        ValueError: If ``path`` is not a result directory, or its ``result.json`` is not valid JSON. For a
            directory without a result, the message quotes the error of a failed stage or lists the result
            directories below ``path`` (for example the stages of a job directory).
    """
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory; pass a directory that aq wrote a result to.")
    if not (path / "result.json").is_file():
        raise ValueError(f"{path} is not an aq result directory: it has no result.json.{_hint(path)}")
    structure = path / "structure.cif"
    if not structure.is_file():
        raise ValueError(f"{path} has a result.json but no structure.cif.")
    document = _read_json(path / "result.json")
    trajectories = {
        field: path / f"{field}.cif"
        for field, entry in document.get("fields", {}).items()
        if entry and entry["kind"] == "trajectory" and (path / f"{field}.cif").is_file()
    }
    return ResultDirectory(path, structure, trajectories, document)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(
            f"{path} is not valid JSON ({error}); aq may still be writing it, or was interrupted while writing it."
        ) from error


def _hint(path: Path) -> str:
    """Why ``path`` holds no result: the error of a failed stage, or where the results below it are."""
    error = path / "error.json"
    if error.is_file():
        report = _read_json(error)
        return f" The stage failed with {report['type']}: {report['message']}"
    found = sorted(
        document.parent.relative_to(path).as_posix()
        for layout in _RESULT_LAYOUTS
        for document in path.glob(f"{layout}/result.json")
        if (document.parent / "structure.cif").is_file()  # other tools write result.json files too
    )
    if not found:
        return ""
    listed = ", ".join(found[:_LISTED])
    if len(found) > _LISTED:
        listed += f" and {len(found) - _LISTED} more"
    return f" Result directories below it: {listed}."


def electronic_state_text(charge: int, multiplicity: int) -> str:
    """One line with a structure's total charge and multiplicity."""
    return f"Electronic state: total charge {charge} e, multiplicity {multiplicity}"


def summary(result: ResultDirectory) -> str:
    """Describe a result: type, formula and model, then its electronic state and scalar values, one per line."""
    document = result.document
    state = result.electronic_state
    if state is None:
        return f"{result.path.name}: structure of a preparation stage (no calculation)"
    lines = [f"{document['type']} of {document['formula']} with {document['model']}", electronic_state_text(*state)]
    lines += [
        _scalar_line(key, value)
        for key, value in document.items()
        if key not in _HEADER_KEYS and isinstance(value, bool | int | float)
    ]
    nested = [name for name, entry in document.get("fields", {}).items() if entry and entry["kind"] == "result"]
    if nested:
        lines.append(f"Nested results: {', '.join(nested)} (aq_load_result {result.path}/<name>)")
    return "\n".join(lines)


def _scalar_line(key: str, value: bool | float) -> str:
    unit = _UNITS.get(key, "")
    if isinstance(value, float):
        text = f"{value:.6f}" if unit == "eV" else f"{value:.4g}"
    else:
        text = str(value)
    return f"{key}: {text} {unit}".rstrip()
