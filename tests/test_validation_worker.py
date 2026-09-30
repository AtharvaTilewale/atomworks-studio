"""The private worker pipe preserves the exact array that upstream validates."""

import json
import os
import pickle
import sys
from io import BytesIO, TextIOWrapper
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from atomworks.io.utils.atom_array_plus import AnnotationList2D
from atomworks.ml.conditions import C_CRD
from atomworks_studio_editing.document import StudioDocument
from atomworks_studio_rfd4 import CONDITION_PROVIDER
from atomworks_studio_rfd4._validate import main
from atomworks_studio_rfd4.model import C_CATH


@pytest.mark.parametrize("cluster", [None, "", "local", "unrelated-cluster"])
def test_desktop_runtime_uses_explicit_dotenv_without_changing_environment(monkeypatch, tmp_path, cluster):
    from atomworks_studio_rfd4._validate import configure_local_runtime

    if cluster is None:
        monkeypatch.delenv("CLUSTER", raising=False)
    else:
        monkeypatch.setenv("CLUSTER", cluster)
    monkeypatch.delitem(sys.modules, "rfproteina", raising=False)
    monkeypatch.delitem(sys.modules, "rfproteina.utils.env", raising=False)
    source = tmp_path / "rfproteina/utils"
    source.mkdir(parents=True)
    (source / "env.py").write_text(
        "from pathlib import Path\n"
        f"DOTENV_ROOT_PATH = Path({str(tmp_path / '.ipd')!r})\n"
        "def load_dotenv(dotenv_path=None, **kwargs):\n    return dotenv_path, kwargs\n"
    )
    before = os.environ.copy()
    configure_local_runtime(tmp_path)
    module = sys.modules.pop("rfproteina.utils.env")
    assert module.load_dotenv() == (os.devnull, {})
    assert module.load_dotenv(dotenv_path="explicit.env", override=True) == ("explicit.env", {"override": True})
    assert os.environ == before


@pytest.mark.parametrize("cluster", ["local", "existing"])
def test_existing_cluster_runtime_configuration_is_preserved(monkeypatch, tmp_path, cluster):
    from atomworks_studio_rfd4._validate import configure_local_runtime

    monkeypatch.setenv("CLUSTER", cluster)
    monkeypatch.delitem(sys.modules, "rfproteina", raising=False)
    monkeypatch.delitem(sys.modules, "rfproteina.utils.env", raising=False)
    source = tmp_path / "rfproteina/utils"
    source.mkdir(parents=True)
    dotenv = tmp_path / ".ipd" / f".env.{cluster}"
    dotenv.parent.mkdir()
    dotenv.write_text("configured cluster settings")
    (source / "env.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"DOTENV_ROOT_PATH = Path({str(dotenv.parent)!r})\n"
        "def load_dotenv(dotenv_path=None, **kwargs):\n"
        "    path = dotenv_path or DOTENV_ROOT_PATH / ('.env.' + os.environ['CLUSTER'])\n"
        "    return Path(path).read_text()\n"
    )
    before = os.environ.copy()
    configure_local_runtime(tmp_path)
    module = sys.modules.pop("rfproteina.utils.env")
    assert module.load_dotenv() == "configured cluster settings"
    assert os.environ == before


@pytest.mark.parametrize("pre_save", [False, True])
@pytest.mark.parametrize("reject", [False, True])
def test_validation_worker_preserves_exact_input(monkeypatch, capsys, pre_save, reject):
    document = StudioDocument.from_cif(
        (Path(__file__).parent / "fixtures/minimal_multichain.cif").read_text(),
        condition_provider=CONDITION_PROVIDER,
    )
    document.set_condition("coordinate", [0])
    document.set_condition("C_CATH", [0], value="1.x.x")
    array = document.array
    array.coord[0, 0] = np.inf  # A CIF parser must not get a chance to repair invalid input.
    array.set_annotation("test_precision", np.full(len(array), 0.12345678901234567))
    array.set_annotation("test_system", np.float64(0.12345678901234567), n_body=0)
    array.set_annotation(
        "test_pairs", AnnotationList2D(len(array), np.array([[0, 1]]), np.array([0.12345678901234567])), n_body=2
    )
    array._custom_ccd_registry["CUSTOM"] = {"sentinel": "preserved"}
    received = []

    def validate(actual):
        np.testing.assert_equal(actual.coord, array.coord)
        np.testing.assert_equal(actual.test_precision, array.test_precision)
        np.testing.assert_equal(
            actual.get_annotation("test_system", n_body=0), array.get_annotation("test_system", n_body=0)
        )
        np.testing.assert_equal(
            actual.get_annotation("test_pairs", n_body=2).values, array.get_annotation("test_pairs", n_body=2).values
        )
        np.testing.assert_equal(actual.bonds.as_array(), array.bonds.as_array())
        np.testing.assert_equal(C_CRD.annotation(actual), C_CRD.annotation(array))
        assert C_CATH.annotation(actual)[0] == "1.x.x.x"
        assert actual._custom_ccd_registry == array._custom_ccd_registry
        received.append("pipeline")
        print("model diagnostics")
        return ["[bond_length] invalid model input"] if reject else []

    def gaps(actual):
        received.append("gaps")
        return ["[res_id_gap] gap"]

    def molecules(actual):
        received.append("molecules")
        return ["[index_group_molecule] split"]

    monkeypatch.setitem(sys.modules, "rfproteina", ModuleType("rfproteina"))
    module = ModuleType("rfproteina.conditions_reporting")
    module.run_pipeline_validation = validate
    module.check_index_group_res_id_gaps = gaps
    module.check_index_group_molecule_consistency = molecules
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["_validate.py", "/unused-runtime-path", *(["--pre-save"] if pre_save else [])])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(sys, "stdin", TextIOWrapper(BytesIO(pickle.dumps(array))))
    main()
    captured = capsys.readouterr()
    assert received == ["pipeline", *(["gaps", "molecules"] if pre_save else [])]
    assert "model diagnostics" in captured.err
    assert json.loads(captured.out) == [
        *(["[bond_length] invalid model input"] if reject else []),
        *(["[res_id_gap] gap", "[index_group_molecule] split"] if pre_save else []),
    ]


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        ([], []),
        (
            ["[bond_length] bad bond", "[pipeline_error] first line\nsecond line", "[res_id_gap] missing residue"],
            [
                {"severity": "error", "check": "bond_length", "message": "bad bond"},
                {"severity": "error", "check": "pipeline_error", "message": "first line\nsecond line"},
                {"severity": "error", "check": "res_id_gap", "message": "missing residue"},
            ],
        ),
    ],
)
def test_parent_preserves_upstream_message_codes_and_order(monkeypatch, messages, expected):
    import subprocess

    from atomworks_studio_rfd4.interactive_validation import validate_model_pipeline

    array = StudioDocument.from_cif((Path(__file__).parent / "fixtures/minimal_multichain.cif").read_text()).array

    def run(command, **kwargs):
        assert command[-1] == "--pre-save"
        np.testing.assert_equal(pickle.loads(kwargs["input"]).coord, array.coord)
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(messages).encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert validate_model_pipeline(array, include_index_checks=True) == expected


@pytest.mark.parametrize("failure", ["malformed", "untagged", "timeout", "crash"])
def test_worker_failure_is_reported_not_treated_as_success(monkeypatch, failure):
    import subprocess

    from atomworks_studio_rfd4.interactive_validation import validate_model_pipeline

    array = StudioDocument.from_cif((Path(__file__).parent / "fixtures/minimal_multichain.cif").read_text()).array

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if failure == "crash":
            raise subprocess.CalledProcessError(1, command, stderr=b"runtime unavailable")
        return subprocess.CompletedProcess(
            command, 0, stdout=b"{}" if failure == "malformed" else b'["untagged"]', stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", run)
    issues = validate_model_pipeline(array)
    assert len(issues) == 1
    assert issues[0]["check"] == "pipeline_error"
    assert issues[0]["severity"] == "error"


def test_presave_isolates_hook_errors_and_copies(monkeypatch, capsys):
    array = StudioDocument.from_cif((Path(__file__).parent / "fixtures/minimal_multichain.cif").read_text()).array
    original_coord = array.coord.copy()
    received = []

    def pipeline(actual):
        actual.coord[:] = 0
        received.append("pipeline")
        raise ValueError("cannot construct pipeline")

    def gaps(actual):
        np.testing.assert_equal(actual.coord, original_coord)
        received.append("gaps")
        raise ValueError("cannot derive index groups")

    def molecules(actual):
        np.testing.assert_equal(actual.coord, original_coord)
        received.append("molecules")
        return ["[index_group_molecule] split"]

    monkeypatch.setitem(sys.modules, "rfproteina", ModuleType("rfproteina"))
    module = ModuleType("rfproteina.conditions_reporting")
    module.run_pipeline_validation = pipeline
    module.check_index_group_res_id_gaps = gaps
    module.check_index_group_molecule_consistency = molecules
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["_validate.py", "/unused-runtime-path", "--pre-save"])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(sys, "stdin", TextIOWrapper(BytesIO(pickle.dumps(array))))
    main()
    assert received == ["pipeline", "gaps", "molecules"]
    assert json.loads(capsys.readouterr().out) == [
        "[hook_error] cannot construct pipeline",
        "[hook_error] cannot derive index groups",
        "[index_group_molecule] split",
    ]
