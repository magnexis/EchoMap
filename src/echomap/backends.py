from __future__ import annotations

import json
import re
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .models import Edge, Node


@dataclass(slots=True)
class BackendInfo:
    kind: str
    status: str
    description: str


class GraphBackend(Protocol):
    kind: str

    def info(self) -> BackendInfo: ...

    def ensure_schema(self) -> None: ...

    def upsert_nodes(self, nodes: list[Node]) -> None: ...

    def upsert_edges(self, edges: list[Edge]) -> None: ...

    def add_discovery(self, query: str, summary: str) -> None: ...

    def add_artifact(self, node_id: str | None, kind: str, payload: dict) -> None: ...

    def add_archaeology_snapshot(self, target: str, source: str, payload: dict) -> None: ...

    def list_nodes(self, limit: int = 500) -> list[dict]: ...

    def list_edges(self, limit: int = 1000) -> list[dict]: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_]", "_", value.strip()) or "Unknown"
    if label[0].isdigit():
        label = f"N_{label}"
    return label[:48]


def _neo4j_auth_from_uri(uri: str) -> tuple[str, tuple[str, str] | None]:
    parsed = urlparse(uri)
    if parsed.username:
        return uri, (parsed.username, parsed.password or "")
    return uri, None


@dataclass(slots=True)
class SQLiteBackend:
    path: Path
    kind: str = "sqlite"

    @contextmanager
    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def info(self) -> BackendInfo:
        return BackendInfo(kind=self.kind, status="ready", description=f"SQLite local store at {self.path}")

    def ensure_schema(self) -> None:
        return None

    def upsert_nodes(self, nodes: list[Node]) -> None:
        return None

    def upsert_edges(self, edges: list[Edge]) -> None:
        return None

    def add_discovery(self, query: str, summary: str) -> None:
        return None

    def add_artifact(self, node_id: str | None, kind: str, payload: dict) -> None:
        return None

    def add_archaeology_snapshot(self, target: str, source: str, payload: dict) -> None:
        return None

    def list_nodes(self, limit: int = 500) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM nodes ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except Exception:
            return []
        return [dict(row) for row in rows]

    def list_edges(self, limit: int = 1000) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM edges ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except Exception:
            return []
        return [dict(row) for row in rows]


@dataclass(slots=True)
class PostgresBackend:
    dsn: str
    kind: str = "postgresql"
    _schema_ready: bool = False

    def _connection(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install psycopg to enable the PostgreSQL backend.") from exc
        return psycopg.connect(self.dsn)

    def info(self) -> BackendInfo:
        try:
            self._ensure_driver()
            return BackendInfo(kind=self.kind, status="ready", description=f"PostgreSQL backend at {self.dsn}")
        except Exception as exc:  # pragma: no cover - optional dependency / runtime state
            return BackendInfo(kind=self.kind, status="degraded", description=f"{self.dsn} ({exc})")

    def _ensure_driver(self) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install psycopg to enable the PostgreSQL backend.") from exc

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echomap_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echomap_edges (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echomap_discoveries (
                    id BIGSERIAL PRIMARY KEY,
                    query TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echomap_artifacts (
                    id BIGSERIAL PRIMARY KEY,
                    node_id TEXT,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echomap_archaeology_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    target TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        self._schema_ready = True

    def upsert_nodes(self, nodes: list[Node]) -> None:
        if not nodes:
            return
        self.ensure_schema()
        with self._connection() as conn:
            for node in nodes:
                conn.execute(
                    """
                    INSERT INTO echomap_nodes (id, label, kind, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        kind = EXCLUDED.kind,
                        metadata = EXCLUDED.metadata
                    """,
                    (node.id, node.label, node.kind, _json(node.metadata), node.created_at),
                )
            conn.commit()

    def upsert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        self.ensure_schema()
        with self._connection() as conn:
            for edge in edges:
                conn.execute(
                    """
                    INSERT INTO echomap_edges (id, source, target, relation, confidence, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        source = EXCLUDED.source,
                        target = EXCLUDED.target,
                        relation = EXCLUDED.relation,
                        confidence = EXCLUDED.confidence,
                        metadata = EXCLUDED.metadata
                    """,
                    (edge.id, edge.source, edge.target, edge.relation, edge.confidence, _json(edge.metadata), edge.created_at),
                )
            conn.commit()

    def add_discovery(self, query: str, summary: str) -> None:
        self.ensure_schema()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO echomap_discoveries (query, summary, created_at) VALUES (%s, %s, NOW()::text)",
                (query, summary),
            )
            conn.commit()

    def add_artifact(self, node_id: str | None, kind: str, payload: dict) -> None:
        self.ensure_schema()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO echomap_artifacts (node_id, kind, payload, created_at) VALUES (%s, %s, %s, NOW()::text)",
                (node_id, kind, _json(payload)),
            )
            conn.commit()

    def add_archaeology_snapshot(self, target: str, source: str, payload: dict) -> None:
        self.ensure_schema()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO echomap_archaeology_snapshots (target, source, payload, created_at) VALUES (%s, %s, %s, NOW()::text)",
                (target, source, _json(payload)),
            )
            conn.commit()

    def list_nodes(self, limit: int = 500) -> list[dict]:
        self.ensure_schema()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, label, kind, metadata, created_at FROM echomap_nodes ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "label": row[1],
                "kind": row[2],
                "metadata": json.loads(row[3]) if isinstance(row[3], str) else row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def list_edges(self, limit: int = 1000) -> list[dict]:
        self.ensure_schema()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, source, target, relation, confidence, metadata, created_at FROM echomap_edges ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "source": row[1],
                "target": row[2],
                "relation": row[3],
                "confidence": float(row[4]),
                "metadata": json.loads(row[5]) if isinstance(row[5], str) else row[5],
                "created_at": row[6],
            }
            for row in rows
        ]


@dataclass(slots=True)
class Neo4jBackend:
    uri: str
    kind: str = "neo4j"
    _schema_ready: bool = False

    def _driver(self):
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install neo4j to enable the Neo4j backend.") from exc

        uri, auth = _neo4j_auth_from_uri(self.uri)
        if auth and not auth[0]:
            auth = None
        return GraphDatabase.driver(uri, auth=auth)

    @contextmanager
    def _session(self):
        driver = self._driver()
        try:
            with driver.session() as session:
                yield session
        finally:
            driver.close()

    def info(self) -> BackendInfo:
        driver = None
        try:
            driver = self._driver()
            driver.verify_connectivity()
            return BackendInfo(kind=self.kind, status="ready", description=f"Neo4j backend at {self.uri}")
        except Exception as exc:  # pragma: no cover - optional dependency / runtime state
            return BackendInfo(kind=self.kind, status="degraded", description=f"{self.uri} ({exc})")
        finally:
            if driver is not None:
                driver.close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._session() as session:
            session.run("CREATE CONSTRAINT echomap_node_id IF NOT EXISTS FOR (n:EchoMapNode) REQUIRE n.id IS UNIQUE")
            session.run("CREATE CONSTRAINT echomap_artifact_id IF NOT EXISTS FOR (n:EchoMapArtifact) REQUIRE n.id IS UNIQUE")
            session.run("CREATE CONSTRAINT echomap_snapshot_id IF NOT EXISTS FOR (n:EchoMapSnapshot) REQUIRE n.id IS UNIQUE")
        self._schema_ready = True

    def upsert_nodes(self, nodes: list[Node]) -> None:
        if not nodes:
            return
        self.ensure_schema()
        with self._session() as session:
            for node in nodes:
                labels = ":".join(("EchoMapNode", _safe_label(node.kind)))
                session.run(
                    f"""
                    MERGE (n:{labels} {{id: $id}})
                    SET n.label = $label,
                        n.kind = $kind,
                        n.metadata = $metadata,
                        n.created_at = $created_at
                    """,
                    id=node.id,
                    label=node.label,
                    kind=node.kind,
                    metadata=_json(node.metadata),
                    created_at=node.created_at,
                )

    def upsert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        self.ensure_schema()
        with self._session() as session:
            for edge in edges:
                session.run(
                    """
                    MERGE (source:EchoMapNode {id: $source})
                    MERGE (target:EchoMapNode {id: $target})
                    MERGE (source)-[r:CONNECTED_TO {id: $id}]->(target)
                    SET r.relation = $relation,
                        r.confidence = $confidence,
                        r.metadata = $metadata,
                        r.created_at = $created_at
                    """,
                    id=edge.id,
                    source=edge.source,
                    target=edge.target,
                    relation=edge.relation,
                    confidence=edge.confidence,
                    metadata=_json(edge.metadata),
                    created_at=edge.created_at,
                )

    def add_discovery(self, query: str, summary: str) -> None:
        self.ensure_schema()
        discovery_id = f"discovery:{_safe_label(query)}:{stable_hash(query)}"
        with self._session() as session:
            session.run(
                """
                MERGE (d:EchoMapDiscovery {id: $id})
                SET d.query = $query,
                    d.summary = $summary,
                    d.created_at = toString(datetime())
                """,
                id=discovery_id,
                query=query,
                summary=summary,
            )

    def add_artifact(self, node_id: str | None, kind: str, payload: dict) -> None:
        self.ensure_schema()
        artifact_id = f"artifact:{kind}:{stable_hash(_json(payload))}"
        with self._session() as session:
            session.run(
                """
                MERGE (a:EchoMapArtifact {id: $id})
                SET a.kind = $kind,
                    a.node_id = $node_id,
                    a.payload = $payload,
                    a.created_at = toString(datetime())
                """,
                id=artifact_id,
                kind=kind,
                node_id=node_id,
                payload=_json(payload),
            )

    def add_archaeology_snapshot(self, target: str, source: str, payload: dict) -> None:
        self.ensure_schema()
        snapshot_id = f"snapshot:{source}:{stable_hash(_json(payload))}"
        with self._session() as session:
            session.run(
                """
                MERGE (s:EchoMapSnapshot {id: $id})
                SET s.target = $target,
                    s.source = $source,
                    s.payload = $payload,
                    s.created_at = toString(datetime())
                """,
                id=snapshot_id,
                target=target,
                source=source,
                payload=_json(payload),
            )

    def list_nodes(self, limit: int = 500) -> list[dict]:
        self.ensure_schema()
        with self._session() as session:
            result = session.run(
                "MATCH (n:EchoMapNode) RETURN n.id AS id, n.label AS label, n.kind AS kind, n.metadata AS metadata, n.created_at AS created_at ORDER BY created_at DESC LIMIT $limit",
                limit=limit,
            )
            rows = []
            for record in result:
                metadata = record["metadata"]
                try:
                    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                except Exception:
                    metadata = {}
                rows.append(
                    {
                        "id": record["id"],
                        "label": record["label"],
                        "kind": record["kind"],
                        "metadata": metadata,
                        "created_at": record["created_at"],
                    }
                )
            return rows

    def list_edges(self, limit: int = 1000) -> list[dict]:
        self.ensure_schema()
        with self._session() as session:
            result = session.run(
                """
                MATCH (source:EchoMapNode)-[r:CONNECTED_TO]->(target:EchoMapNode)
                RETURN r.id AS id, r.source AS source, r.target AS target, r.relation AS relation,
                       r.confidence AS confidence, r.metadata AS metadata, r.created_at AS created_at
                ORDER BY created_at DESC
                LIMIT $limit
                """,
                limit=limit,
            )
            rows = []
            for record in result:
                metadata = record["metadata"]
                try:
                    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                except Exception:
                    metadata = {}
                rows.append(
                    {
                        "id": record["id"],
                        "source": record["source"],
                        "target": record["target"],
                        "relation": record["relation"],
                        "confidence": float(record["confidence"] or 1.0),
                        "metadata": metadata,
                        "created_at": record["created_at"],
                    }
                )
            return rows
