from __future__ import annotations

import csv
import json
import re
import difflib
import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..models import Edge, Node
from .relationship import build_relationship_chains

ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\- ]{2,80}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Parkway|Pkwy|Highway|Hwy|Circle|Cir)\b",
    re.IGNORECASE,
)
COORD_RE = re.compile(r"(?P<lat>-?\d{1,2}\.\d+)\s*,\s*(?P<lon>-?\d{1,3}\.\d+)")
MONEY_RE = re.compile(r"[$]\s?[\d,]+(?:\.\d{2})?")
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})\b",
    re.IGNORECASE,
)
PUBLIC_AGENDA_KEYWORDS = {
    "agenda",
    "council",
    "commission",
    "procurement",
    "public safety",
    "grant",
}
FOIA_KEYWORDS = {"foia", "public records", "sunshine", "records request", "open records"}
SURVEILLANCE_KEYWORDS = {
    "flock",
    "rekor",
    "alpr",
    "license plate reader",
    "surveillance",
    "camera system",
    "cctv",
    "shotspotter",
    "public safety technology",
    "data sharing",
}
AGENCY_KEYWORDS = {"police", "sheriff", "department", "city", "county", "office", "authority", "transit", "district", "commission"}
VENDOR_KEYWORDS = {
    "flock",
    "rekor",
    "shotspotter",
    "motorola",
    "axon",
    "verint",
    "genetec",
    "plate smart",
    "clearview",
    "cubic",
}


@dataclass(slots=True)
class PublicIntelLayer:
    name: str
    kind: str
    visible: bool = True
    color: str = "#2563eb"
    notes: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublicRecordRequest:
    agency: str
    subject: str
    request_date: str
    due_date: str
    status: str
    response_date: str = ""
    notes: str = ""
    attachments: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceCitation:
    entity_type: str
    entity_id: str
    source_type: str
    source_url: str = ""
    uploaded_path: str = ""
    screenshot_path: str = ""
    confidence: float = 1.0
    retrieved_at: str = ""
    notes: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgendaScanResult:
    title: str
    source_url: str
    summary: str
    matches: list[dict[str, Any]] = field(default_factory=list)
    agencies: list[str] = field(default_factory=list)
    vendors: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    citations: list[SourceCitation] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    suggested_layer: PublicIntelLayer | None = None


@dataclass(slots=True)
class DocumentIntelResult:
    title: str
    source_path: str
    summary: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    citations: list[SourceCitation] = field(default_factory=list)
    suggested_layer: PublicIntelLayer | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HeatmapSummary:
    total_points: int
    precision: float
    cells: list[dict[str, Any]] = field(default_factory=list)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass(slots=True)
class GeocodeResult:
    query: str
    label: str
    latitude: float | None
    longitude: float | None
    confidence: float
    source: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SpreadsheetImportResult:
    title: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)
    citations: list[SourceCitation] = field(default_factory=list)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgencyProfile:
    name: str
    address: str = ""
    contact_email: str = ""
    records_officer: str = ""
    vendors: list[str] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    related_agencies: list[str] = field(default_factory=list)
    mapped_assets: list[dict[str, Any]] = field(default_factory=list)
    confidence_score: float = 0.0
    notes: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChangeDetectionResult:
    title: str
    source_key: str
    base_text: str
    current_text: str
    summary: str
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    diff_lines: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EchoTrailResult:
    seed: str
    summary: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgencyRadarResult:
    query: str
    summary: str
    hits: list[dict[str, Any]] = field(default_factory=list)
    possible_agencies: list[str] = field(default_factory=list)
    possible_vendors: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SurveillanceRadiusResult:
    center_label: str
    latitude: float
    longitude: float
    radius_km: float
    summary: str
    groups: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    overlays: list[dict[str, Any]] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def _stable_id(prefix: str, value: str) -> str:
    import hashlib

    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .,:;\"'")


def _read_text_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".rtf", ".log", ".html", ".htm", ".json", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(path))
            return "\n".join(page.get_text("text") for page in doc)
        except Exception:
            try:
                import pdfplumber  # type: ignore

                with pdfplumber.open(str(path)) as pdf:
                    return "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception:
                return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_key_lines(text: str, keywords: set[str]) -> list[str]:
    matches = []
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            matches.append(_normalize_label(line))
    return list(dict.fromkeys(matches))


def _extract_coordinates(text: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for match in COORD_RE.finditer(text):
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
        points.append({"label": f"{lat:.5f}, {lon:.5f}", "latitude": lat, "longitude": lon})
    return points


def _contains_query(text: Any, query: str) -> bool:
    if not query:
        return False
    return query.lower() in str(text).lower()


def _extract_payload_points(payload: Any, label_hint: str = "", kind_hint: str = "") -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    points: list[dict[str, Any]] = []
    if "latitude" in payload and "longitude" in payload:
        points.append(
            {
                "label": payload.get("label") or label_hint or "Point",
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "kind": payload.get("kind") or kind_hint or "point",
                "payload": payload,
            }
        )
    payload_points = payload.get("points", [])
    if isinstance(payload_points, list):
        for point in payload_points:
            if isinstance(point, dict) and "latitude" in point and "longitude" in point:
                points.append(
                    {
                        "label": point.get("label") or label_hint or "Point",
                        "latitude": point.get("latitude"),
                        "longitude": point.get("longitude"),
                        "kind": point.get("kind") or kind_hint or "point",
                        "payload": point,
                    }
                )
    return points


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_earth_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius_earth_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _classify_map_point(point: dict[str, Any]) -> str:
    text = " ".join(
        str(point.get(key, ""))
        for key in ("label", "kind")
    ).lower()
    if any(token in text for token in ("school", "academy", "college", "university")):
        return "schools"
    if any(token in text for token in ("road", "street", "st ", "st.", "highway", "route", "boulevard", "blvd", "avenue", "ave", "pkwy", "lane", "drive")):
        return "roads"
    if any(token in text for token in ("neighborhood", "district", "ward", "borough", "village", "downtown", "subdivision")):
        return "neighborhoods"
    if any(token in text for token in ("city hall", "town hall", "courthouse", "police", "department", "municipal", "government")):
        return "government"
    if any(token in text for token in ("camera", "flock", "alpr", "rekor", "cctv", "shotspotter", "sensor", "license plate reader")):
        return "cameras"
    return "other"


def _workspace_text_rows(db, workspace_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for discovery in db.recent_discoveries(limit=50, workspace_id=workspace_id):
        rows.append({"kind": "discovery", "label": discovery.get("query", "discovery"), "text": discovery.get("summary", ""), "payload": discovery})
    for artifact in db.recent_artifacts(limit=50, workspace_id=workspace_id):
        rows.append({"kind": artifact.get("kind", "artifact"), "label": artifact.get("node_id") or artifact.get("kind", "artifact"), "text": json.dumps(artifact.get("payload", {})), "payload": artifact})
    for layer in db.list_public_layers(limit=100, workspace_id=workspace_id):
        rows.append({"kind": "layer", "label": layer.get("name", "layer"), "text": " ".join([layer.get("name", ""), layer.get("notes", ""), json.dumps(layer.get("payload", {}))]), "payload": layer})
    for request in db.list_public_requests(limit=100, workspace_id=workspace_id):
        rows.append({"kind": "request", "label": request.get("agency", "request"), "text": " ".join([request.get("agency", ""), request.get("subject", ""), request.get("notes", ""), json.dumps(request.get("payload", {}))]), "payload": request})
    for citation in db.list_source_citations(limit=200, workspace_id=workspace_id):
        rows.append({"kind": "citation", "label": citation.get("entity_id", "citation"), "text": " ".join([citation.get("entity_type", ""), citation.get("source_type", ""), citation.get("notes", ""), json.dumps(citation.get("payload", {}))]), "payload": citation})
    return rows


def _collect_public_map_points(db, workspace_id: int) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for citation in db.list_source_citations(limit=500, workspace_id=workspace_id):
        payload = citation.get("payload", {})
        if isinstance(payload, dict):
            points.extend(_extract_payload_points(payload, label_hint=citation.get("entity_id", ""), kind_hint=citation.get("entity_type", "")))
    for layer in db.list_public_layers(limit=200, workspace_id=workspace_id):
        points.extend(_extract_payload_points(layer.get("payload", {}), label_hint=layer.get("name", "layer"), kind_hint=layer.get("kind", "layer")))
    for request in db.list_public_requests(limit=200, workspace_id=workspace_id):
        points.extend(_extract_payload_points(request.get("payload", {}), label_hint=request.get("agency", "request"), kind_hint="request"))
    for artifact in db.recent_artifacts(limit=100, workspace_id=workspace_id):
        points.extend(_extract_payload_points(artifact.get("payload", {}), label_hint=str(artifact.get("kind", "artifact")), kind_hint=str(artifact.get("kind", "artifact"))))
    return points


def _build_gis_overlay_layers(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    color_map = {
        "schools": "#22c55e",
        "roads": "#f59e0b",
        "neighborhoods": "#8b5cf6",
        "government": "#ef4444",
        "cameras": "#38bdf8",
        "other": "#94a3b8",
    }
    overlays: list[dict[str, Any]] = []
    for category, points in groups.items():
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
                "properties": {
                    "label": point.get("label", ""),
                    "kind": point.get("kind", ""),
                    "distance_km": point.get("distance_km", 0),
                    "category": category,
                },
            }
            for point in points
        ]
        overlays.append(
            {
                "category": category,
                "color": color_map.get(category, "#94a3b8"),
                "count": len(points),
                "geojson": {"type": "FeatureCollection", "name": category, "features": features},
            }
        )
    return overlays


def _render_gis_overlay_html(
    *,
    title: str,
    overlays: list[dict[str, Any]],
    center: tuple[float, float] | None = None,
) -> str:
    overlay_data = json.dumps(overlays, indent=2)
    center_lat, center_lon = center if center else (39.5, -98.35)
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{title}</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
          body {{ margin: 0; font-family: Arial, sans-serif; background: #0f172a; color: #e5e7eb; }}
          .header {{ padding: 16px 20px; border-bottom: 1px solid #1e293b; background: #111827; }}
          .header h1 {{ margin: 0 0 8px; font-size: 1.4rem; }}
          .header p {{ margin: 0; color: #94a3b8; }}
          #map {{ height: 72vh; }}
          .panel {{ padding: 16px 20px; }}
          .layers {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
          .layer-card {{ border: 1px solid #334155; border-radius: 12px; padding: 12px; background: #111827; }}
          .layer-card h3 {{ margin: 0 0 6px; text-transform: capitalize; }}
          .layer-card p {{ margin: 4px 0; color: #cbd5e1; }}
        </style>
      </head>
      <body>
        <div class="header">
          <h1>{title}</h1>
          <p>Exported GIS layer package with togglable overlays and embedded GeoJSON data.</p>
        </div>
        <div id="map"></div>
        <div class="panel">
          <div class="layers" id="layer-list"></div>
        </div>
        <script>
          const overlays = {overlay_data};
          const map = L.map('map').setView([{center_lat}, {center_lon}], 12);
          L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18 }}).addTo(map);
          const layerGroups = {{}};
          const bounds = [];
          overlays.forEach(layer => {{
            const group = L.layerGroup();
            (layer.geojson?.features || []).forEach(feature => {{
              const coords = feature.geometry?.coordinates || [];
              if (coords.length < 2) return;
              const [lon, lat] = coords;
              const marker = L.circleMarker([lat, lon], {{
                radius: 7,
                color: layer.color || '#38bdf8',
                fillColor: layer.color || '#38bdf8',
                fillOpacity: 0.85,
                weight: 2,
              }});
              const props = feature.properties || {{}};
              marker.bindPopup(`<strong>${{props.label || layer.category}}</strong><br/>${{props.kind || ''}}<br/>${{props.distance_km != null ? props.distance_km + ' km away' : ''}}`);
              marker.addTo(group);
              bounds.push([lat, lon]);
            }});
            if (group.getLayers().length) {{
              group.addTo(map);
            }}
            layerGroups[layer.category] = group;
          }});
          if (bounds.length) {{
            map.fitBounds(bounds, {{ padding: [24, 24] }});
          }}
          L.control.layers(null, layerGroups, {{ collapsed: false }}).addTo(map);
          const layerList = document.getElementById('layer-list');
          overlays.forEach(layer => {{
            const card = document.createElement('div');
            card.className = 'layer-card';
            card.innerHTML = `<h3>${{layer.category}}</h3><p>Points: ${{layer.count || 0}}</p><p>Color: ${{layer.color || '#38bdf8'}}</p>`;
            layerList.appendChild(card);
          }});
        </script>
      </body>
    </html>
    """


def export_gis_layer_package(
    *,
    title: str,
    overlays: list[dict[str, Any]],
    output: Path,
    center: tuple[float, float] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = io.BytesIO()
    html_doc = _render_gis_overlay_html(title=title, overlays=overlays, center=center)
    manifest = {
        "title": title,
        "overlay_count": len(overlays),
        "overlays": [
            {
                "category": overlay.get("category", "other"),
                "color": overlay.get("color", "#94a3b8"),
                "count": overlay.get("count", 0),
            }
            for overlay in overlays
        ],
    }
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("index.html", html_doc)
        for overlay in overlays:
            category = str(overlay.get("category", "other"))
            geojson = overlay.get("geojson", {"type": "FeatureCollection", "features": []})
            zf.writestr(f"layers/{category}.geojson", json.dumps(geojson, indent=2))
    output.write_bytes(bundle.getvalue())
    return output


def _build_citation(
    *,
    entity_type: str,
    entity_id: str,
    source_type: str,
    source_url: str = "",
    uploaded_path: str = "",
    screenshot_path: str = "",
    confidence: float = 1.0,
    notes: str = "",
    payload: dict[str, Any] | None = None,
) -> SourceCitation:
    return SourceCitation(
        entity_type=entity_type,
        entity_id=entity_id,
        source_type=source_type,
        source_url=source_url,
        uploaded_path=uploaded_path,
        screenshot_path=screenshot_path,
        confidence=confidence,
        retrieved_at=datetime.now().isoformat(),
        notes=notes,
        payload=payload or {},
    )


def scan_agenda_text(
    text: str,
    *,
    title: str = "Public Meeting Agenda",
    source_url: str = "",
    agency_name: str = "",
) -> AgendaScanResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lower_text = text.lower()
    nodes: list[Node] = []
    edges: list[Edge] = []
    citations: list[SourceCitation] = []
    matches: list[dict[str, Any]] = []
    agencies = _extract_key_lines(text, AGENCY_KEYWORDS)
    vendors = _extract_key_lines(text, VENDOR_KEYWORDS)

    agenda_id = _stable_id("agenda", f"{title}:{source_url or text[:120]}")
    agenda_node = Node(
        id=agenda_id,
        label=title,
        kind="Document",
        metadata={
            "source_url": source_url,
            "agency_name": agency_name,
            "source_type": "agenda",
            "match_count": 0,
        },
    )
    nodes.append(agenda_node)

    for keyword in sorted(PUBLIC_AGENDA_KEYWORDS | SURVEILLANCE_KEYWORDS | FOIA_KEYWORDS):
        if keyword in lower_text:
            matches.append({"keyword": keyword, "found": True})

    for agency in agencies[:20]:
        agency_id = _stable_id("agency", agency)
        nodes.append(Node(id=agency_id, label=agency, kind="Agency", metadata={"source_url": source_url, "source_type": "agenda"}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{agenda_id}->{agency_id}:references"),
                source=agenda_id,
                target=agency_id,
                relation="references",
                confidence=0.78,
                metadata={"evidence": "agenda line"},
            )
        )
        citations.append(
            _build_citation(
                entity_type="agency",
                entity_id=agency_id,
                source_type="agenda",
                source_url=source_url,
                notes="Agency detected in public meeting agenda",
                payload={"title": title, "agency": agency},
            )
        )

    for vendor in vendors[:20]:
        vendor_id = _stable_id("vendor", vendor)
        nodes.append(Node(id=vendor_id, label=vendor, kind="Vendor", metadata={"source_url": source_url, "source_type": "agenda"}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{agenda_id}->{vendor_id}:references"),
                source=agenda_id,
                target=vendor_id,
                relation="references",
                confidence=0.76,
                metadata={"evidence": "agenda keyword"},
            )
        )
        citations.append(
            _build_citation(
                entity_type="vendor",
                entity_id=vendor_id,
                source_type="agenda",
                source_url=source_url,
                notes="Vendor detected in public meeting agenda",
                payload={"title": title, "vendor": vendor},
            )
        )

    points = _extract_coordinates(text)
    for point in points:
        location_id = _stable_id("location", point["label"])
        nodes.append(Node(id=location_id, label=point["label"], kind="Location", metadata={**point, "source_url": source_url}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{agenda_id}->{location_id}:mentions"),
                source=agenda_id,
                target=location_id,
                relation="mentions",
                confidence=0.85,
                metadata={"evidence": "coordinate match"},
            )
        )
        citations.append(
            _build_citation(
                entity_type="location",
                entity_id=location_id,
                source_type="agenda",
                source_url=source_url,
                confidence=0.85,
                notes="Location detected in agenda",
                payload=point,
            )
        )

    summary_bits = []
    if matches:
        keywords = ", ".join(match["keyword"] for match in matches[:8])
        summary_bits.append(f"Keyword matches: {keywords}.")
    if agencies:
        summary_bits.append(f"Agencies mentioned: {len(agencies)}.")
    if vendors:
        summary_bits.append(f"Vendors mentioned: {len(vendors)}.")
    if points:
        summary_bits.append(f"Geo-coded points found: {len(points)}.")
    if not summary_bits:
        summary_bits.append(f"Scanned {len(lines)} agenda lines.")

    layer_kind = "agenda"
    if any(keyword in lower_text for keyword in FOIA_KEYWORDS):
        layer_kind = "foia"
    if any(keyword in lower_text for keyword in SURVEILLANCE_KEYWORDS):
        layer_kind = "surveillance"

    payload = {
        "title": title,
        "source_url": source_url,
        "agency_name": agency_name,
        "matches": matches,
        "agencies": agencies,
        "vendors": vendors,
        "points": points,
        "source_type": "agenda",
    }
    return AgendaScanResult(
        title=title,
        source_url=source_url,
        summary=" ".join(summary_bits),
        matches=matches,
        agencies=agencies,
        vendors=vendors,
        nodes=nodes,
        edges=edges,
        citations=citations,
        points=points,
        payload=payload,
        suggested_layer=PublicIntelLayer(
            name=title,
            kind=layer_kind,
            visible=True,
            color="#dc2626" if layer_kind == "foia" else "#f97316" if layer_kind == "surveillance" else "#2563eb",
            notes="Auto-generated from agenda scan.",
            payload=payload,
        ),
    )


def ingest_document_file(path: Path, layer_name: str | None = None, source_type: str | None = None) -> DocumentIntelResult:
    text = _read_text_file(path)
    title = layer_name or path.stem.replace("_", " ").strip() or "Document"
    source_path = str(path)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lower_text = text.lower()

    document_id = _stable_id("document", source_path)
    document_node = Node(
        id=document_id,
        label=title,
        kind="Document",
        metadata={
            "source_path": source_path,
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "source_type": source_type or path.suffix.lower().lstrip(".") or "file",
        },
    )

    agencies = _extract_key_lines(text, AGENCY_KEYWORDS)
    vendors = _extract_key_lines(text, VENDOR_KEYWORDS)
    points = _extract_coordinates(text)
    money_matches = MONEY_RE.findall(text)
    dates = DATE_RE.findall(text)

    nodes = [document_node]
    edges: list[Edge] = []
    entities: list[dict[str, Any]] = []
    citations: list[SourceCitation] = [
        _build_citation(
            entity_type="document",
            entity_id=document_id,
            source_type=source_type or "file",
            uploaded_path=source_path,
            notes="Document uploaded for intelligence extraction",
            payload={"title": title, "source_path": source_path},
        )
    ]

    for agency in agencies[:20]:
        agency_id = _stable_id("agency", agency)
        nodes.append(Node(id=agency_id, label=agency, kind="Agency", metadata={"source": source_path}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{document_id}->{agency_id}:references"),
                source=document_id,
                target=agency_id,
                relation="references",
                confidence=0.72,
                metadata={"evidence": "agency line"},
            )
        )
        entities.append({"kind": "Agency", "label": agency, "evidence": "text heuristic"})
        citations.append(
            _build_citation(
                entity_type="agency",
                entity_id=agency_id,
                source_type=source_type or "file",
                uploaded_path=source_path,
                confidence=0.72,
                notes="Agency extracted from document",
                payload={"line": agency},
            )
        )

    for vendor in vendors[:20]:
        vendor_id = _stable_id("vendor", vendor)
        nodes.append(Node(id=vendor_id, label=vendor, kind="Vendor", metadata={"source": source_path}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{document_id}->{vendor_id}:references"),
                source=document_id,
                target=vendor_id,
                relation="references",
                confidence=0.7,
                metadata={"evidence": "vendor line"},
            )
        )
        entities.append({"kind": "Vendor", "label": vendor, "evidence": "text heuristic"})
        citations.append(
            _build_citation(
                entity_type="vendor",
                entity_id=vendor_id,
                source_type=source_type or "file",
                uploaded_path=source_path,
                confidence=0.7,
                notes="Vendor extracted from document",
                payload={"line": vendor},
            )
        )

    for point in points:
        location_id = _stable_id("location", point["label"])
        nodes.append(Node(id=location_id, label=point["label"], kind="Location", metadata={**point, "source": source_path}))
        edges.append(
            Edge(
                id=_stable_id("edge", f"{document_id}->{location_id}:mentions"),
                source=document_id,
                target=location_id,
                relation="mentions",
                confidence=0.86,
                metadata={"evidence": "coordinate match"},
            )
        )
        entities.append({"kind": "Location", "label": point["label"], "evidence": "coordinates"})
        citations.append(
            _build_citation(
                entity_type="location",
                entity_id=location_id,
                source_type=source_type or "file",
                uploaded_path=source_path,
                confidence=0.86,
                notes="Location extracted from document",
                payload=point,
            )
        )

    summary_bits = [f"Parsed {len(lines)} lines from {path.name}."]
    if money_matches:
        summary_bits.append(f"Found {len(money_matches)} contract/value references.")
    if dates:
        summary_bits.append(f"Found {len(dates)} explicit dates.")
    if any(keyword in lower_text for keyword in FOIA_KEYWORDS):
        summary_bits.append("FOIA/public records language detected.")
    if any(keyword in lower_text for keyword in SURVEILLANCE_KEYWORDS):
        summary_bits.append("Surveillance technology language detected.")

    layer_kind = source_type or "document"
    if any(keyword in lower_text for keyword in FOIA_KEYWORDS):
        layer_kind = "foia"
    elif any(keyword in lower_text for keyword in SURVEILLANCE_KEYWORDS):
        layer_kind = "surveillance"

    payload = {
        "source_path": source_path,
        "source_type": source_type or "file",
        "layer_kind": layer_kind,
        "counts": {
            "agencies": len(agencies),
            "vendors": len(vendors),
            "locations": len(points),
            "money_mentions": len(money_matches),
            "date_mentions": len(dates),
        },
        "entities": entities,
        "sample_text": text[:4000],
    }
    return DocumentIntelResult(
        title=title,
        source_path=source_path,
        summary=" ".join(summary_bits),
        nodes=nodes,
        edges=edges,
        points=points,
        entities=entities,
        citations=citations,
        suggested_layer=PublicIntelLayer(
            name=title,
            kind=layer_kind,
            visible=True,
            color="#dc2626" if layer_kind == "foia" else "#f97316" if layer_kind == "surveillance" else "#2563eb",
            notes="Auto-generated from a document ingest.",
            payload=payload,
        ),
        payload=payload,
    )


def ingest_document_text(title: str, text: str, source_label: str = "manual") -> DocumentIntelResult:
    path = Path(source_label)
    if path.exists():
        return ingest_document_file(path, layer_name=title, source_type="manual")
    fake_path = f"manual:{source_label}"
    lower_text = text.lower()
    agencies = _extract_key_lines(text, AGENCY_KEYWORDS)
    vendors = _extract_key_lines(text, VENDOR_KEYWORDS)
    points = _extract_coordinates(text)
    nodes = [Node(id=_stable_id("document", f"{title}:{source_label}"), label=title, kind="Document", metadata={"source_path": fake_path, "source_type": "manual"})]
    summary = "Parsed manual text input."
    if any(keyword in lower_text for keyword in FOIA_KEYWORDS):
        summary = "FOIA/public records signals detected in manual text."
    if any(keyword in lower_text for keyword in SURVEILLANCE_KEYWORDS):
        summary = "Surveillance technology signals detected in manual text."
    return DocumentIntelResult(
        title=title,
        source_path=fake_path,
        summary=summary,
        nodes=nodes,
        edges=[],
        points=points,
        entities=[{"kind": "Agency", "label": agency} for agency in agencies] + [{"kind": "Vendor", "label": vendor} for vendor in vendors],
        citations=[
            _build_citation(
                entity_type="document",
                entity_id=nodes[0].id,
                source_type="manual",
                notes="Manual text ingestion",
                payload={"title": title, "source_label": source_label},
            )
        ],
        suggested_layer=PublicIntelLayer(
            name=title,
            kind="manual",
            visible=True,
            color="#2563eb",
            notes="Manual text ingest.",
            payload={"source": source_label},
        ),
        payload={"source": source_label, "text": text[:4000]},
    )


def summarize_heatmap(points: list[dict[str, Any]], precision: float = 0.05) -> HeatmapSummary:
    buckets: dict[tuple[float, float], int] = {}
    for point in points:
        latitude = point.get("latitude")
        longitude = point.get("longitude")
        if latitude is None or longitude is None:
            continue
        lat = round(float(latitude) / precision) * precision
        lon = round(float(longitude) / precision) * precision
        buckets[(lat, lon)] = buckets.get((lat, lon), 0) + 1
    cells = [
        {"latitude": lat, "longitude": lon, "count": count}
        for (lat, lon), count in sorted(buckets.items(), key=lambda item: item[1], reverse=True)
    ]
    hotspots = cells[:10]
    if hotspots:
        top = hotspots[0]
        summary = f"Heatmap peak at {top['latitude']:.3f}, {top['longitude']:.3f} with {top['count']} nearby points."
    else:
        summary = "No geo-coded points are available yet."
    return HeatmapSummary(total_points=sum(buckets.values()), precision=precision, cells=cells, hotspots=hotspots, summary=summary)


def export_layer_geojson(points: list[dict[str, Any]], output: Path, layer_name: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    features = []
    for point in points:
        if "latitude" not in point or "longitude" not in point:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
                "properties": {key: value for key, value in point.items() if key not in {"latitude", "longitude"}},
            }
        )
    payload = {"type": "FeatureCollection", "name": layer_name, "features": features}
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output


def export_layer_csv(points: list[dict[str, Any]], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "latitude", "longitude", "kind"])
        for point in points:
            if "latitude" not in point or "longitude" not in point:
                continue
            writer.writerow([point.get("label", ""), point.get("latitude", ""), point.get("longitude", ""), point.get("kind", "")])
    return output


def build_playback_frames(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frames = []
    for index, event in enumerate(sorted(events, key=lambda row: row.get("created_at", ""))):
        frames.append(
            {
                "index": index,
                "timestamp": event.get("created_at", ""),
                "kind": event.get("kind", "event"),
                "label": event.get("label") or event.get("title") or event.get("query") or event.get("target") or event.get("source") or "event",
                "payload": event,
            }
        )
    return frames


def confidence_from_source(source_type: str, confidence: float | None = None) -> float:
    source_type = source_type.lower().strip()
    defaults = {
        "official_contract": 0.95,
        "official_record": 0.95,
        "agenda": 0.8,
        "agenda_item": 0.8,
        "vendor_case_study": 0.6,
        "manual": 0.3,
        "manual_note": 0.3,
        "public_page": 0.7,
        "foia_response": 0.9,
        "email": 0.85,
        "csv": 0.75,
        "excel": 0.75,
        "pdf": 0.85,
    }
    score = defaults.get(source_type, 0.5)
    if confidence is not None:
        score = min(1.0, max(0.0, (score + confidence) / 2))
    return round(score, 2)


def confidence_label(score: float) -> str:
    if score >= 0.9:
        return "confirmed"
    if score >= 0.75:
        return "strong"
    if score >= 0.5:
        return "moderate"
    return "low"


def detect_duplicate_points(points: list[dict[str, Any]], *, precision: int = 5) -> list[dict[str, Any]]:
    seen: dict[tuple[float, float, str], dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for point in points:
        latitude = point.get("latitude")
        longitude = point.get("longitude")
        label = str(point.get("label", "")).strip().lower()
        if latitude is None or longitude is None:
            continue
        key = (round(float(latitude), precision), round(float(longitude), precision), label)
        if key in seen:
            duplicates.append({"original": seen[key], "duplicate": point})
            continue
        seen[key] = point
    return duplicates


def geocode_value(value: str, *, fallback_label: str | None = None, force: bool = False) -> GeocodeResult:
    query = value.strip()
    if not query:
        return GeocodeResult(query=query, label=fallback_label or "", latitude=None, longitude=None, confidence=0.0, source="empty")
    try:
        from geopy.geocoders import Nominatim  # type: ignore
    except Exception:
        return GeocodeResult(query=query, label=fallback_label or query, latitude=None, longitude=None, confidence=0.0, source="geopy-unavailable")

    geocoder = Nominatim(user_agent="echomap/1.0")
    candidates = [query]
    if force and fallback_label:
        candidates.insert(0, fallback_label)
    try:
        location = geocoder.geocode(candidates[0], timeout=8, exactly_one=True)
        if location:
            return GeocodeResult(
                query=query,
                label=fallback_label or query,
                latitude=float(location.latitude),
                longitude=float(location.longitude),
                confidence=0.92,
                source="geopy:nominatim",
                payload={"address": getattr(location, "address", ""), "raw": getattr(location, "raw", {})},
            )
    except Exception:
        pass
    return GeocodeResult(query=query, label=fallback_label or query, latitude=None, longitude=None, confidence=0.0, source="geocode-failed")


def import_tabular_data(path: Path, *, title: str | None = None, source_type: str | None = None) -> SpreadsheetImportResult:
    suffix = path.suffix.lower()
    rows: list[dict[str, Any]] = []
    if suffix in {".xlsx", ".xls"}:
        try:
            import pandas as pd  # type: ignore

            frame = pd.read_excel(path)
            rows = frame.fillna("").to_dict(orient="records")
        except Exception:
            rows = []
    else:
        with path.open("r", newline="", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]

    title = title or path.stem.replace("_", " ").strip() or "Imported Data"
    nodes: list[Node] = [Node(id=_stable_id("dataset", str(path)), label=title, kind="Dataset", metadata={"source_path": str(path), "source_type": source_type or suffix.lstrip(".") or "csv"})]
    edges: list[Edge] = []
    points: list[dict[str, Any]] = []
    citations: list[SourceCitation] = []
    summary_bits: list[str] = [f"Imported {len(rows)} row(s) from {path.name}."]
    column_names = list(rows[0].keys()) if rows else []

    address_columns = [col for col in column_names if any(key in col.lower() for key in ("address", "location", "street", "site"))]
    name_columns = [col for col in column_names if any(key in col.lower() for key in ("agency", "department", "vendor", "company", "name"))]
    lat_columns = [col for col in column_names if any(key in col.lower() for key in ("lat", "latitude"))]
    lon_columns = [col for col in column_names if any(key in col.lower() for key in ("lon", "lng", "longitude"))]

    for index, row in enumerate(rows[:500]):
        row_id = _stable_id("row", f"{path}:{index}:{json.dumps(row, sort_keys=True, default=str)}")
        row_label = _normalize_label(str(row.get(name_columns[0]) if name_columns else row.get("name") or row.get("label") or f"Row {index + 1}"))
        row_node = Node(id=row_id, label=row_label, kind="Record", metadata={"source_path": str(path), "row_index": index, "data": row})
        nodes.append(row_node)
        edges.append(Edge(id=_stable_id("edge", f"{nodes[0].id}->{row_id}:contains"), source=nodes[0].id, target=row_id, relation="contains", confidence=0.9, metadata={"source": "tabular import"}))

        point = {}
        if lat_columns and lon_columns:
            try:
                point = {
                    "label": row_label,
                    "latitude": float(row.get(lat_columns[0])),
                    "longitude": float(row.get(lon_columns[0])),
                    "kind": "dataset",
                    "row_index": index,
                }
            except Exception:
                point = {}
        if not point and address_columns:
            geocode = geocode_value(str(row.get(address_columns[0], "")), fallback_label=row_label)
            if geocode.latitude is not None and geocode.longitude is not None:
                point = {
                    "label": row_label,
                    "latitude": geocode.latitude,
                    "longitude": geocode.longitude,
                    "kind": "dataset",
                    "confidence": geocode.confidence,
                    "row_index": index,
                }
                citations.append(
                    _build_citation(
                        entity_type="location",
                        entity_id=row_id,
                        source_type=source_type or suffix.lstrip(".") or "csv",
                        uploaded_path=str(path),
                        confidence=geocode.confidence,
                        notes="Geocoded from tabular address data",
                        payload=geocode.payload,
                    )
                )
        if point:
            points.append(point)
        if row.get("email"):
            citations.append(
                _build_citation(
                    entity_type="contact",
                    entity_id=row_id,
                    source_type=source_type or suffix.lstrip(".") or "csv",
                    uploaded_path=str(path),
                    confidence=0.8,
                    notes="Contact row included email field",
                    payload={"email": row.get("email")},
                )
            )

    duplicates = detect_duplicate_points(points)
    if duplicates:
        summary_bits.append(f"Duplicate detection flagged {len(duplicates)} point pair(s).")

    payload = {
        "source_path": str(path),
        "source_type": source_type or suffix.lstrip(".") or "csv",
        "columns": column_names,
        "row_count": len(rows),
        "address_columns": address_columns,
        "name_columns": name_columns,
        "duplicate_count": len(duplicates),
    }
    return SpreadsheetImportResult(
        title=title,
        rows=rows,
        nodes=nodes,
        edges=edges,
        points=points,
        citations=citations,
        summary=" ".join(summary_bits),
        payload=payload,
    )


def build_agency_profile(db, name: str, workspace_id: int | None = None) -> AgencyProfile:
    workspace_id = workspace_id or getattr(db, "active_workspace_id", 1)
    lowered = name.lower()
    layers = [layer for layer in db.list_public_layers(limit=200, workspace_id=workspace_id) if lowered in layer["name"].lower()]
    requests = [request for request in db.list_public_requests(limit=200, workspace_id=workspace_id) if lowered in request["agency"].lower() or lowered in request["subject"].lower()]
    citations = [citation for citation in db.list_source_citations(limit=200, workspace_id=workspace_id) if lowered in citation["entity_id"].lower() or lowered in citation["notes"].lower()]
    documents = [artifact for artifact in db.recent_artifacts(limit=50, workspace_id=workspace_id) if lowered in json.dumps(artifact.get("payload", {})).lower()]
    related_agencies = sorted(
        {
            request["agency"]
            for request in requests
            if request["agency"].lower() != lowered
        }
    )
    vendors: set[str] = set()
    mapped_assets: list[dict[str, Any]] = []
    for citation in citations:
        payload = citation.get("payload", {})
        if not isinstance(payload, dict):
            continue
        label = payload.get("label") or payload.get("vendor") or payload.get("agency") or payload.get("entity")
        if isinstance(label, str) and label.strip():
            vendors.add(label.strip())
        if "latitude" in payload and "longitude" in payload:
            mapped_assets.append(payload)
    confidence_score = confidence_from_source("official_record")
    if requests:
        confidence_score = min(1.0, confidence_score + 0.03)
    if citations:
        confidence_score = min(1.0, confidence_score + 0.02)
    notes = f"{len(requests)} request(s), {len(citations)} citation(s), {len(layers)} layer(s)."
    return AgencyProfile(
        name=name,
        address="",
        contact_email="",
        records_officer="",
        vendors=vendors,
        requests=requests,
        citations=citations,
        documents=documents,
        related_agencies=related_agencies,
        mapped_assets=mapped_assets,
        confidence_score=round(confidence_score, 2),
        notes=notes,
        payload={"workspace_id": workspace_id, "layer_ids": [layer["id"] for layer in layers]},
    )


def snapshot_public_source(db, source_key: str, text: str, *, title: str, source_type: str = "public_page", workspace_id: int | None = None) -> dict[str, Any]:
    workspace_id = workspace_id or getattr(db, "active_workspace_id", 1)
    snapshot = {
        "source_key": source_key,
        "title": title,
        "source_type": source_type,
        "text": text,
        "text_hash": _stable_id("hash", text[:200000]),
    }
    db.add_archaeology_snapshot(source_key, source_type, snapshot, workspace_id=workspace_id)
    return snapshot


def compare_public_snapshots(old_text: str, new_text: str, *, title: str, source_key: str) -> ChangeDetectionResult:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"{title}:before", tofile=f"{title}:after", lineterm=""))
    added = [line[2:] for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
    removed = [line[2:] for line in diff_lines if line.startswith("-") and not line.startswith("---")]
    summary = f"{len(added)} line(s) added, {len(removed)} line(s) removed."
    return ChangeDetectionResult(
        title=title,
        source_key=source_key,
        base_text=old_text,
        current_text=new_text,
        summary=summary,
        added_lines=added,
        removed_lines=removed,
        diff_lines=diff_lines,
        payload={"added_count": len(added), "removed_count": len(removed)},
    )


def build_echotrail(db, seed: str, workspace_id: int | None = None, limit: int = 8) -> EchoTrailResult:
    workspace_id = workspace_id or getattr(db, "active_workspace_id", 1)
    graph = db.export_graph()
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_index = {node["id"]: node for node in nodes if node.get("id")}
    edge_index = {edge["id"]: edge for edge in edges if edge.get("id")}
    seed_lower = seed.lower().strip()
    evidence_rows = [
        row
        for row in _workspace_text_rows(db, workspace_id)
        if _contains_query(row.get("text", ""), seed_lower) or _contains_query(row.get("label", ""), seed_lower)
    ]
    matched_nodes = [node for node in nodes if seed_lower in str(node.get("label", "")).lower() or seed_lower in str(node.get("kind", "")).lower() or seed_lower in json.dumps(node.get("metadata", {})).lower()]
    trails: list[dict[str, Any]] = []
    trail_nodes: list[dict[str, Any]] = []
    trail_edges: list[dict[str, Any]] = []
    trail_node_ids: set[str] = set()
    trail_edge_ids: set[str] = set()

    def add_trail_node(node: dict[str, Any] | None) -> None:
        if not node:
            return
        node_id = str(node.get("id", ""))
        if not node_id or node_id in trail_node_ids:
            return
        trail_node_ids.add(node_id)
        trail_nodes.append(node)

    def add_trail_edge(edge: dict[str, Any] | None) -> None:
        if not edge:
            return
        edge_id = str(edge.get("id", ""))
        if not edge_id or edge_id in trail_edge_ids:
            return
        trail_edge_ids.add(edge_id)
        trail_edges.append(edge)

    def add_step(stage: str, kind: str, label: str, summary: str, payload: dict[str, Any]) -> None:
        trails.append(
            {
                "stage": stage,
                "kind": kind,
                "label": label,
                "summary": summary,
                "payload": payload,
            }
        )

    for row in evidence_rows[: max(1, limit // 2)]:
        add_step(
            "evidence",
            row.get("kind", "evidence"),
            row.get("label", seed),
            _normalize_label(str(row.get("text", "")))[:240],
            row.get("payload", {}),
        )

    if matched_nodes:
        origin = matched_nodes[0]
        chains = build_relationship_chains(nodes, edges, origin["id"], limit=limit, max_depth=4)
        add_step(
            "entity",
            origin.get("kind", "Entity"),
            origin.get("label", seed),
            f"Matched graph entity '{origin.get('label', seed)}'.",
            origin,
        )
        add_trail_node(origin)
        expanded_roots: list[str] = []
        for chain in chains[:limit]:
            for node_id in chain.node_ids:
                add_trail_node(node_index.get(node_id))
            for edge_id in chain.edge_ids:
                add_trail_edge(edge_index.get(edge_id))
            add_step(
                "relationship",
                chain.target_kind,
                chain.target_label,
                chain.summary,
                {
                    "node_ids": chain.node_ids,
                    "edge_ids": chain.edge_ids,
                    "steps": chain.steps,
                },
            )
            if chain.target_id in expanded_roots:
                continue
            expanded_roots.append(chain.target_id)
            root_node = node_index.get(chain.target_id)
            if not root_node:
                continue
            neighbor_rows = db.neighbors(chain.target_id, limit=max(4, limit))
            if not neighbor_rows:
                continue
            neighborhood_items: list[dict[str, Any]] = []
            for neighbor in neighbor_rows:
                add_trail_edge(edge_index.get(neighbor.get("edge_id")))
                other_id = neighbor["target"] if neighbor["source"] == chain.target_id else neighbor["source"]
                other_node = node_index.get(other_id)
                if other_node:
                    add_trail_node(other_node)
                neighborhood_items.append(
                    {
                        "node_id": other_id,
                        "label": neighbor.get("target_label") if neighbor["source"] == chain.target_id else neighbor.get("source_label"),
                        "kind": neighbor.get("target_kind") if neighbor["source"] == chain.target_id else neighbor.get("source_kind"),
                        "relation": neighbor.get("relation", ""),
                        "confidence": neighbor.get("confidence", 0.0),
                        "edge_id": neighbor.get("edge_id"),
                    }
                )
            add_step(
                "neighborhood",
                root_node.get("kind", chain.target_kind),
                root_node.get("label", chain.target_label),
                f"{root_node.get('label', chain.target_label)} neighborhood expanded to {len(neighborhood_items)} adjacent node(s).",
                {
                    "root_id": chain.target_id,
                    "root_label": root_node.get("label", chain.target_label),
                    "neighbor_count": len(neighborhood_items),
                    "neighbors": neighborhood_items[: min(6, len(neighborhood_items))],
                },
            )

    if not trails:
        add_step(
            "search",
            "seed",
            seed,
            "No matching evidence was found yet. Try a different agency, vendor, or technology name.",
            {},
        )

    summary = f"EchoTrail for '{seed}' assembled {len(trails)} step(s) across {len(matched_nodes)} matching entity(ies) and {len(evidence_rows)} evidence item(s)."
    return EchoTrailResult(
        seed=seed,
        summary=summary,
        steps=trails,
        nodes=trail_nodes,
        edges=trail_edges,
        payload={
            "workspace_id": workspace_id,
            "matched_entities": len(matched_nodes),
            "evidence_items": len(evidence_rows),
            "neighborhood_nodes": len(trail_nodes),
            "neighborhood_edges": len(trail_edges),
        },
    )


def agency_radar(db, query: str, workspace_id: int | None = None, limit: int = 20) -> AgencyRadarResult:
    workspace_id = workspace_id or getattr(db, "active_workspace_id", 1)
    query_lower = query.lower().strip()
    hits: list[dict[str, Any]] = []
    possible_agencies: set[str] = set()
    possible_vendors: set[str] = set()

    for node in db.search_nodes(query, limit=limit):
        hits.append(
            {
                "label": node["label"],
                "kind": node["kind"],
                "source": "graph",
                "confidence": confidence_from_source("official_record"),
                "evidence": f"Graph node match for '{query}'.",
                "payload": node,
            }
        )
        if node["kind"].lower() in {"agency", "company", "organization"}:
            possible_agencies.add(node["label"])
        if node["kind"].lower() in {"vendor", "technology", "framework", "library"}:
            possible_vendors.add(node["label"])

    for request in db.list_public_requests(limit=200, workspace_id=workspace_id):
        text = " ".join([request.get("agency", ""), request.get("subject", ""), request.get("notes", ""), json.dumps(request.get("payload", {}))]).lower()
        if query_lower not in text:
            continue
        possible_agencies.add(request.get("agency", ""))
        payload = request.get("payload", {})
        vendor = payload.get("vendor_contact") if isinstance(payload, dict) else ""
        if isinstance(vendor, str) and vendor.strip():
            possible_vendors.add(vendor.strip())
        hits.append(
            {
                "label": request.get("agency", "Request"),
                "kind": "request",
                "source": "public_request",
                "confidence": confidence_from_source("official_contract"),
                "evidence": request.get("subject", ""),
                "payload": request,
            }
        )

    for citation in db.list_source_citations(limit=200, workspace_id=workspace_id):
        text = " ".join([citation.get("entity_type", ""), citation.get("entity_id", ""), citation.get("source_type", ""), citation.get("notes", ""), json.dumps(citation.get("payload", {}))]).lower()
        if query_lower not in text:
            continue
        if citation.get("entity_type", "").lower() == "agency":
            possible_agencies.add(citation.get("entity_id", ""))
        if citation.get("entity_type", "").lower() in {"vendor", "technology"}:
            possible_vendors.add(citation.get("entity_id", ""))
        hits.append(
            {
                "label": citation.get("entity_id", "Citation"),
                "kind": citation.get("entity_type", "citation"),
                "source": citation.get("source_type", "citation"),
                "confidence": float(citation.get("confidence", 0.7)),
                "evidence": citation.get("notes", ""),
                "payload": citation,
            }
        )

    for layer in db.list_public_layers(limit=100, workspace_id=workspace_id):
        text = " ".join([layer.get("name", ""), layer.get("notes", ""), json.dumps(layer.get("payload", {}))]).lower()
        if query_lower not in text:
            continue
        hits.append(
            {
                "label": layer.get("name", "Layer"),
                "kind": layer.get("kind", "layer"),
                "source": "public_layer",
                "confidence": confidence_from_source("public_page"),
                "evidence": layer.get("notes", ""),
                "payload": layer,
            }
        )

    for artifact in db.recent_artifacts(limit=100, workspace_id=workspace_id):
        text = json.dumps(artifact.get("payload", {})).lower()
        if query_lower not in text:
            continue
        hits.append(
            {
                "label": artifact.get("kind", "artifact"),
                "kind": artifact.get("kind", "artifact"),
                "source": "artifact",
                "confidence": confidence_from_source("manual"),
                "evidence": "Artifact payload match",
                "payload": artifact,
            }
        )

    hits.sort(key=lambda item: (-float(item.get("confidence", 0.0)), item.get("label", ""), item.get("source", "")))
    summary = f"Agency Radar found {len(hits)} clue(s) for '{query}' across {len(possible_agencies)} agency candidate(s) and {len(possible_vendors)} vendor candidate(s)."
    return AgencyRadarResult(
        query=query,
        summary=summary,
        hits=hits[:limit],
        possible_agencies=sorted(a for a in possible_agencies if a),
        possible_vendors=sorted(v for v in possible_vendors if v),
        payload={"workspace_id": workspace_id, "hit_count": len(hits)},
    )


def surveillance_radius(
    db,
    *,
    latitude: float,
    longitude: float,
    radius_km: float = 1.5,
    center_label: str = "",
    workspace_id: int | None = None,
    limit: int = 50,
) -> SurveillanceRadiusResult:
    workspace_id = workspace_id or getattr(db, "active_workspace_id", 1)
    points = _collect_public_map_points(db, workspace_id)
    grouped: dict[str, list[dict[str, Any]]] = {"schools": [], "roads": [], "neighborhoods": [], "government": [], "cameras": [], "other": []}
    nearby: list[dict[str, Any]] = []
    for point in points:
        try:
            point_lat = float(point["latitude"])
            point_lon = float(point["longitude"])
        except Exception:
            continue
        distance = _haversine_km(latitude, longitude, point_lat, point_lon)
        if distance > radius_km:
            continue
        category = _classify_map_point(point)
        entry = {
            "label": point.get("label", "Point"),
            "kind": point.get("kind", "point"),
            "latitude": point_lat,
            "longitude": point_lon,
            "distance_km": round(distance, 3),
            "category": category,
            "payload": point.get("payload", {}),
        }
        grouped.setdefault(category, []).append(entry)
        nearby.append(entry)

    for category in grouped:
        grouped[category] = sorted(grouped[category], key=lambda item: item["distance_km"])[:limit]
    nearby.sort(key=lambda item: item["distance_km"])
    overlays = _build_gis_overlay_layers(grouped)
    summary = f"Surveillance radius around {center_label or f'{latitude:.4f}, {longitude:.4f}'} found {len(nearby)} nearby point(s) within {radius_km:.2f} km."
    return SurveillanceRadiusResult(
        center_label=center_label or f"{latitude:.4f}, {longitude:.4f}",
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        summary=summary,
        groups=grouped,
        overlays=overlays,
        points=nearby,
        payload={"workspace_id": workspace_id, "coverage_area_sq_km": round(math.pi * radius_km * radius_km, 3)},
    )


def export_public_radius_package(result: SurveillanceRadiusResult, output: Path) -> Path:
    title = f"Surveillance Radius: {result.center_label}"
    center = (result.latitude, result.longitude)
    return export_gis_layer_package(title=title, overlays=result.overlays, output=output, center=center)


def _render_public_map_html(
    *,
    title: str,
    layers: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    points: list[dict[str, Any]],
) -> str:
    map_points = json.dumps(points, indent=2)
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{title}</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }}
          #map {{ height: 70vh; }}
          .panel {{ padding: 16px; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
          th, td {{ border: 1px solid #334155; padding: 8px; text-align: left; }}
        </style>
      </head>
      <body>
        <div class="panel">
          <h1>{title}</h1>
          <p>Interactive map export with layers, requests, citations, and geocoded points.</p>
        </div>
        <div id="map"></div>
        <script>
          const map = L.map('map').setView([39.5, -98.35], 4);
          L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18 }}).addTo(map);
          const points = {map_points};
          const bounds = [];
          points.forEach(point => {{
            if (point.latitude == null || point.longitude == null) return;
            const marker = L.circleMarker([point.latitude, point.longitude], {{
              radius: 6,
              color: '#60a5fa',
              fillColor: '#38bdf8',
              fillOpacity: 0.85,
            }}).addTo(map);
            marker.bindPopup(`<strong>${{point.label || 'Point'}}</strong><br/>${{point.kind || ''}}`);
            bounds.push([point.latitude, point.longitude]);
          }});
          if (bounds.length) {{
            map.fitBounds(bounds, {{ padding: [20, 20] }});
          }}
        </script>
        <div class="panel">
          <h2>Layers</h2>
          <table><thead><tr><th>Name</th><th>Kind</th><th>Visible</th></tr></thead><tbody>
          {''.join(f"<tr><td>{layer.get('name','')}</td><td>{layer.get('kind','')}</td><td>{layer.get('visible', True)}</td></tr>" for layer in layers)}
          </tbody></table>
          <h2>Requests</h2>
          <table><thead><tr><th>Agency</th><th>Subject</th><th>Status</th></tr></thead><tbody>
          {''.join(f"<tr><td>{request.get('agency','')}</td><td>{request.get('subject','')}</td><td>{request.get('status','')}</td></tr>" for request in requests)}
          </tbody></table>
          <h2>Citations</h2>
          <table><thead><tr><th>Entity</th><th>Source</th><th>Confidence</th></tr></thead><tbody>
          {''.join(f"<tr><td>{citation.get('entity_type','')}:{citation.get('entity_id','')}</td><td>{citation.get('source_type','')}</td><td>{citation.get('confidence','')}</td></tr>" for citation in citations)}
          </tbody></table>
        </div>
      </body>
    </html>
    """


def export_public_map_html(
    *,
    title: str,
    layers: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    points: list[dict[str, Any]],
    output: Path,
) -> Path:
    html_doc = _render_public_map_html(title=title, layers=layers, requests=requests, citations=citations, points=points)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc, encoding="utf-8")
    return output


def export_public_map_geojson(points: list[dict[str, Any]], output: Path, title: str) -> Path:
    return export_layer_geojson(points, output, title)


def export_public_map_csv(points: list[dict[str, Any]], output: Path) -> Path:
    return export_layer_csv(points, output)


def export_public_map_bundle(
    *,
    title: str,
    layers: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    points: list[dict[str, Any]],
    output: Path,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = io.BytesIO()
    html_payload = _render_public_map_html(
        title=title,
        layers=layers,
        requests=requests,
        citations=citations,
        points=points,
    )
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"title": title, "layers": layers, "requests": requests, "citations": citations}, indent=2))
        zf.writestr("points.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
                "properties": {key: value for key, value in point.items() if key not in {"latitude", "longitude"}},
            }
            for point in points
            if "latitude" in point and "longitude" in point
        ]}, indent=2))
        zf.writestr("points.csv", "label,latitude,longitude,kind\n" + "\n".join(
            f"{point.get('label','')},{point.get('latitude','')},{point.get('longitude','')},{point.get('kind','')}"
            for point in points
            if "latitude" in point and "longitude" in point
        ))
        zf.writestr("map.html", html_payload)
    output.write_bytes(bundle.getvalue())
    return output
