from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ComparisonResult:
    left: dict[str, Any]
    right: dict[str, Any]
    shared_neighbors: list[dict[str, Any]] = field(default_factory=list)
    shared_relations: list[str] = field(default_factory=list)
    summary: str = ""
    score: float = 0.0


@dataclass(slots=True)
class GraphComparisonResult:
    left_node_count: int
    right_node_count: int
    shared_node_ids: list[str] = field(default_factory=list)
    left_only_node_ids: list[str] = field(default_factory=list)
    right_only_node_ids: list[str] = field(default_factory=list)
    shared_edge_ids: list[str] = field(default_factory=list)
    left_only_edge_ids: list[str] = field(default_factory=list)
    right_only_edge_ids: list[str] = field(default_factory=list)
    summary: str = ""
    overlap_score: float = 0.0


def compare_nodes(left: dict[str, Any], right: dict[str, Any], left_neighbors: list[dict], right_neighbors: list[dict]) -> ComparisonResult:
    left_neighbor_ids = {
        row["target"] if row["source"] == left["id"] else row["source"]
        for row in left_neighbors
    }
    right_neighbor_ids = {
        row["target"] if row["source"] == right["id"] else row["source"]
        for row in right_neighbors
    }
    shared_ids = left_neighbor_ids & right_neighbor_ids
    shared_neighbors = []
    for neighbor_id in shared_ids:
        left_match = next((row for row in left_neighbors if row["source"] == neighbor_id or row["target"] == neighbor_id), None)
        right_match = next((row for row in right_neighbors if row["source"] == neighbor_id or row["target"] == neighbor_id), None)
        if left_match or right_match:
            match = left_match or right_match
            shared_neighbors.append(
                {
                    "id": neighbor_id,
                    "label": match.get("target_label") or match.get("source_label"),
                }
            )

    left_relations = {row["relation"] for row in left_neighbors}
    right_relations = {row["relation"] for row in right_neighbors}
    shared_relations = sorted(left_relations & right_relations)

    label_overlap = len(set(left["label"].lower().split()) & set(right["label"].lower().split()))
    kind_bonus = 0.15 if left["kind"] == right["kind"] else 0.0
    neighbor_score = min(1.0, len(shared_ids) / max(1, len(left_neighbor_ids | right_neighbor_ids)))
    relation_score = min(1.0, len(shared_relations) / max(1, len(left_relations | right_relations)))
    score = round(min(1.0, 0.35 * neighbor_score + 0.25 * relation_score + 0.25 * kind_bonus + 0.15 * (1.0 if label_overlap else 0.0)), 2)

    summary = (
        f"{left['label']} and {right['label']} share {len(shared_ids)} neighbors, "
        f"{len(shared_relations)} relationship types, and a similarity score of {score:.2f}."
    )
    return ComparisonResult(
        left=left,
        right=right,
        shared_neighbors=shared_neighbors[:12],
        shared_relations=shared_relations[:12],
        summary=summary,
        score=score,
    )


def compare_graphs(left_graph: dict[str, Any], right_graph: dict[str, Any]) -> GraphComparisonResult:
    left_nodes = {node["id"]: node for node in left_graph.get("nodes", [])}
    right_nodes = {node["id"]: node for node in right_graph.get("nodes", [])}
    left_edges = {edge["id"]: edge for edge in left_graph.get("edges", [])}
    right_edges = {edge["id"]: edge for edge in right_graph.get("edges", [])}

    shared_node_ids = sorted(left_nodes.keys() & right_nodes.keys())
    left_only_node_ids = sorted(left_nodes.keys() - right_nodes.keys())
    right_only_node_ids = sorted(right_nodes.keys() - left_nodes.keys())
    shared_edge_ids = sorted(left_edges.keys() & right_edges.keys())
    left_only_edge_ids = sorted(left_edges.keys() - right_edges.keys())
    right_only_edge_ids = sorted(right_edges.keys() - left_edges.keys())

    node_overlap = len(shared_node_ids) / max(1, len(left_nodes.keys() | right_nodes.keys()))
    edge_overlap = len(shared_edge_ids) / max(1, len(left_edges.keys() | right_edges.keys()))
    overlap_score = round(min(1.0, 0.65 * node_overlap + 0.35 * edge_overlap), 2)
    summary = (
        f"Shared nodes: {len(shared_node_ids)} | Shared edges: {len(shared_edge_ids)} | "
        f"Overlap score: {overlap_score:.2f}"
    )
    return GraphComparisonResult(
        left_node_count=len(left_nodes),
        right_node_count=len(right_nodes),
        shared_node_ids=shared_node_ids[:50],
        left_only_node_ids=left_only_node_ids[:50],
        right_only_node_ids=right_only_node_ids[:50],
        shared_edge_ids=shared_edge_ids[:50],
        left_only_edge_ids=left_only_edge_ids[:50],
        right_only_edge_ids=right_only_edge_ids[:50],
        summary=summary,
        overlap_score=overlap_score,
    )
