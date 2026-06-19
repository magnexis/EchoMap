from __future__ import annotations

from dataclasses import dataclass
from collections import Counter, defaultdict, deque
from urllib.parse import urlparse


@dataclass(slots=True)
class RelationshipBundle:
    nodes: list
    edges: list


@dataclass(slots=True)
class RelationshipPathResult:
    start_id: str
    end_id: str
    node_ids: list[str]
    edge_ids: list[str]
    steps: list[dict]
    summary: str
    hop_count: int


@dataclass(slots=True)
class RelationshipChainResult:
    origin_id: str
    target_id: str
    target_label: str
    target_kind: str
    node_ids: list[str]
    edge_ids: list[str]
    steps: list[dict]
    summary: str
    hop_count: int


def domain_from_url(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path
    return host.lower().lstrip("www.")


def parent_domains(host: str) -> list[str]:
    parts = [part for part in host.split(".") if part]
    return [".".join(parts[i:]) for i in range(1, len(parts) - 1)] if len(parts) > 2 else []


def trace_relationship_path(nodes: list[dict], edges: list[dict], start_id: str, end_id: str) -> RelationshipPathResult:
    node_index = {node["id"]: node for node in nodes}
    if start_id not in node_index or end_id not in node_index:
        return RelationshipPathResult(
            start_id=start_id,
            end_id=end_id,
            node_ids=[],
            edge_ids=[],
            steps=[],
            summary="One or both entities are missing from the current graph.",
            hop_count=0,
        )

    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge))
        adjacency.setdefault(edge["target"], []).append((edge["source"], edge))

    queue = deque([start_id])
    visited = {start_id: None}
    parent_edge: dict[str, dict | None] = {start_id: None}

    while queue:
        current = queue.popleft()
        if current == end_id:
            break
        for neighbor_id, edge in adjacency.get(current, []):
            if neighbor_id in visited:
                continue
            visited[neighbor_id] = current
            parent_edge[neighbor_id] = edge
            queue.append(neighbor_id)

    if end_id not in visited:
        return RelationshipPathResult(
            start_id=start_id,
            end_id=end_id,
            node_ids=[],
            edge_ids=[],
            steps=[],
            summary=f"No relationship path found between {node_index[start_id]['label']} and {node_index[end_id]['label']}.",
            hop_count=0,
        )

    node_ids = [end_id]
    edge_ids: list[str] = []
    steps: list[dict] = []
    current = end_id
    while current != start_id:
        edge = parent_edge[current]
        previous = visited[current]
        if edge is None or previous is None:
            break
        edge_ids.append(edge["id"])
        node_ids.append(previous)
        steps.append(
            {
                "from": previous,
                "to": current,
                "relation": edge["relation"],
                "edge_id": edge["id"],
            }
        )
        current = previous

    node_ids.reverse()
    edge_ids.reverse()
    steps.reverse()
    start_label = node_index[start_id]["label"]
    end_label = node_index[end_id]["label"]
    summary = f"Shortest trace between {start_label} and {end_label}: {len(edge_ids)} hop(s)."
    return RelationshipPathResult(
        start_id=start_id,
        end_id=end_id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        steps=steps,
        summary=summary,
        hop_count=len(edge_ids),
    )


def build_relationship_chains(
    nodes: list[dict],
    edges: list[dict],
    origin_id: str,
    limit: int = 6,
    max_depth: int = 3,
) -> list[RelationshipChainResult]:
    node_index = {node["id"]: node for node in nodes}
    if origin_id not in node_index:
        return []

    degree = Counter()
    adjacency: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
        adjacency[edge["source"]].append(edge)
        adjacency[edge["target"]].append(edge)

    candidate_ids: list[str] = []
    seen: set[str] = set()

    for edge in sorted(adjacency.get(origin_id, []), key=lambda item: (-float(item.get("confidence", 1.0)), item.get("relation", ""), item.get("id", ""))):
        other = edge["target"] if edge["source"] == origin_id else edge["source"]
        if other in seen or other == origin_id or other not in node_index:
            continue
        seen.add(other)
        candidate_ids.append(other)

    for node_id, _score in degree.most_common(20):
        if node_id == origin_id or node_id in seen or node_id not in node_index:
            continue
        seen.add(node_id)
        candidate_ids.append(node_id)

    chains: list[RelationshipChainResult] = []
    origin_label = node_index[origin_id]["label"]
    for target_id in candidate_ids:
        trace = trace_relationship_path(nodes, edges, origin_id, target_id)
        if trace.hop_count == 0 or trace.hop_count > max_depth:
            continue
        target = node_index[target_id]
        chains.append(
            RelationshipChainResult(
                origin_id=origin_id,
                target_id=target_id,
                target_label=target["label"],
                target_kind=target["kind"],
                node_ids=trace.node_ids,
                edge_ids=trace.edge_ids,
                steps=trace.steps,
                summary=f"{origin_label} -> {target['label']} ({trace.hop_count} hop(s))",
                hop_count=trace.hop_count,
            )
        )
        if len(chains) >= limit:
            break
    return chains
