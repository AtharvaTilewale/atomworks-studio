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


def test_selection_scope_and_atomworks_query_api() -> None:
    session_id = _session()
    atom = client.get(f"/api/sessions/{session_id}/atoms/0/annotations").json()

    residue = client.post(
        f"/api/sessions/{session_id}/selections/resolve",
        json={"atom_indices": [0], "scope": "residue"},
    )
    assert residue.status_code == 200, residue.text
    assert 0 in residue.json()["atom_indices"]
    assert residue.json()["count"] > 1

    expression = (
        f"(chain_iid == {atom['chain']!r}) & "
        f"(res_id == {atom['residue_id']})"
    )
    queried = client.post(
        f"/api/sessions/{session_id}/selections/query",
        json={"expression": expression, "syntax": "expression"},
    )
    assert queried.status_code == 200, queried.text
    assert queried.json()["atom_indices"] == residue.json()["atom_indices"]

    unsafe = client.post(
        f"/api/sessions/{session_id}/selections/query",
        json={"expression": "__import__('os').system('true')"},
    )
    assert unsafe.status_code == 422


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


def test_bond_inspection_ccd_inference_and_distance_restraints() -> None:
    session_id = _session()
    original = client.get(f"/api/sessions/{session_id}").json()["structure"]

    bonds = client.get(f"/api/sessions/{session_id}/bonds")
    assert bonds.status_code == 200, bonds.text
    assert bonds.json()["count"] > 0
    first_bond = bonds.json()["bonds"][0]
    assert first_bond["bond_type"] in client.get("/api/schema").json()["bond_types"]
    assert first_bond["distance"] > 0

    touching = client.get(
        f"/api/sessions/{session_id}/bonds",
        params={"atom_index": first_bond["atom1"]},
    )
    assert touching.status_code == 200, touching.text
    assert all(first_bond["atom1"] in (bond["atom1"], bond["atom2"]) for bond in touching.json()["bonds"])

    set_restraint = client.post(
        f"/api/sessions/{session_id}/distance-restraints",
        json={"atom1": 0, "atom2": 1, "action": "set", "distance": 3.25},
    )
    assert set_restraint.status_code == 200, set_restraint.text
    assert set_restraint.json()["structure"]["revision"] == original["revision"] + 1
    restraints = client.get(f"/api/sessions/{session_id}/distance-restraints")
    assert restraints.status_code == 200, restraints.text
    assert restraints.json()["restraints"] == [
        {
            "atom1": 0,
            "atom2": 1,
            "distance": 3.25,
            "current_distance": restraints.json()["restraints"][0]["current_distance"],
        }
    ]

    removed = client.post(
        f"/api/sessions/{session_id}/distance-restraints",
        json={"atom1": 1, "atom2": 0, "action": "remove"},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(f"/api/sessions/{session_id}/distance-restraints").json()["count"] == 0

    inferred = client.post(f"/api/sessions/{session_id}/bonds/infer-ccd")
    assert inferred.status_code == 200, inferred.text
    assert inferred.json()["structure"]["revision"] == original["revision"] + 3


def test_interaction_analysis_filtering_validation_and_revision_cache() -> None:
    session_id = _session()
    request = {
        "interaction_types": ["hbond", "hydrophobic", "pi", "salt", "halogen", "metal"],
        "hbond_model": "plip",
        "inter_chain_only": True,
    }
    first = client.post(f"/api/sessions/{session_id}/interactions/analyze", json=request)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["cached"] is False
    assert payload["interaction_count"] == len(payload["interactions"])
    for interaction in payload["interactions"]:
        assert interaction["type"] in request["interaction_types"]
        assert interaction["interaction_type"] in {
            "hbond",
            "hydrophobic",
            "pistacking",
            "pication",
            "saltbridge",
            "halogen",
            "metal",
        }
        assert interaction["atom1_role"]
        assert interaction["atom2_role"]
        assert interaction["distance"] >= 0

    cached = client.post(f"/api/sessions/{session_id}/interactions/analyze", json=request)
    assert cached.status_code == 200, cached.text
    assert cached.json()["cached"] is True
    assert cached.json()["interactions"] == payload["interactions"]

    if payload["interactions"]:
        source = payload["interactions"][0]["atom2"]
        filtered = client.post(
            f"/api/sessions/{session_id}/interactions/analyze",
            json={**request, "source_atom_indices": [source]},
        )
        assert filtered.status_code == 200, filtered.text
        assert all(interaction["atom1"] == source for interaction in filtered.json()["interactions"])

    mutated = client.post(
        f"/api/sessions/{session_id}/charges",
        json={"atom_indices": [0], "delta": 1},
    )
    assert mutated.status_code == 200, mutated.text
    refreshed = client.post(f"/api/sessions/{session_id}/interactions/analyze", json=request)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["cached"] is False
    assert refreshed.json()["revision"] == payload["revision"] + 1

    invalid_type = client.post(
        f"/api/sessions/{session_id}/interactions/analyze",
        json={"interaction_types": ["unknown"]},
    )
    assert invalid_type.status_code == 422
    invalid_filter = client.post(
        f"/api/sessions/{session_id}/interactions/analyze",
        json={"source_atom_indices": [1_000_000]},
    )
    assert invalid_filter.status_code == 422


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
