from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from pathlib import Path
from queue import Empty

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .db import Database
from .services.comparison import compare_graphs, compare_nodes
from .services.discovery import discover
from .services.public_intelligence import (
    AgencyProfile,
    AgencyRadarResult,
    ChangeDetectionResult,
    EchoTrailResult,
    GeocodeResult,
    SpreadsheetImportResult,
    build_agency_profile,
    build_echotrail,
    agency_radar,
    confidence_from_source,
    compare_public_snapshots,
    export_public_map_bundle,
    export_public_map_csv,
    export_public_map_geojson,
    export_public_map_html,
    geocode_value,
    import_tabular_data,
    ingest_document_file,
    ingest_document_text,
    scan_agenda_text,
    snapshot_public_source,
    surveillance_radius,
    summarize_heatmap,
)
from .services.reports import build_report_context
from .services.relationship import trace_relationship_path
from . import __version__
from .models import utc_now_iso


def _validate_path(user_path: str, *, must_exist: bool = False) -> Path:
    """Validate and sanitize a user-supplied file path.

    Rejects paths containing '..' components and ensures the resolved
    path stays within an allowed directory (the caller's cwd or the
    echomap data directory).
    """
    raw = Path(user_path)
    if ".." in raw.parts:
        raise HTTPException(status_code=400, detail="Path traversal ('..') is not allowed.")
    resolved = raw.resolve()
    allowed_roots = [Path.cwd(), data_dir()]
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(status_code=400, detail="Path is outside allowed directories.")
    if must_exist and not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found at the specified path.")
    return resolved


def data_dir() -> Path:
    base = Path.home() / ".echomap"
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_database() -> Database:
    backend_kind = os.environ.get("ECHOMAP_BACKEND", "sqlite")
    backend_dsn = os.environ.get("ECHOMAP_BACKEND_DSN")
    return Database(data_dir() / "echomap.sqlite3", backend_kind=backend_kind, backend_dsn=backend_dsn)


class DiscoveryRequest(BaseModel):
    query: str = Field(..., min_length=1)


class InvestigationRequest(BaseModel):
    title: str
    query: str
    selected_node_id: str | None = None
    notes: str = ""
    tags: str = ""
    payload: dict = Field(default_factory=dict)


class InvestigationUpdateRequest(InvestigationRequest):
    pass


class BookmarkRequest(BaseModel):
    node: dict
    note: str = ""


class PublicLayerRequest(BaseModel):
    name: str
    kind: str
    visible: bool = True
    color: str = "#2563eb"
    notes: str = ""
    payload: dict = Field(default_factory=dict)


class PublicRequestBody(BaseModel):
    agency: str
    subject: str
    request_date: str
    due_date: str
    status: str
    response_date: str = ""
    notes: str = ""
    attachments: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)


class SourceCitationRequest(BaseModel):
    entity_type: str
    entity_id: str
    source_type: str
    source_url: str = ""
    uploaded_path: str = ""
    screenshot_path: str = ""
    confidence: float = 1.0
    retrieved_at: str = ""
    notes: str = ""
    payload: dict = Field(default_factory=dict)


class AgendaScanRequest(BaseModel):
    text: str
    title: str = "Public Meeting Agenda"
    source_url: str = ""
    agency_name: str = ""


class DocumentIngestRequest(BaseModel):
    path: str
    layer_name: str | None = None
    source_type: str | None = None


class ManualDocumentRequest(BaseModel):
    title: str
    text: str
    source_label: str = "manual"


class GeocodeRequest(BaseModel):
    value: str
    fallback_label: str | None = None


class TabularImportRequest(BaseModel):
    path: str
    title: str | None = None
    source_type: str | None = None


class AgencyProfileRequest(BaseModel):
    name: str


class ChangeDetectionRequest(BaseModel):
    title: str
    source_key: str
    base_text: str
    current_text: str
    workspace_id: int | None = None


class PublicExportRequest(BaseModel):
    title: str = "Public Map"
    format: str = "html"
    output: str


class SignaturePresetRequest(BaseModel):
    name: str
    mode: str
    query: str = ""
    center_label: str = ""
    radius_km: float = 1.5
    notes: str = ""
    payload: dict = Field(default_factory=dict)
    workspace_id: int | None = None


class EchoTrailRequest(BaseModel):
    seed: str
    workspace_id: int | None = None


class AgencyRadarRequest(BaseModel):
    query: str
    workspace_id: int | None = None


class SurveillanceRadiusRequest(BaseModel):
    center: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = 1.5
    workspace_id: int | None = None


def create_app(db: Database | None = None) -> FastAPI:
    db = db or build_database()
    app = FastAPI(
        title="EchoMap API",
        version=__version__,
        description="FastAPI backend for EchoMap's relationship graph, investigations, and discovery workflows.",
    )
    app.state.db = db

    def _backend_info() -> dict:
        info = db.backend_info()
        return {
            "kind": info.kind,
            "status": info.status,
            "description": info.description,
        }

    @app.get("/health")
    def health() -> dict:
        backend = db.backend_info()
        return {
            "status": "ok",
            "backend": {
                "kind": backend.kind,
                "status": backend.status,
                "description": backend.description,
            },
        }

    @app.get("/backend")
    def backend_snapshot() -> dict:
        info = db.backend_info()
        snapshot = db.backend_snapshot()
        return {
            "info": {
                "kind": info.kind,
                "status": info.status,
                "description": info.description,
            },
            "snapshot": snapshot,
        }

    @app.get("/stats")
    def stats() -> dict:
        return {"stats": db.stats(), "backend": _backend_info()}

    @app.get("/workspaces")
    def workspaces() -> list[dict]:
        return db.list_workspaces()

    @app.post("/workspaces")
    def create_workspace(request: dict) -> dict:
        workspace_id = db.save_workspace(
            request.get("name", "Workspace"),
            request.get("description", ""),
            request.get("notes", ""),
        )
        return db.get_workspace(workspace_id) or {"id": workspace_id}

    @app.patch("/workspaces/{workspace_id}")
    def update_workspace(workspace_id: int, request: dict) -> dict:
        ok = db.update_workspace(
            workspace_id,
            request.get("name", "Workspace"),
            request.get("description", ""),
            request.get("notes", ""),
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return db.get_workspace(workspace_id) or {"id": workspace_id}

    @app.post("/workspaces/{workspace_id}/activate")
    def activate_workspace(workspace_id: int) -> dict:
        if not db.set_active_workspace(workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return db.active_workspace()

    @app.get("/graph")
    def graph() -> dict:
        return db.export_graph()

    @app.get("/nodes")
    def nodes(limit: int = Query(500, ge=1, le=5000)) -> list[dict]:
        return db.list_nodes(limit=limit)

    @app.get("/edges")
    def edges(limit: int = Query(1000, ge=1, le=10000)) -> list[dict]:
        return db.list_edges(limit=limit)

    @app.get("/search/nodes")
    def search_nodes(query: str = Query(..., min_length=1), limit: int = Query(25, ge=1, le=100)) -> list[dict]:
        return db.search_nodes(query, limit=limit)

    @app.get("/public/layers")
    def public_layers(include_hidden: bool = True, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
        return db.list_public_layers(include_hidden=include_hidden, limit=limit)

    @app.post("/public/layers")
    def create_public_layer(request: PublicLayerRequest) -> dict:
        layer_id = db.save_public_layer(request.name, request.kind, request.visible, request.color, request.notes, request.payload)
        layer = db.list_public_layers(limit=1)[0]
        return layer if layer.get("id") == layer_id else {"id": layer_id}

    @app.patch("/public/layers/{layer_id}")
    def update_public_layer(layer_id: int, visible: bool = Query(...)) -> dict:
        if not db.set_public_layer_visibility(layer_id, visible):
            raise HTTPException(status_code=404, detail="Layer not found.")
        layer = next((row for row in db.list_public_layers(limit=500) if row["id"] == layer_id), None)
        return layer or {"id": layer_id, "visible": visible}

    @app.delete("/public/layers/{layer_id}")
    def delete_public_layer(layer_id: int) -> dict:
        with db.connect() as conn:
            cursor = conn.execute("DELETE FROM public_layers WHERE id = ?", (layer_id,))
            deleted = cursor.rowcount > 0
        if not deleted:
            raise HTTPException(status_code=404, detail="Layer not found.")
        return {"deleted": True, "layer_id": layer_id}

    @app.get("/public/requests")
    def public_requests(status: str | None = None, agency: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
        return db.list_public_requests(limit=limit, status=status, agency=agency)

    @app.post("/public/requests")
    def create_public_request(request: PublicRequestBody) -> dict:
        request_id = db.save_public_request(
            request.agency,
            request.subject,
            request.request_date,
            request.due_date,
            request.status,
            request.response_date,
            request.notes,
            request.attachments,
            request.payload,
        )
        created = db.get_public_request(request_id)
        return created or {"id": request_id}

    @app.patch("/public/requests/{request_id}")
    def update_public_request(request_id: int, request: PublicRequestBody) -> dict:
        updated = db.update_public_request(
            request_id,
            request.agency,
            request.subject,
            request.request_date,
            request.due_date,
            request.status,
            request.response_date,
            request.notes,
            request.attachments,
            request.payload,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Public record request not found.")
        return db.get_public_request(request_id) or {"id": request_id}

    @app.get("/public/citations")
    def public_citations(limit: int = Query(200, ge=1, le=500), entity_type: str | None = None, entity_id: str | None = None) -> list[dict]:
        return db.list_source_citations(limit=limit, entity_type=entity_type, entity_id=entity_id)

    @app.post("/public/geocode")
    def public_geocode(request: GeocodeRequest) -> dict:
        result = geocode_value(request.value, fallback_label=request.fallback_label)
        return asdict(result)

    @app.post("/public/import/tabular")
    def public_import_tabular(request: TabularImportRequest) -> dict:
        path = _validate_path(request.path, must_exist=True)
        result = import_tabular_data(path, title=request.title, source_type=request.source_type)
        db.upsert_nodes(result.nodes)
        db.upsert_edges(result.edges)
        if result.points:
            db.save_public_layer(
                result.title,
                "dataset",
                True,
                "#38bdf8",
                result.summary,
                {"points": result.points, **result.payload},
            )
        for citation in result.citations:
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
        return {
            "title": result.title,
            "summary": result.summary,
            "rows": len(result.rows),
            "points": result.points,
            "payload": result.payload,
        }

    @app.get("/public/agency/{name}")
    def public_agency_profile(name: str) -> dict:
        profile = build_agency_profile(db, name)
        return asdict(profile)

    @app.post("/public/change-detection")
    def public_change_detection(request: ChangeDetectionRequest) -> dict:
        if request.workspace_id is not None:
            db.set_active_workspace(request.workspace_id)
        result = compare_public_snapshots(
            request.base_text,
            request.current_text,
            title=request.title,
            source_key=request.source_key,
        )
        snapshot_public_source(db, request.source_key, request.current_text, title=request.title, source_type="change_detection")
        return asdict(result)

    @app.post("/public/export")
    def public_export(request: PublicExportRequest) -> dict:
        points: list[dict] = []
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
        output = _validate_path(request.output)
        format_name = request.format.lower()
        if format_name == "html":
            path = export_public_map_html(
                title=request.title,
                layers=db.list_public_layers(limit=200),
                requests=db.list_public_requests(limit=200),
                citations=db.list_source_citations(limit=200),
                points=points,
                output=output,
            )
        elif format_name == "geojson":
            path = export_public_map_geojson(points, output, request.title)
        elif format_name == "csv":
            path = export_public_map_csv(points, output)
        elif format_name == "zip":
            path = export_public_map_bundle(
                title=request.title,
                layers=db.list_public_layers(limit=200),
                requests=db.list_public_requests(limit=200),
                citations=db.list_source_citations(limit=200),
                points=points,
                output=output,
            )
        else:
            raise HTTPException(status_code=400, detail="Format must be one of: html, geojson, csv, zip.")
        return {"output": str(path)}

    @app.get("/public/presets")
    def public_signature_presets(mode: str | None = None, workspace_id: int | None = None) -> list[dict]:
        return db.list_signature_presets(limit=200, mode=mode, workspace_id=workspace_id)

    @app.get("/public/presets/{preset_id}")
    def get_public_signature_preset(preset_id: int, workspace_id: int | None = None) -> dict:
        preset = db.get_signature_preset(preset_id, workspace_id=workspace_id)
        if not preset:
            raise HTTPException(status_code=404, detail="Signature preset not found.")
        return preset

    @app.post("/public/presets")
    def create_public_signature_preset(request: SignaturePresetRequest) -> dict:
        preset_id = db.save_signature_preset(
            request.name,
            request.mode,
            request.query,
            request.center_label,
            request.radius_km,
            request.notes,
            request.payload,
            workspace_id=request.workspace_id,
        )
        preset = next((row for row in db.list_signature_presets(limit=200, workspace_id=request.workspace_id) if row["id"] == preset_id), None)
        return preset or {"id": preset_id}

    @app.delete("/public/presets/{preset_id}")
    def delete_public_signature_preset(preset_id: int, workspace_id: int | None = None) -> dict:
        ok = db.delete_signature_preset(preset_id, workspace_id=workspace_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Signature preset not found.")
        return {"deleted": True, "id": preset_id}

    @app.post("/public/echotrail")
    def public_echotrail(request: EchoTrailRequest) -> dict:
        result = build_echotrail(db, request.seed, workspace_id=request.workspace_id)
        return asdict(result)

    @app.post("/public/radar")
    def public_radar(request: AgencyRadarRequest) -> dict:
        result = agency_radar(db, request.query, workspace_id=request.workspace_id)
        return asdict(result)

    @app.post("/public/radius")
    def public_radius(request: SurveillanceRadiusRequest) -> dict:
        center_label = request.center or ""
        latitude = request.latitude
        longitude = request.longitude
        if latitude is None or longitude is None:
            if not center_label:
                raise HTTPException(status_code=400, detail="Provide a center label or latitude/longitude.")
            geocoded = geocode_value(center_label, fallback_label=center_label, force=True)
            latitude = geocoded.latitude
            longitude = geocoded.longitude
            center_label = geocoded.label or center_label
        if latitude is None or longitude is None:
            raise HTTPException(status_code=400, detail="Could not geocode the supplied center.")
        result = surveillance_radius(
            db,
            latitude=latitude,
            longitude=longitude,
            radius_km=request.radius_km,
            center_label=center_label,
            workspace_id=request.workspace_id,
        )
        return asdict(result)

    @app.post("/public/citations")
    def create_public_citation(request: SourceCitationRequest) -> dict:
        citation_id = db.save_source_citation(
            request.entity_type,
            request.entity_id,
            request.source_type,
            request.source_url,
            request.uploaded_path,
            request.screenshot_path,
            request.confidence,
            request.retrieved_at,
            request.notes,
            request.payload,
        )
        created = db.list_source_citations(limit=1)[0]
        return created if created.get("id") == citation_id else {"id": citation_id}

    @app.post("/public/agenda/scan")
    def scan_public_agenda(request: AgendaScanRequest) -> dict:
        result = scan_agenda_text(request.text, title=request.title, source_url=request.source_url, agency_name=request.agency_name)
        db.upsert_nodes(result.nodes)
        db.upsert_edges(result.edges)
        if result.suggested_layer:
            db.save_public_layer(
                result.suggested_layer.name,
                result.suggested_layer.kind,
                result.suggested_layer.visible,
                result.suggested_layer.color,
                result.suggested_layer.notes,
                result.suggested_layer.payload,
            )
        for citation in result.citations:
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
        return {
            "title": result.title,
            "summary": result.summary,
            "agencies": result.agencies,
            "vendors": result.vendors,
            "matches": result.matches,
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "payload": result.payload,
        }

    @app.post("/public/documents/ingest")
    def ingest_public_document(request: DocumentIngestRequest) -> dict:
        path = _validate_path(request.path, must_exist=True)
        result = ingest_document_file(path, layer_name=request.layer_name, source_type=request.source_type)
        db.upsert_nodes(result.nodes)
        db.upsert_edges(result.edges)
        if result.suggested_layer:
            db.save_public_layer(
                result.suggested_layer.name,
                result.suggested_layer.kind,
                result.suggested_layer.visible,
                result.suggested_layer.color,
                result.suggested_layer.notes,
                result.suggested_layer.payload,
            )
        for citation in result.citations:
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
        return {
            "title": result.title,
            "summary": result.summary,
            "entities": result.entities,
            "points": result.points,
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "payload": result.payload,
        }

    @app.post("/public/documents/text")
    def ingest_public_text(request: ManualDocumentRequest) -> dict:
        result = ingest_document_text(request.title, request.text, request.source_label)
        db.upsert_nodes(result.nodes)
        db.upsert_edges(result.edges)
        if result.suggested_layer:
            db.save_public_layer(
                result.suggested_layer.name,
                result.suggested_layer.kind,
                result.suggested_layer.visible,
                result.suggested_layer.color,
                result.suggested_layer.notes,
                result.suggested_layer.payload,
            )
        for citation in result.citations:
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
        return {"title": result.title, "summary": result.summary, "payload": result.payload}

    @app.get("/public/timeline")
    def public_timeline(limit: int = Query(200, ge=1, le=500)) -> dict:
        events = db.public_timeline_events(limit=limit)
        return {"events": events, "frames": [{"index": idx, **event} for idx, event in enumerate(events)]}

    @app.get("/public/heatmap")
    def public_heatmap(limit: int = Query(200, ge=1, le=1000)) -> dict:
        points: list[dict] = []
        for citation in db.list_source_citations(limit=limit):
            payload = citation.get("payload", {})
            if isinstance(payload, dict):
                if "latitude" in payload and "longitude" in payload:
                    points.append({"label": payload.get("label", citation["entity_id"]), "latitude": payload["latitude"], "longitude": payload["longitude"], "kind": citation["entity_type"]})
                payload_points = payload.get("points", [])
                if isinstance(payload_points, list):
                    for item in payload_points:
                        if "latitude" in item and "longitude" in item:
                            points.append(item)
        summary = summarize_heatmap(points)
        return {
            "total_points": summary.total_points,
            "precision": summary.precision,
            "cells": summary.cells,
            "hotspots": summary.hotspots,
            "summary": summary.summary,
        }

    @app.get("/trace")
    def trace(start_id: str = Query(..., min_length=1), end_id: str = Query(..., min_length=1)) -> dict:
        result = trace_relationship_path(db.list_nodes(limit=5000), db.list_edges(limit=10000), start_id, end_id)
        return {
            "start_id": result.start_id,
            "end_id": result.end_id,
            "node_ids": result.node_ids,
            "edge_ids": result.edge_ids,
            "steps": result.steps,
            "summary": result.summary,
            "hop_count": result.hop_count,
        }

    @app.get("/compare/nodes")
    def compare_nodes_endpoint(left_id: str = Query(...), right_id: str = Query(...)) -> dict:
        left = db.get_node(left_id)
        right = db.get_node(right_id)
        if not left or not right:
            raise HTTPException(status_code=404, detail="One or both nodes were not found.")
        result = compare_nodes(left, right, db.neighbors(left_id), db.neighbors(right_id))
        return {
            "summary": result.summary,
            "score": result.score,
            "shared_neighbors": result.shared_neighbors,
            "shared_relations": result.shared_relations,
            "left": result.left,
            "right": result.right,
        }

    @app.get("/compare/graphs")
    def compare_graphs_endpoint(
        left_investigation_id: int = Query(..., ge=1),
        right_investigation_id: int | None = Query(None, ge=1),
    ) -> dict:
        left = db.get_investigation(left_investigation_id)
        if not left:
            raise HTTPException(status_code=404, detail="Left investigation not found.")
        left_graph = left["payload"].get("graph", {})
        if right_investigation_id is None:
            right_graph = db.export_graph()
            right_label = "live workspace"
        else:
            right = db.get_investigation(right_investigation_id)
            if not right:
                raise HTTPException(status_code=404, detail="Right investigation not found.")
            right_graph = right["payload"].get("graph", {})
            right_label = right["title"]
        result = compare_graphs(left_graph, right_graph)
        return {
            "summary": result.summary,
            "overlap_score": result.overlap_score,
            "shared_nodes": result.shared_node_ids,
            "shared_edges": result.shared_edge_ids,
            "left_title": left["title"],
            "right_title": right_label,
        }

    @app.get("/reports/workspace")
    def report_workspace() -> dict:
        return build_report_context(db, live=True)

    @app.get("/reports/investigations/{investigation_id}")
    def report_investigation(investigation_id: int) -> dict:
        try:
            return build_report_context(db, investigation_id=investigation_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/reports/comparisons/{comparison_id}")
    def report_comparison(comparison_id: int) -> dict:
        try:
            return build_report_context(db, comparison_id=comparison_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/investigations")
    def investigations(query: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
        if query:
            return db.search_investigations(query, limit=limit)
        return db.list_investigations(limit=limit)

    @app.post("/investigations")
    def create_investigation(request: InvestigationRequest) -> dict:
        investigation_id = db.save_investigation(
            request.title,
            request.query,
            request.selected_node_id,
            request.notes,
            request.payload,
            request.tags,
        )
        created = db.get_investigation(investigation_id)
        return created or {"id": investigation_id}

    @app.put("/investigations/{investigation_id}")
    def update_investigation(investigation_id: int, request: InvestigationUpdateRequest) -> dict:
        ok = db.update_investigation(
            investigation_id,
            request.title,
            request.query,
            request.selected_node_id,
            request.notes,
            request.payload,
            request.tags,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Investigation not found.")
        updated = db.get_investigation(investigation_id)
        return updated or {"id": investigation_id}

    @app.delete("/investigations/{investigation_id}")
    def delete_investigation(investigation_id: int) -> dict:
        ok = db.delete_investigation(investigation_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Investigation not found.")
        return {"deleted": True, "investigation_id": investigation_id}

    @app.get("/bookmarks")
    def bookmarks() -> list[dict]:
        return db.list_bookmarks()

    @app.post("/bookmarks")
    def create_bookmark(request: BookmarkRequest) -> dict:
        db.bookmark_node(request.node, note=request.note)
        node_id = request.node.get("id")
        return db.get_bookmark(node_id) if node_id else {"bookmarked": True}

    @app.delete("/bookmarks/{node_id}")
    def delete_bookmark(node_id: str) -> dict:
        deleted = db.remove_bookmark(node_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Bookmark not found.")
        return {"deleted": True, "node_id": node_id}

    @app.post("/discover")
    def discover_endpoint(request: DiscoveryRequest) -> dict:
        result = discover(request.query)
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
        return {
            "root_query": result.root_query,
            "summary": result.summary,
            "nodes": len(result.nodes),
            "edges": len(result.edges),
            "technologies": result.technologies,
            "tech_profile": result.tech_profile,
            "archaeology": result.archaeology,
            "related_targets": result.related_targets,
        }

    @app.websocket("/ws/graph")
    async def graph_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        subscription = db.events.subscribe()
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "created_at": utc_now_iso(),
                    "payload": db.export_graph(),
                }
            )
            while True:
                try:
                    event = subscription.get(timeout=0.25)
                except Empty:
                    await asyncio.sleep(0.05)
                    continue
                await websocket.send_json(event.to_dict())
        except WebSocketDisconnect:
            pass
        finally:
            subscription.close()

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("ECHOMAP_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ECHOMAP_API_PORT", "8000"))
    uvicorn.run("echomap.api:app", host=host, port=port, reload=False)
