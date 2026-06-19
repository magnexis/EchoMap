from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Node:
    id: str
    label: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class Edge:
    id: str
    source: str
    target: str
    relation: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class DiscoveryResult:
    root_query: str
    nodes: list[Node]
    edges: list[Edge]
    timeline: list[dict[str, Any]]
    technologies: list[dict[str, Any]]
    archaeology: list[dict[str, Any]]
    summary: str
    tech_profile: dict[str, Any] = field(default_factory=dict)
    related_targets: list[str] = field(default_factory=list)
