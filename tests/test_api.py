"""HTTP-level parity checks for upload, mutation, validation, and download."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from annotation_studio.core import StudioDocument
from annotation_studio.main import DocumentStore, app
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data" / "benchmarks" / "SAM_hbonds.cif"
client = TestClient(app)


def _session() -> str:
    response = client.post(
        "/api/sessions",
        files={"file": ("SAM_hbonds.cif", FIXTURE.read_bytes(), "chemical/x-mmcif")},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def test_session_deep_link_and_optional_embedding_policy(monkeypatch: MonkeyPatch) -> None:
    response = client.post(
        "/api/sessions",
        files={"file": ("SAM_hbonds.cif", FIXTURE.read_bytes(), "chemical/x-mmcif")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    query = parse_qs(urlparse(payload["editor_path"]).query)
    assert query == {"session": [payload["session_id"]], "embed": ["1"]}
    assert response.headers["x-content-type-options"] == "nosniff"

    monkeypatch.setenv("ANNOTATION_STUDIO_FRAME_ANCESTORS", "'self' https://build.nvidia.com")
    monkeypatch.setenv("ANNOTATION_STUDIO_VERSION", "1.2.3")
    monkeypatch.setenv("ANNOTATION_STUDIO_REVISION", "test-revision")
    health = client.get("/api/health")
    assert health.headers["content-security-policy"] == "frame-ancestors 'self' https://build.nvidia.com"
    assert health.headers["cache-control"] == "no-store"
    assert health.json()["version"] == "1.2.3"
    assert health.json()["revision"] == "test-revision"


def test_persistent_store_restores_mutations_after_restart(tmp_path: Path) -> None:
    first_process = DocumentStore(tmp_path)
    session_id = first_process.add(StudioDocument.from_cif(FIXTURE.read_text(), "persistent"))
    first_process.call(
        session_id,
        lambda document: document.set_condition("hotspot", [0], enabled=True),
        persist=True,
    )

    restarted_process = DocumentStore(tmp_path)
    summary = restarted_process.call(session_id, lambda document: document.summary())
    assert summary["name"] == "persistent"
    assert summary["condition_counts"]["hotspot"] == 1
    assert summary["undo_depth"] == 0
    assert len(list(tmp_path.glob("*.session"))) == 1


def test_full_condition_api_and_download_roundtrip() -> None:
    session_id = _session()
    mutations = [
        {"condition": "sequence", "atom_indices": [0], "scope": "residue"},
        {"condition": "coordinate", "atom_indices": [0]},
        {"condition": "index", "atom_indices": [0], "enabled": True, "scope": "residue"},
        {"condition": "hotspot", "atom_indices": [0]},
        {"condition": "rasa", "atom_indices": [0], "value": 3},
        {"condition": "hbond", "atom_indices": [1], "value": 1},
        {"condition": "index_group", "atom_indices": [0], "value": 7, "scope": "residue"},
        {"condition": "coordinate_group", "atom_indices": [0], "value": 2},
        {
            "condition": "chain_centroid",
            "atom_indices": [0],
            "scope": "chain",
            "value": [1.0, 2.0, 3.0],
        },
    ]
    for mutation in mutations:
        response = client.post(f"/api/sessions/{session_id}/conditions", json=mutation)
        assert response.status_code == 200, response.text

    annotation = client.get(f"/api/sessions/{session_id}/annotations/hotspot")
    assert annotation.status_code == 200, annotation.text
    assert annotation.json()["annotation"]["values"][0]["atom_indices"] == [0]
    index_annotation = client.get(f"/api/sessions/{session_id}/annotations/index")
    assert index_annotation.json()["annotation"]["values"][0]["label"] == "Unindexed"
    atom = client.get(f"/api/sessions/{session_id}/atoms/0/annotations")
    assert atom.status_code == 200, atom.text
    assert "hotspot" in {item["name"] for item in atom.json()["annotations"]}
    stored = client.get(f"/api/sessions/{session_id}/annotation-fields/mask_pistack_1_atom")
    assert stored.status_code == 200, stored.text
    assert stored.json()["annotation"]["values"]

    response = client.post(
        f"/api/sessions/{session_id}/charges",
        json={"atom_indices": [0], "value": 1},
    )
    assert response.status_code == 200, response.text

    download = client.get(f"/api/sessions/{session_id}/cif")
    assert download.status_code == 200
    assert "_atom_site.mask_hotspot_1_atom" in download.text
    assert "_condition_ChainCentroid_1_atom" in download.text

    reupload = client.post(
        "/api/sessions",
        files={"file": ("roundtrip.cif", download.content, "chemical/x-mmcif")},
    )
    assert reupload.status_code == 200, reupload.text
    counts = reupload.json()["structure"]["condition_counts"]
    assert counts["hotspot"] == 1
    assert counts["rasa"] == 1
    assert counts["chain_centroid"] > 0


def test_upload_defenses_and_unknown_session() -> None:
    wrong_type = client.post(
        "/api/sessions",
        files={"file": ("structure.pdb", b"ATOM", "chemical/x-pdb")},
    )
    assert wrong_type.status_code == 415
    assert client.get("/api/sessions/not-a-session").status_code == 404
    session_id = _session()
    invalid_enum = client.post(
        f"/api/sessions/{session_id}/conditions",
        json={"condition": "rasa", "atom_indices": [0], "value": 999},
    )
    assert invalid_enum.status_code == 422


def test_charge_bond_delete_and_undo_routes() -> None:
    session_id = _session()
    original = client.get(f"/api/sessions/{session_id}").json()["structure"]["atom_count"]

    charge = client.post(
        f"/api/sessions/{session_id}/charges",
        json={"atom_indices": [0], "value": 1},
    )
    assert charge.status_code == 200, charge.text
    bond = client.post(
        f"/api/sessions/{session_id}/bonds",
        json={"atom1": 0, "atom2": 1, "action": "add", "bond_type": "single"},
    )
    assert bond.status_code == 200, bond.text
    deleted = client.post(
        f"/api/sessions/{session_id}/delete",
        json={"atom_indices": [original - 1], "close_residue_gaps": True},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["structure"]["atom_count"] == original - 1
    restored = client.post(f"/api/sessions/{session_id}/undo")
    assert restored.status_code == 200, restored.text
    assert restored.json()["structure"]["atom_count"] == original


def test_expandable_segment_routes() -> None:
    session_id = _session()
    inserted = client.post(
        f"/api/sessions/{session_id}/segments",
        json={"minimum": 2, "maximum": 4, "atom_index": 0, "before": False},
    )
    assert inserted.status_code == 200, inserted.text
    collapsed_count = inserted.json()["structure"]["atom_count"]
    inserted_segments = [
        item
        for chain in inserted.json()["structure"]["sequence"]
        for item in chain["items"]
        if item["kind"] == "segment"
    ]
    assert inserted_segments
    assert all(not segment["realized"] for segment in inserted_segments)
    realized = client.post(f"/api/sessions/{session_id}/segments/realize", json={"seed": 7})
    assert realized.status_code == 200, realized.text
    assert realized.json()["structure"]["atom_count"] > collapsed_count
    realized_segments = [
        item
        for chain in realized.json()["structure"]["sequence"]
        for item in chain["items"]
        if item["kind"] == "segment"
    ]
    assert realized_segments
    assert all(segment["realized"] for segment in realized_segments)
    collapsed = client.post(f"/api/sessions/{session_id}/segments/collapse")
    assert collapsed.status_code == 200, collapsed.text
    assert collapsed.json()["structure"]["atom_count"] == collapsed_count
    collapsed_segments = [
        item
        for chain in collapsed.json()["structure"]["sequence"]
        for item in chain["items"]
        if item["kind"] == "segment"
    ]
    assert all(not segment["realized"] for segment in collapsed_segments)


def test_combine_and_reset_residue_routes() -> None:
    first = _session()
    second = _session()
    original = client.get(f"/api/sessions/{first}").json()["structure"]["atom_count"]
    combined = client.post(
        f"/api/sessions/{first}/combine",
        json={"session_ids": [second]},
    )
    assert combined.status_code == 200, combined.text
    assert combined.json()["structure"]["atom_count"] == 2 * original
    reset = client.post(f"/api/sessions/{first}/reset-residue-ids")
    assert reset.status_code == 200, reset.text
