from pathlib import Path

from fastapi.testclient import TestClient

from echomap.api import create_app
from echomap.db import Database
from echomap.models import DiscoveryResult, Edge, Node


def test_api_health_and_trace(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(
        [
            Node(id="node:a", label="Alpha", kind="Website", metadata={}),
            Node(id="node:b", label="Beta", kind="Technology", metadata={}),
        ]
    )
    db.upsert_edges(
        [
            Edge(id="edge:ab", source="node:a", target="node:b", relation="uses", confidence=0.9, metadata={}),
        ]
    )

    client = TestClient(create_app(db))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["backend"]["kind"] == "sqlite"

    backend = client.get("/backend")
    assert backend.status_code == 200
    assert backend.json()["snapshot"]["mode"] == "readable"

    trace = client.get("/trace", params={"start_id": "node:a", "end_id": "node:b"})
    assert trace.status_code == 200
    body = trace.json()
    assert body["hop_count"] == 1
    assert body["steps"][0]["relation"] == "uses"


def test_api_investigation_crud(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    client = TestClient(create_app(db))

    created = client.post(
        "/investigations",
        json={
            "title": "Case Alpha",
            "query": "Alpha",
            "selected_node_id": "node:a",
            "notes": "seed case",
            "tags": "alpha,graph",
            "payload": {"graph": {"nodes": [], "edges": []}},
        },
    )
    assert created.status_code == 200
    investigation = created.json()
    assert investigation["title"] == "Case Alpha"
    assert investigation["tags"] == "alpha,graph"

    listed = client.get("/investigations", params={"query": "Case"})
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "Case Alpha"

    updated = client.put(
        f"/investigations/{investigation['id']}",
        json={
            "title": "Case Alpha Updated",
            "query": "Alpha+",
            "selected_node_id": "node:a",
            "notes": "updated",
            "tags": "alpha,graph,updated",
            "payload": {"graph": {"nodes": [], "edges": []}},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Case Alpha Updated"


def test_api_reports_endpoints(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes([Node(id="node:a", label="Alpha", kind="Website", metadata={})])
    investigation_id = db.save_investigation(
        "Alpha Case",
        "alpha.example",
        "node:a",
        "notes",
        {"graph": db.export_graph(), "discovery": {"query": "alpha.example", "summary": "Alpha discovery"}},
        "alpha",
    )
    comparison_id = db.save_comparison("node:a", "live", {"summary": "Alpha vs live", "score": 0.9})

    client = TestClient(create_app(db))
    workspace = client.get("/reports/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["kind"] == "workspace"

    investigation = client.get(f"/reports/investigations/{investigation_id}")
    assert investigation.status_code == 200
    assert investigation.json()["kind"] == "investigation"
    assert investigation.json()["title"].startswith("EchoMap Investigation Report")

    comparison = client.get(f"/reports/comparisons/{comparison_id}")
    assert comparison.status_code == 200
    assert comparison.json()["kind"] == "comparison"


def test_api_workspace_and_tabular_import_endpoints(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    client = TestClient(create_app(db))

    workspaces = client.get("/workspaces")
    assert workspaces.status_code == 200
    assert workspaces.json()[0]["is_active"] is True

    created = client.post(
        "/workspaces",
        json={
            "name": "Connecticut ALPR",
            "description": "Geographic surveillance investigation",
            "notes": "Private workspace for the public intel map",
        },
    )
    assert created.status_code == 200
    workspace_id = created.json()["id"]

    activated = client.post(f"/workspaces/{workspace_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["id"] == workspace_id

    csv_path = tmp_path / "import.csv"
    csv_path.write_text("name,latitude,longitude\nTown Hall,41.0,-73.0\nLibrary,41.1,-73.1\n", encoding="utf-8")

    imported = client.post(
        "/public/import/tabular",
        json={
            "path": str(csv_path),
            "title": "Agency Locations",
            "source_type": "csv",
        },
    )
    assert imported.status_code == 200
    assert imported.json()["rows"] == 2
    assert len(imported.json()["points"]) == 2
    assert db.list_public_layers()[0]["name"] == "Agency Locations"


def test_api_public_intel_endpoints(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    client = TestClient(create_app(db))

    db.save_public_layer(
        "Camera Layer",
        "camera",
        True,
        "#38bdf8",
        "Camera coverage points",
        {"points": [{"label": "Town Hall Camera", "latitude": 41.0, "longitude": -73.0, "kind": "camera"}]},
    )
    db.save_public_request(
        "City Council",
        "Flock camera contract",
        "2026-06-01",
        "2026-06-15",
        "Pending",
        "",
        "Flock contract review in progress.",
        [],
        {"vendor_contact": "Flock Safety"},
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

    agenda = client.post(
        "/public/agenda/scan",
        json={
            "title": "Council Agenda",
            "source_url": "https://example.org/agenda",
            "agency_name": "City Council",
            "text": "Agenda item about Flock cameras, ALPR, and a public records request.",
        },
    )
    assert agenda.status_code == 200
    assert agenda.json()["nodes"] >= 1

    layer = client.get("/public/layers")
    assert layer.status_code == 200
    assert layer.json()[0]["name"] == "Council Agenda"

    request = client.post(
        "/public/requests",
        json={
            "agency": "City Council",
            "subject": "Camera contract review",
            "request_date": "2026-06-01",
            "due_date": "2026-06-15",
            "status": "Pending",
            "response_date": "",
            "notes": "Initial request filed.",
            "attachments": [],
            "payload": {
                "contract_amount": "$45,000",
                "vendor_contact": "Vendor Rep",
                "retention_policy": "90 days",
                "sharing_policy": "Shared with police",
                "termination_clause": "30 day notice",
                "public_source": "https://example.org/minutes",
                "confidence_score": "0.8",
            },
        },
    )
    assert request.status_code == 200
    assert request.json()["agency"] == "City Council"

    citation = client.post(
        "/public/citations",
        json={
            "entity_type": "location",
            "entity_id": "location:town-hall",
            "source_type": "agenda",
            "source_url": "https://example.org/agenda",
            "uploaded_path": "",
            "screenshot_path": "",
            "confidence": 0.92,
            "retrieved_at": "2026-06-17T00:00:00",
            "notes": "Town hall coordinate",
            "payload": {"label": "Town Hall", "latitude": 41.0, "longitude": -73.0},
        },
    )
    assert citation.status_code == 200
    assert citation.json()["entity_type"] == "location"

    timeline = client.get("/public/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["events"]

    heatmap = client.get("/public/heatmap")
    assert heatmap.status_code == 200
    assert heatmap.json()["total_points"] == 2

    trail = client.post("/public/echotrail", json={"seed": "Flock"})
    assert trail.status_code == 200
    assert trail.json()["steps"]

    radar = client.post("/public/radar", json={"query": "Flock"})
    assert radar.status_code == 200
    assert radar.json()["hits"]

    radius = client.post("/public/radius", json={"latitude": 41.0, "longitude": -73.0, "radius_km": 2.0})
    assert radius.status_code == 200
    assert radius.json()["points"]

    preset = client.post(
        "/public/presets",
        json={
            "name": "Flock Connecticut",
            "mode": "radar",
            "query": "Flock",
            "notes": "Statewide radar preset",
            "payload": {"saved_for": "tests"},
        },
    )
    assert preset.status_code == 200
    assert preset.json()["name"] == "Flock Connecticut"

    presets = client.get("/public/presets", params={"mode": "radar"})
    assert presets.status_code == 200
    assert presets.json()[0]["name"] == "Flock Connecticut"

    preset_by_id = client.get(f"/public/presets/{preset.json()['id']}")
    assert preset_by_id.status_code == 200
    assert preset_by_id.json()["query"] == "Flock"


def test_api_websocket_streams_discovery_events(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "echomap.sqlite3")
    client = TestClient(create_app(db))

    monkeypatch.setattr(
        "echomap.api.discover",
        lambda query: DiscoveryResult(
            root_query=query,
            nodes=[Node(id="node:a", label="Alpha", kind="Website", metadata={})],
            edges=[],
            timeline=[],
            technologies=[],
            archaeology=[],
            summary="Websocket discovery",
            tech_profile={"summary": "None", "confidence_score": 0.0, "categories": {}},
            related_targets=[],
        ),
    )

    with client.websocket_connect("/ws/graph") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"

        response = client.post("/discover", json={"query": "ws.example"})
        assert response.status_code == 200

        event = websocket.receive_json()
        assert event["type"] in {"nodes_upserted", "discovery_added", "artifact_added"}
