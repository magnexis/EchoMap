from pathlib import Path
import zipfile

from echomap.db import Database
from echomap.models import Edge, Node
from echomap.services.comparison import compare_graphs, compare_nodes
from echomap.services.public_intelligence import agency_radar, build_echotrail, export_public_radius_package, ingest_document_text, scan_agenda_text, summarize_heatmap, surveillance_radius
from echomap.services.relationship import build_relationship_chains


def test_bookmarks_and_investigations(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.bookmark_node({"id": "node:1", "label": "Alpha", "kind": "Website", "metadata": {}}, note="test")
    db.save_investigation("Case 1", "Alpha", "node:1", "notes", {"foo": "bar"})
    db.save_comparison("node:1", "node:2", {"score": 0.75})
    db.add_artifact(None, "discovery", {"tech_profile": {"summary": "Detected frontend: React"}})
    investigation = db.list_investigations()[0]
    db.update_investigation(investigation["id"], "Case 1 Updated", "Alpha+", "node:1", "updated notes", {"foo": "baz"})
    db.update_bookmark_note("node:1", "updated bookmark note")
    assert db.get_investigation(investigation["id"])["title"] == "Case 1 Updated"
    assert db.get_bookmark("node:1")["note"] == "updated bookmark note"
    assert db.recent_artifacts(limit=1, kind="discovery")[0]["payload"]["tech_profile"]["summary"] == "Detected frontend: React"
    assert db.export_graph()["latest_discovery_profile"]["summary"] == "Detected frontend: React"
    assert db.delete_investigation(investigation["id"]) is True
    assert db.get_investigation(investigation["id"]) is None

    assert db.list_bookmarks()[0]["label"] == "Alpha"
    assert db.recent_comparisons()[0]["payload"]["score"] == 0.75


def test_compare_nodes_smoke():
    left = {"id": "a", "label": "Alpha", "kind": "Website", "metadata": {}}
    right = {"id": "b", "label": "Beta", "kind": "Website", "metadata": {}}
    left_neighbors = [{"source": "a", "target": "c", "relation": "uses", "source_label": "Alpha", "target_label": "Common", "source_kind": "Website", "target_kind": "Technology"}]
    right_neighbors = [{"source": "b", "target": "c", "relation": "uses", "source_label": "Beta", "target_label": "Common", "source_kind": "Website", "target_kind": "Technology"}]
    result = compare_nodes(left, right, left_neighbors, right_neighbors)
    assert result.score >= 0
    assert result.shared_neighbors


def test_compare_graphs_smoke():
    left_graph = {
        "nodes": [{"id": "n1", "label": "Alpha", "kind": "Website", "metadata": {}}],
        "edges": [{"id": "e1", "source": "n1", "target": "n2", "relation": "uses", "confidence": 0.9, "metadata": {}}],
    }
    right_graph = {
        "nodes": [{"id": "n1", "label": "Alpha", "kind": "Website", "metadata": {}}, {"id": "n3", "label": "Beta", "kind": "Technology", "metadata": {}}],
        "edges": [{"id": "e2", "source": "n1", "target": "n3", "relation": "built_with", "confidence": 0.8, "metadata": {}}],
    }
    result = compare_graphs(left_graph, right_graph)
    assert result.shared_node_ids == ["n1"]
    assert result.left_only_node_ids == []
    assert result.right_only_node_ids == ["n3"]
    assert result.summary.startswith("Shared nodes: 1")


def test_annotations_and_relationship_chains(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(
        [
            Node(id="n1", label="Alpha", kind="Website", metadata={}),
            Node(id="n2", label="Common", kind="Technology", metadata={}),
            Node(id="n3", label="Beta", kind="Company", metadata={}),
        ]
    )
    db.upsert_edges(
        [
            Edge(id="e1", source="n1", target="n2", relation="uses", confidence=0.9, metadata={}),
            Edge(id="e2", source="n2", target="n3", relation="connected_to", confidence=0.85, metadata={}),
        ]
    )

    annotation_id = db.save_annotation("node", "n1", "Research note", "Alpha is a seed node", {"source": "test"})
    assert annotation_id > 0
    annotations = db.list_annotations(target_type="node", target_id="n1")
    assert annotations[0]["title"] == "Research note"
    assert db.stats()["annotations"] == 1

    chains = build_relationship_chains(db.list_nodes(limit=10), db.list_edges(limit=10), "n1", limit=5, max_depth=3)
    assert chains
    assert chains[0].node_ids[0] == "n1"
    assert chains[0].summary.startswith("Alpha ->")


def test_public_intelligence_scan_and_documents(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    agenda = scan_agenda_text(
        """
        City Council Agenda
        Discussion: Flock cameras and ALPR coverage
        Vendor: Rekor public safety technology
        Meeting location: 123 Main Street
        """,
        title="Council Agenda",
        source_url="https://example.org/agenda",
        agency_name="City Council",
    )
    db.upsert_nodes(agenda.nodes)
    db.upsert_edges(agenda.edges)
    if agenda.suggested_layer:
        db.save_public_layer(
            agenda.suggested_layer.name,
            agenda.suggested_layer.kind,
            agenda.suggested_layer.visible,
            agenda.suggested_layer.color,
            agenda.suggested_layer.notes,
            agenda.suggested_layer.payload,
        )
    for citation in agenda.citations:
        db.save_source_citation(
            citation.entity_type,
            citation.entity_id,
            citation.source_type,
            citation.source_url,
            citation.uploaded_path,
            citation.screenshot_path,
            citation.confidence,
            citation.retrieved_at,
            citation.notes,
            citation.payload,
        )

    document = ingest_document_text(
        "Police Contract",
        "Police Department contract with Flock for camera system and data sharing.",
        "manual-notes",
    )
    assert document.suggested_layer is not None
    assert "FOIA" not in document.summary
    heatmap = summarize_heatmap([{"latitude": 41.0, "longitude": -73.0}, {"latitude": 41.01, "longitude": -73.02}])
    assert heatmap.total_points == 2
    assert heatmap.summary.startswith("Heatmap")
    assert db.list_public_layers()[0]["name"] == "Council Agenda"
    assert db.list_source_citations()[0]["entity_type"] in {"agency", "vendor", "location"}
    assert db.stats()["public_layers"] == 1
    assert db.export_graph()["timeline_events"]


def test_signature_public_intel_features(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(
        [
            Node(id="vendor:flock", label="Flock Safety", kind="Vendor", metadata={"type": "vendor"}),
            Node(id="agency:east-haven", label="East Haven Police Department", kind="Agency", metadata={"type": "agency"}),
            Node(id="camera:town-hall", label="Town Hall Camera", kind="Camera", metadata={"type": "camera"}),
        ]
    )
    db.upsert_edges(
        [
            Edge(id="edge:vendor-agency", source="vendor:flock", target="agency:east-haven", relation="serves", confidence=0.95, metadata={}),
            Edge(id="edge:agency-camera", source="agency:east-haven", target="camera:town-hall", relation="operates", confidence=0.9, metadata={}),
        ]
    )
    db.save_public_layer(
        "Flock Camera Layer",
        "camera",
        True,
        "#38bdf8",
        "Camera locations with surveillance coverage",
        {
            "points": [
                {"label": "Town Hall Camera", "latitude": 41.0, "longitude": -73.0, "kind": "camera"},
                {"label": "Nearby School", "latitude": 41.01, "longitude": -73.01, "kind": "school"},
            ]
        },
    )
    db.save_public_request(
        "East Haven Police Department",
        "Flock contract review",
        "2026-06-01",
        "2026-06-15",
        "Pending",
        "",
        "Flock Safety camera contract under review.",
        [],
        {"vendor_contact": "Flock Safety", "confidence_score": "0.8"},
    )
    db.save_source_citation(
        "vendor",
        "Flock Safety",
        "agenda",
        "https://example.org/agenda",
        "",
        "",
        0.9,
        "2026-06-17T00:00:00",
        "Flock mentioned in agenda",
        {"label": "Flock Safety", "latitude": 41.0, "longitude": -73.0},
    )

    trail = build_echotrail(db, "Flock")
    assert trail.steps
    assert any(step["stage"] == "neighborhood" for step in trail.steps)
    assert trail.summary.startswith("EchoTrail")

    radar = agency_radar(db, "Flock")
    assert radar.hits
    assert "Flock Safety" in radar.summary or radar.possible_vendors

    radius = surveillance_radius(db, latitude=41.0, longitude=-73.0, radius_km=2.0, center_label="Town Hall")
    assert radius.points
    assert radius.groups["cameras"] or radius.groups["schools"]
    assert radius.overlays
    assert radius.summary.startswith("Surveillance radius")

    package_path = export_public_radius_package(radius, tmp_path / "radius-package.zip")
    assert package_path.exists()
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
    assert "index.html" in names
    assert any(name.startswith("layers/") and name.endswith(".geojson") for name in names)
