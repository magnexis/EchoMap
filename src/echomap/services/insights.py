from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter


@dataclass(slots=True)
class GraphInsights:
    kind_counts: dict[str, int] = field(default_factory=dict)
    relation_counts: dict[str, int] = field(default_factory=dict)
    top_hubs: list[dict] = field(default_factory=list)
    technology_counts: dict[str, int] = field(default_factory=dict)
    summaries: list[str] = field(default_factory=list)
    anomaly_explanations: list[str] = field(default_factory=list)


def analyze_graph(nodes: list[dict], edges: list[dict]) -> GraphInsights:
    kind_counts = Counter(node.get("kind", "Unknown") for node in nodes)
    relation_counts = Counter(edge.get("relation", "unknown") for edge in edges)
    degree = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    node_index = {node["id"]: node for node in nodes}
    top_hubs = []
    for node_id, score in degree.most_common(12):
        node = node_index.get(node_id)
        if not node:
            continue
        top_hubs.append(
            {
                "id": node_id,
                "label": node["label"],
                "kind": node["kind"],
                "degree": score,
            }
        )

    technology_counts = Counter(node["label"] for node in nodes if node.get("kind") == "Technology")
    summaries: list[str] = []
    anomaly_explanations: list[str] = []
    isolated_count = sum(1 for score in degree.values() if score == 0)
    if top_hubs:
        summaries.append(f"Hub node: {top_hubs[0]['label']} ({top_hubs[0]['degree']} connections)")
    if technology_counts:
        tech_name, tech_count = technology_counts.most_common(1)[0]
        summaries.append(f"Dominant technology signal: {tech_name}")
    if kind_counts:
        strongest_kind, strongest_count = kind_counts.most_common(1)[0]
        summaries.append(f"Most common entity type: {strongest_kind} ({strongest_count})")
    if relation_counts:
        strongest_relation, strongest_relation_count = relation_counts.most_common(1)[0]
        summaries.append(f"Most frequent relationship: {strongest_relation} ({strongest_relation_count})")
    if isolated_count:
        anomaly_explanations.append(
            f"{isolated_count} isolated nodes are present. These are likely seed nodes, newly discovered entities, or records that still need enrichment."
        )
    orphan_tech = [node for node in nodes if node.get("kind") == "Technology" and degree.get(node["id"], 0) == 0]
    if orphan_tech:
        anomaly_explanations.append(
            f"{len(orphan_tech)} technology nodes have no supporting relationships. They may be stale fingerprints or pending graph expansion."
        )
    if top_hubs and top_hubs[0]["degree"] > max(6, len(edges) // 4):
        anomaly_explanations.append(
            f"Graph activity is concentrated around {top_hubs[0]['label']}. That can indicate a central platform, a hub-and-spoke ecosystem, or an overfocused seed."
        )
    if not anomaly_explanations and nodes:
        anomaly_explanations.append("No obvious structural anomalies were detected in the current graph slice.")

    return GraphInsights(
        kind_counts=dict(kind_counts),
        relation_counts=dict(relation_counts),
        top_hubs=top_hubs,
        technology_counts=dict(technology_counts),
        summaries=summaries,
        anomaly_explanations=anomaly_explanations,
    )
