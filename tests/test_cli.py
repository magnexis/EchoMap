from pathlib import Path

from echomap.cli import build_parser, run_command
from echomap.db import Database
from echomap.models import DiscoveryResult, Edge, Node
from echomap.services.public_intelligence import GeocodeResult


def _sample_result() -> DiscoveryResult:
    return DiscoveryResult(
        root_query="example.com",
        nodes=[
            Node(id="node:a", label="Alpha", kind="Website", metadata={}),
            Node(id="node:b", label="Beta", kind="Technology", metadata={}),
        ],
        edges=[
            Edge(id="edge:ab", source="node:a", target="node:b", relation="uses", confidence=0.92, metadata={}),
        ],
        timeline=[],
        technologies=[],
        archaeology=[],
        summary="Example discovery summary",
        tech_profile={"summary": "Example stack", "confidence_score": 88.0, "categories": {}},
        related_targets=["beta.example.com"],
    )


def test_cli_headless_discovery_and_save_investigation(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "echomap.sqlite3")
    monkeypatch.setattr("echomap.cli.discover", lambda query: _sample_result())

    output = tmp_path / "discovery.json"
    args = build_parser().parse_args(
        [
            "discover",
            "example.com",
            "--save-investigation",
            "--title",
            "Example Case",
            "--tags",
            "headless,cli",
            "--output",
            str(output),
        ]
    )

    exit_code = run_command(db, args)
    assert exit_code == 0
    assert output.exists()
    investigations = db.search_investigations("Example Case")
    assert investigations[0]["title"] == "Example Case"
    assert investigations[0]["tags"] == "headless,cli"


def test_cli_export_investigation_markdown(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(_sample_result().nodes)
    db.upsert_edges(_sample_result().edges)
    investigation_id = db.save_investigation(
        "Exported Case",
        "example.com",
        "node:a",
        "notes",
        {
            "graph": db.export_graph(),
            "saved_at": "2026-06-17T00:00:00Z",
        },
        "export,markdown",
    )

    output = tmp_path / "export.md"
    args = build_parser().parse_args(
        [
            "export-investigation",
            str(investigation_id),
            "--format",
            "md",
            "--output",
            str(output),
        ]
    )

    exit_code = run_command(db, args)
    assert exit_code == 0
    assert output.exists()
    assert "Exported Case" not in output.read_text(encoding="utf-8")


def test_cli_compare_nodes_and_save(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(
        [
            Node(id="node:a", label="Alpha", kind="Website", metadata={}),
            Node(id="node:b", label="Beta", kind="Website", metadata={}),
            Node(id="node:c", label="Common", kind="Technology", metadata={}),
        ]
    )
    db.upsert_edges(
        [
            Edge(id="edge:ac", source="node:a", target="node:c", relation="uses", confidence=0.9, metadata={}),
            Edge(id="edge:bc", source="node:b", target="node:c", relation="uses", confidence=0.9, metadata={}),
        ]
    )

    args = build_parser().parse_args(["compare", "nodes", "node:a", "node:b", "--save"])
    exit_code = run_command(db, args)
    assert exit_code == 0
    comparison = db.recent_comparisons()[0]
    assert comparison["payload"]["mode"] == "nodes"
    assert comparison["payload"]["score"] >= 0
    assert comparison["payload"]["shared_neighbors"]


def test_cli_compare_graphs_against_live(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    db.upsert_nodes(_sample_result().nodes)
    db.upsert_edges(_sample_result().edges)
    investigation_id = db.save_investigation(
        "Snapshot Case",
        "example.com",
        "node:a",
        "notes",
        {"graph": db.export_graph(), "saved_at": "2026-06-17T00:00:00Z"},
        "compare,graph",
    )
    db.upsert_nodes([Node(id="node:c", label="Gamma", kind="Company", metadata={})])
    db.upsert_edges([Edge(id="edge:ac2", source="node:a", target="node:c", relation="references", confidence=0.7, metadata={})])

    args = build_parser().parse_args(["compare", "graphs", str(investigation_id), "--live", "--save"])
    exit_code = run_command(db, args)
    assert exit_code == 0
    comparison = db.recent_comparisons()[0]
    assert comparison["payload"]["mode"] == "graphs"
    assert comparison["payload"]["left_title"] == "Snapshot Case"
    assert comparison["payload"]["right_title"] == "live workspace"


def test_cli_report_for_investigation_and_comparison(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    result = _sample_result()
    db.upsert_nodes(result.nodes)
    db.upsert_edges(result.edges)
    investigation_id = db.save_investigation(
        "Report Case",
        "example.com",
        "node:a",
        "Investigation notes",
        {
            "graph": db.export_graph(),
            "discovery": {
                "query": "example.com",
                "summary": "Example discovery summary",
                "technologies": [],
                "tech_profile": {"summary": "Example stack", "confidence_score": 88.0, "categories": {}},
                "related_targets": [],
            },
        },
        "report,markdown",
    )
    comparison_id = db.save_comparison("node:a", "node:b", {"summary": "Example comparison", "score": 0.84, "shared_relations": ["uses"]})

    investigation_output = tmp_path / "investigation-report.md"
    comparison_output = tmp_path / "comparison-report.html"

    investigation_args = build_parser().parse_args(
        [
            "report",
            "--investigation-id",
            str(investigation_id),
            "--format",
            "md",
            "--output",
            str(investigation_output),
        ]
    )
    comparison_args = build_parser().parse_args(
        [
            "report",
            "--comparison-id",
            str(comparison_id),
            "--format",
            "html",
            "--output",
            str(comparison_output),
        ]
    )

    assert run_command(db, investigation_args) == 0
    assert run_command(db, comparison_args) == 0

    assert investigation_output.exists()
    investigation_text = investigation_output.read_text(encoding="utf-8")
    assert "Report Case" in investigation_text
    assert "Example discovery summary" in investigation_text

    assert comparison_output.exists()
    comparison_text = comparison_output.read_text(encoding="utf-8")
    assert "Example comparison" in comparison_text


def test_cli_signature_public_commands(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
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

    trail_args = build_parser().parse_args(["public", "echotrail", "Flock"])
    radar_args = build_parser().parse_args(["public", "radar", "Flock"])
    assert run_command(db, trail_args) == 0
    assert run_command(db, radar_args) == 0


def test_cli_signature_presets_and_radius_package(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "echomap.sqlite3")

    save_args = build_parser().parse_args(
        [
            "public",
            "presets",
            "save",
            "Flock Connecticut",
            "--query",
            "Flock Safety Connecticut",
            "--center-label",
            "East Haven",
            "--radius-km",
            "2.5",
            "--notes",
            "Statewide radar preset",
        ]
    )
    assert run_command(db, save_args) == 0

    presets = db.list_signature_presets(mode="radar")
    assert presets[0]["name"] == "Flock Connecticut"

    list_args = build_parser().parse_args(["public", "presets", "list", "--mode", "radar"])
    assert run_command(db, list_args) == 0

    load_args = build_parser().parse_args(["public", "presets", "load", str(presets[0]["id"])])
    assert run_command(db, load_args) == 0

    delete_args = build_parser().parse_args(["public", "presets", "delete", str(presets[0]["id"])])
    assert run_command(db, delete_args) == 0
    assert not db.list_signature_presets(mode="radar")

    db.save_public_layer(
        "Camera Layer",
        "camera",
        True,
        "#38bdf8",
        "Camera coverage points",
        {"points": [{"label": "Town Hall Camera", "latitude": 41.0, "longitude": -73.0, "kind": "camera"}]},
    )

    monkeypatch.setattr(
        "echomap.cli.geocode_value",
        lambda value, fallback_label=None, force=False: GeocodeResult(
            query=value,
            label=fallback_label or value,
            latitude=41.0,
            longitude=-73.0,
            confidence=0.95,
            source="test",
        ),
    )

    package_path = tmp_path / "radius-package.zip"
    radius_args = build_parser().parse_args(
        [
            "public",
            "radius",
            "Town Hall",
            "--radius-km",
            "2.0",
            "--package",
            str(package_path),
        ]
    )
    assert run_command(db, radius_args) == 0
    assert package_path.exists()
