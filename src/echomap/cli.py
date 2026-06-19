from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .db import Database
from .services.comparison import compare_graphs, compare_nodes
from .services.discovery import discover
from .services.public_intelligence import (
    build_agency_profile,
    build_echotrail,
    agency_radar,
    compare_public_snapshots,
    confidence_from_source,
    export_public_map_bundle,
    export_public_map_csv,
    export_public_map_geojson,
    export_public_map_html,
    export_public_radius_package,
    geocode_value,
    import_tabular_data,
    export_layer_csv,
    export_layer_geojson,
    ingest_document_file,
    ingest_document_text,
    scan_agenda_text,
    snapshot_public_source,
    surveillance_radius,
    summarize_heatmap,
)
from .services.reports import build_report_context
from .services.reporting import (
    export_csv,
    export_html,
    export_json,
    export_markdown,
    export_report_html,
    export_report_markdown,
)


def data_dir() -> Path:
    base = Path.home() / ".echomap"
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_database() -> Database:
    backend_kind = os.environ.get("ECHOMAP_BACKEND", "sqlite")
    backend_dsn = os.environ.get("ECHOMAP_BACKEND_DSN")
    return Database(data_dir() / "echomap.sqlite3", backend_kind=backend_kind, backend_dsn=backend_dsn)


def persist_discovery(db: Database, query: str) -> dict[str, Any]:
    result = discover(query)
    db.upsert_nodes(result.nodes)
    db.upsert_edges(result.edges)
    db.add_discovery(result.root_query, result.summary)
    db.add_artifact(
        None,
        "discovery",
        {
            "query": result.root_query,
            "summary": result.summary,
            "timeline": result.timeline,
            "technologies": result.technologies,
            "tech_profile": result.tech_profile,
            "archaeology": result.archaeology,
            "related_targets": result.related_targets,
        },
    )
    for snapshot in result.archaeology:
        db.add_archaeology_snapshot(result.root_query, snapshot.get("type", "archaeology"), snapshot)
    payload = {
        "query": result.root_query,
        "summary": result.summary,
        "nodes": len(result.nodes),
        "edges": len(result.edges),
        "technologies": result.technologies,
        "tech_profile": result.tech_profile,
        "archaeology": result.archaeology,
        "related_targets": result.related_targets,
    }
    payload["graph"] = db.export_graph()
    return payload


def save_discovery_investigation(
    db: Database,
    discovery: dict[str, Any],
    title: str | None = None,
    notes: str = "",
    tags: str = "",
) -> int:
    graph = discovery.get("graph", {})
    investigation_payload = {
        "graph": graph,
        "discovery": {key: value for key, value in discovery.items() if key != "graph"},
        "saved_via": "echomap-cli",
    }
    return db.save_investigation(
        title or discovery.get("query") or "Discovery",
        discovery.get("query", ""),
        None,
        notes or discovery.get("summary", ""),
        investigation_payload,
        tags,
    )


def export_investigation(db: Database, investigation_id: int, output: Path, format_name: str) -> Path | tuple[Path, Path]:
    investigation = db.get_investigation(investigation_id)
    if not investigation:
        raise ValueError(f"Investigation {investigation_id} was not found.")
    graph = investigation["payload"].get("graph", {})
    format_name = format_name.lower()
    if format_name == "json":
        return export_json(investigation, output)
    if format_name == "md":
        return export_markdown(graph, output)
    if format_name == "html":
        return export_html(graph, output)
    if format_name == "csv":
        if output.suffix.lower() == ".csv":
            nodes_output = output.with_name(f"{output.stem}-nodes.csv")
            edges_output = output.with_name(f"{output.stem}-edges.csv")
        else:
            output.mkdir(parents=True, exist_ok=True)
            nodes_output = output / "echomap-nodes.csv"
            edges_output = output / "echomap-edges.csv"
        return export_csv(graph, nodes_output, edges_output)
    raise ValueError("Format must be one of: json, md, html, csv")


def export_report(db: Database, output: Path, format_name: str, investigation_id: int | None = None, comparison_id: int | None = None, live: bool = False, title: str | None = None) -> Path:
    report = build_report_context(db, investigation_id=investigation_id, comparison_id=comparison_id, live=live)
    if title:
        report["title"] = title
    format_name = format_name.lower()
    if format_name == "md":
        return export_report_markdown(report, output)
    if format_name == "html":
        return export_report_html(report, output)
    raise ValueError("Format must be one of: md, html")


def compare_nodes_command(db: Database, left_id: str, right_id: str, save: bool = False) -> dict[str, Any]:
    left = db.get_node(left_id)
    right = db.get_node(right_id)
    if not left or not right:
        raise ValueError("One or both nodes were not found.")
    result = compare_nodes(left, right, db.neighbors(left_id), db.neighbors(right_id))
    payload = {
        "mode": "nodes",
        "summary": result.summary,
        "score": result.score,
        "shared_neighbors": result.shared_neighbors,
        "shared_relations": result.shared_relations,
        "left": left,
        "right": right,
    }
    if save:
        comparison_id = db.save_comparison(left_id, right_id, payload)
        payload["comparison_id"] = comparison_id
    return payload


def compare_graphs_command(
    db: Database,
    left_investigation_id: int,
    right_investigation_id: int | None = None,
    live: bool = False,
    save: bool = False,
) -> dict[str, Any]:
    left = db.get_investigation(left_investigation_id)
    if not left:
        raise ValueError("Left investigation not found.")
    left_graph = left["payload"].get("graph", {})
    if live and right_investigation_id is not None:
        raise ValueError("Use either --live or --right-investigation-id, not both.")
    if live or right_investigation_id is None:
        right_graph = db.export_graph()
        right_label = "live workspace"
        right_reference = "live"
    else:
        right = db.get_investigation(right_investigation_id)
        if not right:
            raise ValueError("Right investigation not found.")
        right_graph = right["payload"].get("graph", {})
        right_label = right["title"]
        right_reference = f"investigation:{right_investigation_id}"
    result = compare_graphs(left_graph, right_graph)
    payload = {
        "mode": "graphs",
        "summary": result.summary,
        "overlap_score": result.overlap_score,
        "shared_nodes": result.shared_node_ids,
        "shared_edges": result.shared_edge_ids,
        "left_title": left["title"],
        "right_title": right_label,
        "left_node_count": result.left_node_count,
        "right_node_count": result.right_node_count,
    }
    if save:
        comparison_id = db.save_comparison(f"investigation:{left_investigation_id}", right_reference, payload)
        payload["comparison_id"] = comparison_id
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echomap-cli",
        description="Headless EchoMap discovery and export tooling.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  echomap-cli discover magnexis.site --save-investigation --title \"Magnexis Seed\"\n"
            "  echomap-cli compare graphs 3 --live --save\n"
            "  echomap-cli report --live --format md --output .\\exports\\workspace-report.md\n"
            "  echomap-cli report --investigation-id 1 --format html --output .\\exports\\case.html"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover", help="Run discovery headlessly and store the results locally.")
    discover_parser.add_argument("query", help="Seed query such as a domain, repo, company, or keyword.")
    discover_parser.add_argument("--title", help="Optional investigation title for saving the result.")
    discover_parser.add_argument("--notes", default="", help="Notes to attach to the saved investigation.")
    discover_parser.add_argument("--tags", default="", help="Comma-separated tags for the saved investigation.")
    discover_parser.add_argument("--save-investigation", action="store_true", help="Persist the discovery as an investigation.")
    discover_parser.add_argument("--output", type=Path, help="Write the discovery summary JSON to a file.")

    export_parser = subparsers.add_parser("export-investigation", help="Export a saved investigation.")
    export_parser.add_argument("investigation_id", type=int, help="Investigation ID to export.")
    export_parser.add_argument("--format", default="json", choices=["json", "md", "html", "csv"], help="Export format.")
    export_parser.add_argument("--output", type=Path, required=True, help="Destination path or directory.")

    compare_parser = subparsers.add_parser("compare", help="Compare nodes or saved investigation graphs.")
    compare_subparsers = compare_parser.add_subparsers(dest="compare_command", required=True)

    compare_nodes_parser = compare_subparsers.add_parser("nodes", help="Compare two nodes.")
    compare_nodes_parser.add_argument("left_id", help="Left node ID.")
    compare_nodes_parser.add_argument("right_id", help="Right node ID.")
    compare_nodes_parser.add_argument("--save", action="store_true", help="Save the comparison in the local database.")

    compare_graphs_parser = compare_subparsers.add_parser("graphs", help="Compare saved investigations or an investigation against the live workspace.")
    compare_graphs_parser.add_argument("left_investigation_id", type=int, help="Left investigation ID.")
    compare_graphs_parser.add_argument("--right-investigation-id", type=int, help="Right investigation ID.")
    compare_graphs_parser.add_argument("--live", action="store_true", help="Compare the left investigation against the current live workspace.")
    compare_graphs_parser.add_argument("--save", action="store_true", help="Save the comparison in the local database.")

    report_parser = subparsers.add_parser("report", help="Generate a Markdown or HTML report from a workspace, investigation, or comparison.")
    report_parser.add_argument("--format", default="md", choices=["md", "html"], help="Report format.")
    report_parser.add_argument("--output", type=Path, required=True, help="Output file path for the report.")
    report_source = report_parser.add_mutually_exclusive_group()
    report_source.add_argument("--investigation-id", type=int, help="Generate a report for a saved investigation.")
    report_source.add_argument("--comparison-id", type=int, help="Generate a report for a saved comparison.")
    report_source.add_argument(
        "--live",
        action="store_true",
        help="Generate a report from the live workspace. If omitted, the live workspace is used by default.",
    )
    report_parser.add_argument("--title", help="Optional custom report title.")

    public_parser = subparsers.add_parser("public", help="Public intelligence workflows for agendas, documents, citations, and heatmaps.")
    public_subparsers = public_parser.add_subparsers(dest="public_command", required=True)

    presets_parser = public_subparsers.add_parser("presets", help="Save, list, load, or delete radar presets.")
    presets_subparsers = presets_parser.add_subparsers(dest="presets_command", required=True)

    presets_save_parser = presets_subparsers.add_parser("save", help="Save a radar preset for later reuse.")
    presets_save_parser.add_argument("name", help="Preset name.")
    presets_save_parser.add_argument("--mode", default="radar", help="Preset mode to store, usually radar.")
    presets_save_parser.add_argument("--query", required=True, help="Radar query to save.")
    presets_save_parser.add_argument("--center-label", default="", help="Optional center label for location-based presets.")
    presets_save_parser.add_argument("--radius-km", type=float, default=1.5, help="Radius in kilometers for location-based presets.")
    presets_save_parser.add_argument("--notes", default="", help="Optional notes for the preset.")
    presets_save_parser.add_argument("--workspace-id", type=int, help="Optional workspace to save into.")

    presets_list_parser = presets_subparsers.add_parser("list", help="List saved presets.")
    presets_list_parser.add_argument("--mode", default="radar", help="Optional mode filter.")
    presets_list_parser.add_argument("--workspace-id", type=int, help="Optional workspace to read from.")
    presets_list_parser.add_argument("--limit", type=int, default=100, help="Maximum number of presets to return.")

    presets_load_parser = presets_subparsers.add_parser("load", help="Load a saved preset by ID.")
    presets_load_parser.add_argument("preset_id", type=int, help="Preset ID to load.")
    presets_load_parser.add_argument("--workspace-id", type=int, help="Optional workspace to read from.")

    presets_delete_parser = presets_subparsers.add_parser("delete", help="Delete a saved preset by ID.")
    presets_delete_parser.add_argument("preset_id", type=int, help="Preset ID to delete.")
    presets_delete_parser.add_argument("--workspace-id", type=int, help="Optional workspace to delete from.")

    agenda_parser = public_subparsers.add_parser("scan-agenda", help="Scan a meeting agenda or agenda text for civic-tech keywords.")
    agenda_parser.add_argument("--input", type=Path, help="Text, Markdown, HTML, or PDF agenda file to scan.")
    agenda_parser.add_argument("--text", help="Agenda text provided inline on the command line.")
    agenda_parser.add_argument("--title", default="Public Meeting Agenda", help="Title for the scan result.")
    agenda_parser.add_argument("--source-url", default="", help="Source URL for the agenda.")
    agenda_parser.add_argument("--agency-name", default="", help="Agency name to attach to the scan.")
    agenda_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    doc_parser = public_subparsers.add_parser("ingest-document", help="Ingest a document and extract agencies, vendors, locations, and citations.")
    doc_parser.add_argument("path", type=Path, help="Document file path to ingest.")
    doc_parser.add_argument("--layer-name", help="Optional display name for the generated layer.")
    doc_parser.add_argument("--source-type", help="Optional source type label such as pdf or email.")
    doc_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    text_parser = public_subparsers.add_parser("ingest-text", help="Ingest pasted text into the civic intelligence model.")
    text_parser.add_argument("title", help="Title for the generated document layer.")
    text_parser.add_argument("text", help="Text to analyze.")
    text_parser.add_argument("--source-label", default="manual", help="Label for the source text.")
    text_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    heatmap_parser = public_subparsers.add_parser("heatmap", help="Summarize geo-coded points into a heatmap-friendly payload.")
    heatmap_parser.add_argument("--input", type=Path, help="JSON file containing points.")
    heatmap_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    geocode_parser = public_subparsers.add_parser("geocode", help="Geocode an address, agency, town, or business name.")
    geocode_parser.add_argument("value", help="Address or place name to geocode.")
    geocode_parser.add_argument("--fallback-label", help="Optional label to use if geocoding succeeds.")
    geocode_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    tabular_parser = public_subparsers.add_parser("import-table", help="Import a CSV or Excel file and map the rows.")
    tabular_parser.add_argument("path", type=Path, help="CSV or Excel file to import.")
    tabular_parser.add_argument("--title", help="Optional dataset title.")
    tabular_parser.add_argument("--source-type", help="Optional source label such as csv or excel.")
    tabular_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    profile_parser = public_subparsers.add_parser("agency-profile", help="Build a profile page for an agency or organization.")
    profile_parser.add_argument("name", help="Agency or organization name.")
    profile_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    change_parser = public_subparsers.add_parser("change-detect", help="Compare old and new text and snapshot the current version.")
    change_parser.add_argument("title", help="Title for the tracked source.")
    change_parser.add_argument("source_key", help="Unique key for the source being monitored.")
    change_parser.add_argument("base_text", help="Previous text or snapshot content.")
    change_parser.add_argument("current_text", help="Current text or snapshot content.")
    change_parser.add_argument("--workspace-id", type=int, help="Optional workspace to snapshot into.")
    change_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    export_map_parser = public_subparsers.add_parser("export-map", help="Export the current civic map as HTML, CSV, GeoJSON, or ZIP.")
    export_map_parser.add_argument("--title", default="Public Map", help="Title for the export.")
    export_map_parser.add_argument("--format", default="html", choices=["html", "csv", "geojson", "zip"], help="Export format.")
    export_map_parser.add_argument("--output", type=Path, required=True, help="Output file path.")

    trail_parser = public_subparsers.add_parser("echotrail", help="Trace how a clue or entity was discovered through the workspace.")
    trail_parser.add_argument("seed", help="Seed entity, vendor, agency, or keyword.")
    trail_parser.add_argument("--workspace-id", type=int, help="Optional workspace to use.")
    trail_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    radar_parser = public_subparsers.add_parser("radar", help="Search the workspace for public clues related to a vendor, agency, or technology.")
    radar_parser.add_argument("query", help="Search query.")
    radar_parser.add_argument("--workspace-id", type=int, help="Optional workspace to use.")
    radar_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    radius_parser = public_subparsers.add_parser("radius", help="Analyze nearby public infrastructure around a geocoded location.")
    radius_parser.add_argument("center", help="Center place name or address.")
    radius_parser.add_argument("--radius-km", type=float, default=1.5, help="Radius in kilometers to inspect.")
    radius_parser.add_argument("--workspace-id", type=int, help="Optional workspace to use.")
    radius_parser.add_argument("--package", type=Path, help="Optional ZIP package containing HTML and per-layer GeoJSON files.")
    radius_parser.add_argument("--output", type=Path, help="Optional JSON output file.")

    list_parser = subparsers.add_parser("list-investigations", help="List saved investigations.")
    list_parser.add_argument("--query", default="", help="Optional search query.")

    subparsers.add_parser("stats", help="Print the current workspace stats as JSON.")

    return parser


def run_command(db: Database, args: argparse.Namespace) -> int:
    if args.command == "discover":
        payload = persist_discovery(db, args.query)
        investigation_id = None
        if args.save_investigation or args.title:
            investigation_id = save_discovery_investigation(db, payload, args.title, args.notes, args.tags)
            payload["investigation_id"] = investigation_id
        if args.output:
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "export-investigation":
        result = export_investigation(db, args.investigation_id, args.output, args.format)
        if isinstance(result, tuple):
            print(json.dumps({"nodes": str(result[0]), "edges": str(result[1])}, indent=2))
        else:
            print(json.dumps({"output": str(result)}, indent=2))
        return 0

    if args.command == "compare":
        if args.compare_command == "nodes":
            payload = compare_nodes_command(db, args.left_id, args.right_id, args.save)
        elif args.compare_command == "graphs":
            payload = compare_graphs_command(
                db,
                args.left_investigation_id,
                right_investigation_id=args.right_investigation_id,
                live=args.live,
                save=args.save,
            )
        else:  # pragma: no cover - argparse enforces subcommand
            raise ValueError(f"Unknown compare command: {args.compare_command}")
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "report":
        if args.investigation_id is not None:
            report_path = export_report(db, args.output, args.format, investigation_id=args.investigation_id, title=args.title)
        elif args.comparison_id is not None:
            report_path = export_report(db, args.output, args.format, comparison_id=args.comparison_id, title=args.title)
        else:
            report_path = export_report(db, args.output, args.format, live=True, title=args.title)
        print(json.dumps({"output": str(report_path)}, indent=2))
        return 0

    if args.command == "public":
        if args.public_command == "presets":
            if args.presets_command == "save":
                query = args.query.strip()
                if not query:
                    raise ValueError("Provide a radar query before saving a preset.")
                preset_id = db.save_signature_preset(
                    args.name,
                    args.mode,
                    query=query,
                    center_label=args.center_label,
                    radius_km=args.radius_km,
                    notes=args.notes,
                    payload={"source": "echomap-cli"},
                    workspace_id=args.workspace_id,
                )
                preset = next(
                    (row for row in db.list_signature_presets(limit=200, mode=args.mode, workspace_id=args.workspace_id) if row["id"] == preset_id),
                    None,
                ) or {"id": preset_id, "name": args.name, "mode": args.mode, "query": query}
                print(json.dumps(preset, indent=2))
                return 0
            if args.presets_command == "list":
                presets = db.list_signature_presets(limit=args.limit, mode=args.mode, workspace_id=args.workspace_id)
                print(json.dumps(presets, indent=2))
                return 0
            if args.presets_command == "load":
                preset = db.get_signature_preset(args.preset_id, workspace_id=args.workspace_id)
                if not preset:
                    raise ValueError(f"Preset {args.preset_id} was not found.")
                print(json.dumps(preset, indent=2))
                return 0
            if args.presets_command == "delete":
                if not db.delete_signature_preset(args.preset_id, workspace_id=args.workspace_id):
                    raise ValueError(f"Preset {args.preset_id} was not found.")
                print(json.dumps({"deleted": True, "id": args.preset_id}, indent=2))
                return 0
            raise ValueError(f"Unknown preset command: {args.presets_command}")
        if args.public_command == "scan-agenda":
            if args.input is None and args.text is None:
                raise ValueError("Provide either --input or --text for agenda scanning.")
            if args.input is not None:
                result = scan_agenda_text(args.input.read_text(encoding="utf-8", errors="ignore"), title=args.title, source_url=args.source_url, agency_name=args.agency_name)
            else:
                result = scan_agenda_text(args.text or "", title=args.title, source_url=args.source_url, agency_name=args.agency_name)
            payload = {
                "title": result.title,
                "summary": result.summary,
                "agencies": result.agencies,
                "vendors": result.vendors,
                "matches": result.matches,
                "nodes": len(result.nodes),
                "edges": len(result.edges),
            }
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "geocode":
            result = geocode_value(args.value, fallback_label=args.fallback_label)
            payload = asdict(result)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "import-table":
            result = import_tabular_data(args.path, title=args.title, source_type=args.source_type)
            payload = {"title": result.title, "summary": result.summary, "rows": len(result.rows), "points": result.points, "payload": result.payload}
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "agency-profile":
            profile = build_agency_profile(db, args.name)
            payload = asdict(profile)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "change-detect":
            if args.workspace_id is not None:
                db.set_active_workspace(args.workspace_id)
            result = compare_public_snapshots(args.base_text, args.current_text, title=args.title, source_key=args.source_key)
            snapshot_public_source(db, args.source_key, args.current_text, title=args.title, source_type="change_detection")
            payload = asdict(result)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "export-map":
            points = []
            for citation in db.list_source_citations(limit=500):
                payload = citation.get("payload", {})
                if isinstance(payload, dict) and "latitude" in payload and "longitude" in payload:
                    points.append({"label": payload.get("label", citation["entity_id"]), "latitude": payload["latitude"], "longitude": payload["longitude"], "kind": citation["entity_type"]})
                if isinstance(payload, dict):
                    payload_points = payload.get("points", [])
                    if isinstance(payload_points, list):
                        for point in payload_points:
                            if "latitude" in point and "longitude" in point:
                                points.append(point)
            if args.format == "html":
                path = export_public_map_html(
                    title=args.title,
                    layers=db.list_public_layers(limit=200),
                    requests=db.list_public_requests(limit=200),
                    citations=db.list_source_citations(limit=200),
                    points=points,
                    output=args.output,
                )
            elif args.format == "geojson":
                path = export_public_map_geojson(points, args.output, args.title)
            elif args.format == "csv":
                path = export_public_map_csv(points, args.output)
            elif args.format == "zip":
                path = export_public_map_bundle(
                    title=args.title,
                    layers=db.list_public_layers(limit=200),
                    requests=db.list_public_requests(limit=200),
                    citations=db.list_source_citations(limit=200),
                    points=points,
                    output=args.output,
                )
            else:
                raise ValueError("Format must be one of: html, csv, geojson, zip.")
            print(json.dumps({"output": str(path)}, indent=2))
            return 0
        if args.public_command == "echotrail":
            if args.workspace_id is not None:
                db.set_active_workspace(args.workspace_id)
            result = build_echotrail(db, args.seed)
            payload = asdict(result)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "radar":
            if args.workspace_id is not None:
                db.set_active_workspace(args.workspace_id)
            result = agency_radar(db, args.query)
            payload = asdict(result)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "radius":
            if args.workspace_id is not None:
                db.set_active_workspace(args.workspace_id)
            geocoded = geocode_value(args.center, fallback_label=args.center, force=True)
            if geocoded.latitude is None or geocoded.longitude is None:
                raise ValueError(f"Could not geocode center: {args.center}")
            result = surveillance_radius(
                db,
                latitude=geocoded.latitude,
                longitude=geocoded.longitude,
                radius_km=args.radius_km,
                center_label=geocoded.label or args.center,
            )
            payload = asdict(result)
            if args.package:
                package_path = export_public_radius_package(result, args.package)
                payload["package"] = str(package_path)
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "ingest-document":
            result = ingest_document_file(args.path, layer_name=args.layer_name, source_type=args.source_type)
            payload = {
                "title": result.title,
                "summary": result.summary,
                "entities": result.entities,
                "nodes": len(result.nodes),
                "edges": len(result.edges),
                "points": result.points,
            }
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "ingest-text":
            result = ingest_document_text(args.title, args.text, args.source_label)
            payload = {"title": result.title, "summary": result.summary, "payload": result.payload}
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.public_command == "heatmap":
            points = []
            if args.input and args.input.exists():
                points = json.loads(args.input.read_text(encoding="utf-8"))
            summary = summarize_heatmap(points if isinstance(points, list) else [])
            payload = {
                "total_points": summary.total_points,
                "precision": summary.precision,
                "cells": summary.cells,
                "hotspots": summary.hotspots,
                "summary": summary.summary,
            }
            if args.output:
                args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0

    if args.command == "list-investigations":
        investigations = db.search_investigations(args.query) if args.query else db.list_investigations()
        print(json.dumps(investigations, indent=2))
        return 0

    if args.command == "stats":
        print(json.dumps(db.stats(), indent=2))
        return 0

    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        db = build_database()
        return run_command(db, args)
    except ValueError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    except OSError as exc:
        parser.exit(1, f"{parser.prog}: filesystem error: {exc}\n")


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
