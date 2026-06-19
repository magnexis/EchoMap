from __future__ import annotations

from typing import Any

from ..db import Database
from ..models import utc_now_iso


def resolve_reference_label(db: Database, reference: str) -> str:
    if reference == "live":
        return "live workspace"
    if reference.startswith("investigation:"):
        try:
            investigation_id = int(reference.split(":", 1)[1])
        except ValueError:
            return reference
        investigation = db.get_investigation(investigation_id)
        return investigation["title"] if investigation else reference
    node = db.get_node(reference)
    if node:
        return node["label"]
    return reference


def build_report_context(
    db: Database,
    investigation_id: int | None = None,
    comparison_id: int | None = None,
    live: bool = False,
) -> dict[str, Any]:
    if comparison_id is not None:
        comparison = db.get_comparison(comparison_id)
        if not comparison:
            raise ValueError(f"Comparison {comparison_id} was not found.")
        payload = comparison["payload"]
        left_id = comparison["left_id"]
        right_id = comparison["right_id"]
        return {
            "kind": "comparison",
            "title": f"EchoMap Comparison Report #{comparison_id}",
            "subtitle": f"{resolve_reference_label(db, left_id)} vs {resolve_reference_label(db, right_id)}",
            "generated_at": utc_now_iso(),
            "summary": payload.get("summary", ""),
            "comparison": {
                **payload,
                "left_id": left_id,
                "right_id": right_id,
                "comparison_id": comparison_id,
            },
        }

    if investigation_id is not None:
        investigation = db.get_investigation(investigation_id)
        if not investigation:
            raise ValueError(f"Investigation {investigation_id} was not found.")
        payload = investigation["payload"]
        return {
            "kind": "investigation",
            "title": f"EchoMap Investigation Report: {investigation['title']}",
            "subtitle": investigation["query"],
            "generated_at": utc_now_iso(),
            "summary": investigation.get("notes") or payload.get("discovery", {}).get("summary", ""),
            "graph": payload.get("graph", {}),
            "investigation": investigation,
            "discovery": payload.get("discovery", {}),
        }

    if live:
        graph = db.export_graph()
        latest_discovery = next(iter(db.recent_artifacts(limit=1, kind="discovery")), None)
        discovery = (latest_discovery or {}).get("payload", {})
        return {
            "kind": "workspace",
            "title": "EchoMap Workspace Report",
            "subtitle": "Live graph snapshot",
            "generated_at": utc_now_iso(),
            "summary": "Current live workspace snapshot.",
            "graph": graph,
            "discovery": discovery,
        }

    return build_report_context(db, live=True)
