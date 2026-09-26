"""AtomWorks-Quantum PyMOL extension: result directories and summaries, then registration and loading in PyMOL."""

import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from atomworks_studio_quantum.pymol.results import electronic_state_text, find_result, summary

# result.json of an aq optimization of water, as ``Result.save`` writes it.
OPTIMIZATION = {
    "format": 1,
    "type": "Optimization",
    "formula": "H2O",
    "charge": 0,
    "multiplicity": 1,
    "model": "gfn2-xtb",
    "energy": -137.9765423616494,
    "elapsed": 0.047134608030319214,
    "converged": True,
    "steps": 3,
    "fmax": 0.0015490308353355098,
    "calculator": {"model": "gfn2-xtb", "device": "cpu", "dtype": "float64", "options": {}},
    "fields": {"forces": {"kind": "array"}, "trajectory": {"kind": "trajectory"}},
    "versions": {"atomworks-quantum": "0.1", "atomworks": "2.1.1"},
    "stage_hash": "6d55367f",
}
WATER = (("O", "O1", 0.0, 0.0, 0.101), ("H", "H2", 0.0, 0.772, -0.468), ("H", "H3", 0.0, -0.772, -0.468))


def water_cif(name: str, models: int = 1, state: tuple[int, int] | None = None) -> str:
    """Water as aq writes it: ``models`` frames, and the 0-body charge and multiplicity if ``state`` is given."""
    columns = (
        "group_PDB type_symbol label_atom_id label_alt_id label_comp_id label_asym_id label_entity_id label_seq_id "
        "pdbx_PDB_ins_code auth_seq_id auth_comp_id auth_asym_id auth_atom_id id occupancy pdbx_formal_charge "
        "Cartn_x Cartn_y Cartn_z pdbx_PDB_model_num"
    )
    rows = [
        f"HETATM {element} {atom} . UNL A 1 1 . 1 UNL A {atom} {serial} 1.0 ? "
        f"{x:.3f} {y + 0.01 * model:.3f} {z:.3f} {model}"
        for model in range(1, models + 1)
        for serial, (element, atom, x, y, z) in enumerate(WATER, start=1)
    ]
    header = "".join(f"_atom_site.{column}\n" for column in columns.split())
    text = f"data_{name}\n#\nloop_\n{header}" + "\n".join(rows) + "\n#\n"
    if state is not None:
        text += f"_annotation_totalcharge_0_system.val0 {state[0]}\n#\n"
        text += f"_annotation_multiplicity_0_system.val0 {state[1]}\n#\n"
    return text


def result_dir(path: Path, document: dict = OPTIMIZATION, trajectories: tuple[str, ...] = ("trajectory",)) -> Path:
    """A result directory as aq writes it."""
    path.mkdir(parents=True)
    (path / "result.json").write_text(json.dumps(document))
    state = None if document["type"] == "AtomArray" else (document["charge"], document["multiplicity"])
    (path / "structure.cif").write_text(water_cif("structure", state=state))
    (path / "arrays.npz").write_bytes(b"")
    for field in trajectories:
        (path / f"{field}.cif").write_text(water_cif(field, models=4))
    return path


def test_find_result_lists_structure_trajectories_and_state(tmp_path: Path) -> None:
    directory = result_dir(tmp_path / "00-optimize")

    result = find_result(directory)

    assert result.path == directory.resolve()
    assert result.structure == directory / "structure.cif"
    assert result.trajectories == {"trajectory": directory / "trajectory.cif"}
    assert result.electronic_state == (0, 1)


def test_summary_shows_state_and_scalars_with_units(tmp_path: Path) -> None:
    result = find_result(result_dir(tmp_path / "water-opt"))

    assert summary(result).splitlines() == [
        "Optimization of H2O with gfn2-xtb",
        "Electronic state: total charge 0 e, multiplicity 1",
        "energy: -137.976542 eV",
        "elapsed: 0.04713 s",
        "converged: True",
        "steps: 3",
        "fmax: 0.001549 eV/Å",
    ]
    assert electronic_state_text(-1, 2) == "Electronic state: total charge -1 e, multiplicity 2"


def test_summary_names_nested_results(tmp_path: Path) -> None:
    document = {
        **OPTIMIZATION,
        "type": "TransitionState",
        "n_imaginary": 1,
        "fields": {
            "trajectory": {"kind": "trajectory"},
            "images": {"kind": "trajectory"},
            "vibrations": {"kind": "result"},
            "reactant": None,
        },
    }
    directory = result_dir(tmp_path / "01-find_ts", document, trajectories=("trajectory", "images"))

    result = find_result(directory)

    assert set(result.trajectories) == {"images", "trajectory"}
    lines = summary(result).splitlines()
    assert "n_imaginary: 1" in lines
    assert lines[-1] == f"Nested results: vibrations (aq_load_result {result.path}/<name>)"


def test_preparation_stage_has_no_electronic_state(tmp_path: Path) -> None:
    directory = result_dir(tmp_path / "00-protonate", {"type": "AtomArray", "stage_hash": "ab12"}, trajectories=())

    result = find_result(directory)

    assert result.electronic_state is None
    assert result.trajectories == {}
    assert summary(result) == "00-protonate: structure of a preparation stage (no calculation)"


def test_trajectories_are_the_fields_result_json_lists(tmp_path: Path) -> None:
    # A single point saved over an optimization leaves its trajectory.cif; an empty trajectory writes no CIF.
    fields = {"forces": {"kind": "array"}, "images": {"kind": "trajectory"}}
    document = {**OPTIMIZATION, "type": "SinglePoint", "fields": fields}
    directory = result_dir(tmp_path / "reused", document, trajectories=("trajectory",))

    assert find_result(directory).trajectories == {}


def test_invalid_result_json_is_named(tmp_path: Path) -> None:
    directory = result_dir(tmp_path / "00-optimize")
    (directory / "result.json").write_text('{"format": 1, "type": "Optim')

    with pytest.raises(ValueError, match=r"00-optimize/result\.json is not valid JSON \(Unterminated string"):
        find_result(directory)


def test_failed_stage_reports_its_error(tmp_path: Path) -> None:
    stage = tmp_path / "00-optimize"
    stage.mkdir()
    report = {"type": "ChargeError", "message": "the structure has no 'nhyd' annotation.", "traceback": "..."}
    (stage / "error.json").write_text(json.dumps(report))

    with pytest.raises(ValueError, match=r"has no result.json\. The stage failed with ChargeError: the structure"):
        find_result(stage)


def test_job_directory_lists_the_result_directories_below_it(tmp_path: Path) -> None:
    job = tmp_path / "job"
    for stage in ("00-optimize", "01-frequencies"):
        result_dir(job / "run" / "input" / stage)
    (job / "outputs").mkdir()
    (job / "outputs" / "manifest.json").write_text("{}")

    message = r"has no result.json\. Result directories below it: run/input/00-optimize, run/input/01-frequencies\.$"
    with pytest.raises(ValueError, match=message):
        find_result(job)


def test_long_listings_are_shortened(tmp_path: Path) -> None:
    for index in range(7):
        result_dir(tmp_path / "run" / f"{index:02d}-md")

    with pytest.raises(ValueError, match=r"04-md and 2 more\.$"):
        find_result(tmp_path / "run")


def test_listing_searches_only_where_aq_writes_results(tmp_path: Path) -> None:
    result_dir(tmp_path / "results" / "water-opt")
    result_dir(tmp_path / "results" / "archive" / "old" / "water-opt")
    (tmp_path / "results" / "benchmark").mkdir()
    (tmp_path / "results" / "benchmark" / "result.json").write_text("{}")

    with pytest.raises(ValueError, match=r"Result directories below it: water-opt\.$"):
        find_result(tmp_path / "results")


def test_missing_directory_and_structure_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a directory"):
        find_result(tmp_path / "missing")
    directory = result_dir(tmp_path / "00-optimize")
    (directory / "structure.cif").unlink()
    with pytest.raises(ValueError, match=r"has a result\.json but no structure\.cif"):
        find_result(directory)


@pytest.fixture
def pymol_cmd() -> Iterator[ModuleType]:
    pymol = pytest.importorskip("pymol", reason="PyMOL is not installed")
    pymol.finish_launching(["pymol", "-qck"])
    from atomworks_studio_pymol.state import STATE
    from pymol import cmd

    cmd.reinitialize()
    STATE.clear_all()
    yield cmd
    cmd.reinitialize()
    STATE.clear_all()


def test_extension_loader_registers_command_and_summary_hook(
    pymol_cmd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import atomworks_studio_pymol.extensions as extensions
    from atomworks_studio_pymol import panel
    from atomworks_studio_quantum.pymol.commands import aq_load_result, summarize_electronic_state

    monkeypatch.setattr(extensions, "_EXTENSIONS_FILE", tmp_path / "pymol_extensions")
    monkeypatch.setattr(extensions, "_LEGACY_EXTENSIONS_FILE", tmp_path / "legacy")
    assert extensions.add_extension("atomworks_studio_quantum.pymol")
    pymol_cmd.keyword.pop("aq_load_result", None)

    with caplog.at_level(logging.WARNING, logger="atomworks_studio_pymol"):
        extensions.load_extensions()

    assert not [record for record in caplog.records if record.name == "atomworks_studio_pymol"]
    assert pymol_cmd.keyword["aq_load_result"][0] is aq_load_result
    assert summarize_electronic_state in panel._summary_hooks


def test_register_without_qt_adds_only_the_command(pymol_cmd: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    from atomworks_studio_pymol import panel
    from atomworks_studio_quantum.pymol import register
    from atomworks_studio_quantum.pymol.commands import aq_load_result

    monkeypatch.setattr(panel, "_summary_hooks", [])
    monkeypatch.setitem(sys.modules, "pymol.Qt", None)
    monkeypatch.delitem(sys.modules, "atomworks_studio_pymol.panel")
    pymol_cmd.keyword.pop("aq_load_result", None)

    register()

    assert pymol_cmd.keyword["aq_load_result"][0] is aq_load_result
    assert panel._summary_hooks == []


def test_aq_load_result_loads_tracked_structure_and_trajectories(
    pymol_cmd: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from atomworks_studio_pymol.state import STATE
    from atomworks_studio_quantum.pymol.commands import aq_load_result, summarize_electronic_state

    directory = result_dir(tmp_path / "00-optimize")

    aq_load_result(str(directory))

    assert STATE.active == "00-optimize"
    array = STATE.arrays["00-optimize"]
    assert len(array) == 3
    assert pymol_cmd.count_states("00-optimize") == 1
    assert "00-optimize_trajectory" not in STATE.arrays
    assert pymol_cmd.count_states("00-optimize_trajectory") == 4
    assert pymol_cmd.get_names("objects", enabled_only=1) == ["00-optimize"]
    output = capsys.readouterr().out
    assert "Loaded '00-optimize' (editable), '00-optimize_trajectory' (4 states, disabled)." in output
    assert "Optimization of H2O with gfn2-xtb" in output

    assert summarize_electronic_state(array.copy()) == "Electronic state: total charge 0 e, multiplicity 1"
    STATE.set_array("00-optimize", array.copy())
    assert summarize_electronic_state(array.copy()) == ""


def test_charge_edit_ends_the_electronic_state(pymol_cmd: ModuleType, tmp_path: Path) -> None:
    from atomworks_studio_editing.structure_mutations import set_formal_charge
    from atomworks_studio_pymol.state import STATE
    from atomworks_studio_quantum.pymol.commands import aq_load_result, summarize_electronic_state

    aq_load_result(str(result_dir(tmp_path / "00-optimize")))
    array = STATE.arrays["00-optimize"]
    set_formal_charge(array, [0], value=0)
    assert summarize_electronic_state(array.copy()) == "Electronic state: total charge 0 e, multiplicity 1"

    set_formal_charge(array, [0], value=-1)  # in place, as the charge editor does
    assert summarize_electronic_state(array.copy()) == ""


def test_aq_load_result_keeps_existing_objects(
    pymol_cmd: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from atomworks_studio_pymol.state import STATE
    from atomworks_studio_quantum.pymol import register
    from atomworks_studio_quantum.pymol.commands import aq_load_result

    register()
    directory = result_dir(tmp_path / "00-optimize")
    aq_load_result(str(directory))
    capsys.readouterr()

    aq_load_result(str(directory))
    assert "00-optimize, 00-optimize_trajectory already exist" in capsys.readouterr().out
    assert pymol_cmd.count_states("00-optimize") == 1

    pymol_cmd.do(f"aq_load_result {directory}, 00-OPTIMIZE")
    pymol_cmd.sync(timeout=10)  # cmd.do may queue the command
    assert "00-optimize, 00-optimize_trajectory already exist" in capsys.readouterr().out
    assert set(STATE.arrays) == {"00-optimize"}

    pymol_cmd.do(f"aq_load_result {directory}, second")
    pymol_cmd.sync(timeout=10)
    assert set(STATE.arrays) == {"00-optimize", "second"}
    assert pymol_cmd.count_states("second_trajectory") == 4


def test_aq_load_result_reports_a_directory_without_result(
    pymol_cmd: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from atomworks_studio_quantum.pymol.commands import aq_load_result

    aq_load_result(str(tmp_path))

    assert capsys.readouterr().out.startswith(f"AtomWorks-Quantum: {tmp_path.resolve()} is not an aq result directory")
    assert pymol_cmd.get_names("objects") == []
