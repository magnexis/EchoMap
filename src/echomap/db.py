from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .backends import BackendInfo, Neo4jBackend, PostgresBackend, SQLiteBackend
from .models import Edge, Node
from .services.live import GraphEventHub


class Database:
    def __init__(self, path: Path, backend_kind: str = "sqlite", backend_dsn: str | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = self._select_backend(backend_kind, backend_dsn)
        self.events = GraphEventHub()
        self._backend_warning: str | None = None
        self._init_schema()

    def _select_backend(self, backend_kind: str, backend_dsn: str | None) -> SQLiteBackend | PostgresBackend | Neo4jBackend:
        backend_kind = (backend_kind or "sqlite").lower()
        if backend_kind == "postgres" or backend_kind == "postgresql":
            return PostgresBackend(backend_dsn or "postgresql://localhost/echomap")
        if backend_kind == "neo4j":
            return Neo4jBackend(backend_dsn or "neo4j://localhost:7687")
        return SQLiteBackend(self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source) REFERENCES nodes(id),
                    FOREIGN KEY(target) REFERENCES nodes(id)
                );

                CREATE TABLE IF NOT EXISTS discoveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    query TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    node_id TEXT,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(node_id) REFERENCES nodes(id)
                );

                CREATE TABLE IF NOT EXISTS archaeology_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    target TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investigations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL,
                    query TEXT NOT NULL,
                    selected_node_id TEXT,
                    notes TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bookmarks (
                    node_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    left_id TEXT NOT NULL,
                    right_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS public_layers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    visible INTEGER NOT NULL DEFAULT 1,
                    color TEXT NOT NULL DEFAULT '#2563eb',
                    notes TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS public_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    agency TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    request_date TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_date TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    attachments TEXT NOT NULL DEFAULT '[]',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_citations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    uploaded_path TEXT NOT NULL DEFAULT '',
                    screenshot_path TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    retrieved_at TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signature_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    center_label TEXT NOT NULL DEFAULT '',
                    radius_km REAL NOT NULL DEFAULT 1.5,
                    notes TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_kind_created_at ON nodes(kind, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_nodes_label_created_at ON nodes(label, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_edges_source_target_created_at ON edges(source, target, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_bookmarks_created_at ON bookmarks(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_comparisons_created_at ON comparisons(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_annotations_target_created_at ON annotations(target_type, target_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_public_layers_created_at ON public_layers(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_public_requests_created_at ON public_requests(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_citations_created_at ON source_citations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_signature_presets_created_at ON signature_presets(created_at DESC);
                """
            )
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self._ensure_column("investigations", "tags", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("discoveries", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("artifacts", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("archaeology_snapshots", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("investigations", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("public_layers", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("public_requests", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("source_citations", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("signature_presets", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workspaces (id, name, description, notes, created_at, updated_at) VALUES (1, 'Default Workspace', 'Local workspace for EchoMap', '', datetime('now'), datetime('now'))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_investigations_workspace_created_at ON investigations(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discoveries_workspace_created_at ON discoveries(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_workspace_created_at ON artifacts(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archaeology_workspace_created_at ON archaeology_snapshots(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_public_layers_workspace_created_at ON public_layers(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_public_requests_workspace_created_at ON public_requests(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_citations_workspace_created_at ON source_citations(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signature_presets_workspace_created_at ON signature_presets(workspace_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_investigations_tags_created_at ON investigations(tags, created_at DESC)")
        self.active_workspace_id = 1

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        with self.connect() as conn:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def list_workspaces(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "notes": row["notes"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_active": int(row["id"]) == int(self.active_workspace_id),
            }
            for row in rows
        ]

    def get_workspace(self, workspace_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "is_active": int(row["id"]) == int(self.active_workspace_id),
        }

    def save_workspace(self, name: str, description: str = "", notes: str = "") -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workspaces (name, description, notes, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    notes = excluded.notes,
                    updated_at = datetime('now')
                """,
                (name, description, notes),
            )
            workspace_id = int(cursor.lastrowid or conn.execute("SELECT id FROM workspaces WHERE name = ?", (name,)).fetchone()[0])
        self.emit_graph_event("workspace_saved", {"id": workspace_id, "name": name})
        return workspace_id

    def update_workspace(self, workspace_id: int, name: str, description: str = "", notes: str = "") -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE workspaces SET name = ?, description = ?, notes = ?, updated_at = datetime('now') WHERE id = ?",
                (name, description, notes, workspace_id),
            )
            updated = cursor.rowcount > 0
        if updated:
            self.emit_graph_event("workspace_updated", {"id": workspace_id, "name": name})
        return updated

    def set_active_workspace(self, workspace_id: int) -> bool:
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return False
        self.active_workspace_id = workspace_id
        self.emit_graph_event("workspace_changed", {"id": workspace_id, "name": workspace["name"]})
        return True

    def active_workspace(self) -> dict:
        workspace = self.get_workspace(self.active_workspace_id)
        return workspace or {"id": 1, "name": "Default Workspace", "description": "", "notes": "", "is_active": True}

    def _mirror(self, method_name: str, *args) -> None:
        method = getattr(self.backend, method_name, None)
        if not callable(method):
            return
        try:
            self.backend.ensure_schema()
            method(*args)
            self._backend_warning = None
        except Exception as exc:  # pragma: no cover - optional remote backend
            self._backend_warning = f"{method_name}: {exc}"

    def emit_graph_event(self, event_type: str, payload: dict) -> None:
        self.events.emit(
            event_type,
            {
                **payload,
                "stats": self.stats(),
            },
        )

    def upsert_nodes(self, nodes: list[Node]) -> None:
        if not nodes:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO nodes (id, label, kind, metadata, created_at)
                VALUES (:id, :label, :kind, :metadata, :created_at)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label,
                    kind=excluded.kind,
                    metadata=excluded.metadata
                """,
                [
                    {
                        "id": node.id,
                        "label": node.label,
                        "kind": node.kind,
                        "metadata": json.dumps(node.metadata),
                        "created_at": node.created_at,
                    }
                    for node in nodes
                ],
            )
        self._mirror("upsert_nodes", nodes)
        self.emit_graph_event("nodes_upserted", {"count": len(nodes), "node_ids": [node.id for node in nodes[:25]]})

    def upsert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO edges (id, source, target, relation, confidence, metadata, created_at)
                VALUES (:id, :source, :target, :relation, :confidence, :metadata, :created_at)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    target=excluded.target,
                    relation=excluded.relation,
                    confidence=excluded.confidence,
                    metadata=excluded.metadata
                """,
                [
                    {
                        "id": edge.id,
                        "source": edge.source,
                        "target": edge.target,
                        "relation": edge.relation,
                        "confidence": edge.confidence,
                        "metadata": json.dumps(edge.metadata),
                        "created_at": edge.created_at,
                    }
                    for edge in edges
                ],
            )
        self._mirror("upsert_edges", edges)
        self.emit_graph_event("edges_upserted", {"count": len(edges), "edge_ids": [edge.id for edge in edges[:25]]})

    def add_discovery(self, query: str, summary: str, workspace_id: int | None = None) -> None:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO discoveries (workspace_id, query, summary, created_at) VALUES (?, ?, ?, datetime('now'))",
                (workspace_id, query, summary),
            )
        self._mirror("add_discovery", query, summary)
        self.emit_graph_event("discovery_added", {"query": query, "summary": summary})

    def add_artifact(self, node_id: str | None, kind: str, payload: dict, workspace_id: int | None = None) -> None:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (workspace_id, node_id, kind, payload, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (workspace_id, node_id, kind, json.dumps(payload)),
            )
        self._mirror("add_artifact", node_id, kind, payload)
        self.emit_graph_event("artifact_added", {"node_id": node_id, "kind": kind, "payload": payload})

    def recent_artifacts(self, limit: int = 25, kind: str | None = None, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                    (workspace_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE workspace_id = ? AND kind = ? ORDER BY id DESC LIMIT ?",
                    (workspace_id, kind, limit),
                ).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "node_id": row["node_id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def add_archaeology_snapshot(self, target: str, source: str, payload: dict, workspace_id: int | None = None) -> None:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO archaeology_snapshots (workspace_id, target, source, payload, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (workspace_id, target, source, json.dumps(payload)),
            )
        self._mirror("add_archaeology_snapshot", target, source, payload)
        self.emit_graph_event("archaeology_snapshot_added", {"target": target, "source": source, "payload": payload})

    def save_investigation(
        self,
        title: str,
        query: str,
        selected_node_id: str | None,
        notes: str,
        payload: dict,
        tags: str = "",
        workspace_id: int | None = None,
    ) -> int:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO investigations (workspace_id, title, query, selected_node_id, notes, tags, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (workspace_id, title, query, selected_node_id, notes, tags, json.dumps(payload)),
            )
            investigation_id = int(cursor.lastrowid)
        self.emit_graph_event("investigation_saved", {"id": investigation_id, "title": title, "tags": tags})
        return investigation_id

    def list_investigations(self, limit: int = 50, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM investigations WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "title": row["title"],
                "query": row["query"],
                "selected_node_id": row["selected_node_id"],
                "notes": row["notes"],
                "tags": row["tags"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def search_investigations(self, query: str, limit: int = 50, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        term = f"%{query.strip()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM investigations
                WHERE workspace_id = ? AND (title LIKE ? OR query LIKE ? OR notes LIKE ? OR tags LIKE ? OR payload LIKE ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (workspace_id, term, term, term, term, term, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "title": row["title"],
                "query": row["query"],
                "selected_node_id": row["selected_node_id"],
                "notes": row["notes"],
                "tags": row["tags"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_investigation(self, investigation_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "title": row["title"],
            "query": row["query"],
            "selected_node_id": row["selected_node_id"],
            "notes": row["notes"],
            "tags": row["tags"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    def update_investigation(
        self,
        investigation_id: int,
        title: str,
        query: str,
        selected_node_id: str | None,
        notes: str,
        payload: dict,
        tags: str = "",
        workspace_id: int | None = None,
    ) -> bool:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE investigations
                SET title = ?, query = ?, selected_node_id = ?, notes = ?, tags = ?, payload = ?, workspace_id = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (title, query, selected_node_id, notes, tags, json.dumps(payload), workspace_id, investigation_id, workspace_id),
            )
            updated = cursor.rowcount > 0
        if updated:
            self.emit_graph_event("investigation_updated", {"id": investigation_id, "title": title, "tags": tags})
        return updated

    def delete_investigation(self, investigation_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM investigations WHERE id = ? AND workspace_id = ?", (investigation_id, self.active_workspace_id))
            deleted = cursor.rowcount > 0
        if deleted:
            self.emit_graph_event("investigation_deleted", {"id": investigation_id})
        return deleted

    def bookmark_node(self, node: dict, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bookmarks (node_id, label, kind, note, payload, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(node_id) DO UPDATE SET
                    label=excluded.label,
                    kind=excluded.kind,
                    note=excluded.note,
                    payload=excluded.payload
                """,
                (
                    node["id"],
                    node["label"],
                    node["kind"],
                    note,
                    json.dumps(node),
                ),
            )
        self.emit_graph_event("bookmark_saved", {"node_id": node["id"], "label": node["label"], "kind": node["kind"]})

    def update_bookmark_note(self, node_id: str, note: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("UPDATE bookmarks SET note = ? WHERE node_id = ?", (note, node_id))
            updated = cursor.rowcount > 0
        if updated:
            self.emit_graph_event("bookmark_updated", {"node_id": node_id, "note": note})
        return updated

    def remove_bookmark(self, node_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM bookmarks WHERE node_id = ?", (node_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self.emit_graph_event("bookmark_removed", {"node_id": node_id})
        return deleted

    def list_bookmarks(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM bookmarks ORDER BY created_at DESC").fetchall()
        return [
            {
                "node_id": row["node_id"],
                "label": row["label"],
                "kind": row["kind"],
                "note": row["note"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_bookmark(self, node_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM bookmarks WHERE node_id = ?", (node_id,)).fetchone()
        if not row:
            return None
        return {
            "node_id": row["node_id"],
            "label": row["label"],
            "kind": row["kind"],
            "note": row["note"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    def save_comparison(self, left_id: str, right_id: str, payload: dict) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO comparisons (left_id, right_id, payload, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (left_id, right_id, json.dumps(payload)),
            )
            comparison_id = int(cursor.lastrowid)
        self.emit_graph_event("comparison_saved", {"id": comparison_id, "left_id": left_id, "right_id": right_id})
        return comparison_id

    def save_annotation(self, target_type: str, target_id: str, title: str, body: str, payload: dict | None = None) -> int:
        payload = payload or {}
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO annotations (target_type, target_id, title, body, payload, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (target_type, target_id, title, body, json.dumps(payload)),
            )
            annotation_id = int(cursor.lastrowid)
        self.emit_graph_event(
            "annotation_saved",
            {
                "id": annotation_id,
                "target_type": target_type,
                "target_id": target_id,
                "title": title,
            },
        )
        return annotation_id

    def list_annotations(self, target_type: str | None = None, target_id: str | None = None, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            if target_type and target_id:
                rows = conn.execute(
                    """
                    SELECT * FROM annotations
                    WHERE target_type = ? AND target_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (target_type, target_id, limit),
                ).fetchall()
            elif target_type:
                rows = conn.execute(
                    """
                    SELECT * FROM annotations
                    WHERE target_type = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (target_type, limit),
                ).fetchall()
            elif target_id:
                rows = conn.execute(
                    """
                    SELECT * FROM annotations
                    WHERE target_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (target_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM annotations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": row["id"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "title": row["title"],
                "body": row["body"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_annotation(self, annotation_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self.emit_graph_event("annotation_deleted", {"id": annotation_id})
        return deleted

    def save_public_layer(
        self,
        name: str,
        kind: str,
        visible: bool = True,
        color: str = "#2563eb",
        notes: str = "",
        payload: dict | None = None,
        workspace_id: int | None = None,
    ) -> int:
        payload = payload or {}
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO public_layers (workspace_id, name, kind, visible, color, notes, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (workspace_id, name, kind, int(bool(visible)), color, notes, json.dumps(payload)),
            )
            layer_id = int(cursor.lastrowid)
        self.emit_graph_event("public_layer_saved", {"id": layer_id, "name": name, "kind": kind, "visible": visible})
        return layer_id

    def list_public_layers(self, include_hidden: bool = True, limit: int = 100, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        query = "SELECT * FROM public_layers WHERE workspace_id = ?"
        params: tuple = (workspace_id,)
        if not include_hidden:
            query += " AND visible = 1"
        query += " ORDER BY id DESC LIMIT ?"
        params = (*params, limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "name": row["name"],
                "kind": row["kind"],
                "visible": bool(row["visible"]),
                "color": row["color"],
                "notes": row["notes"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def set_public_layer_visibility(self, layer_id: int, visible: bool) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE public_layers SET visible = ?, updated_at = datetime('now') WHERE id = ?",
                (int(bool(visible)), layer_id),
            )
            updated = cursor.rowcount > 0
        if updated:
            self.emit_graph_event("public_layer_visibility_changed", {"id": layer_id, "visible": visible})
        return updated

    def save_public_request(
        self,
        agency: str,
        subject: str,
        request_date: str,
        due_date: str,
        status: str,
        response_date: str = "",
        notes: str = "",
        attachments: list[str] | None = None,
        payload: dict | None = None,
        workspace_id: int | None = None,
    ) -> int:
        attachments = attachments or []
        payload = payload or {}
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO public_requests (
                    workspace_id, agency, subject, request_date, due_date, status, response_date, notes, attachments, payload, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    workspace_id,
                    agency,
                    subject,
                    request_date,
                    due_date,
                    status,
                    response_date,
                    notes,
                    json.dumps(attachments),
                    json.dumps(payload),
                ),
            )
            request_id = int(cursor.lastrowid)
        self.emit_graph_event("public_request_saved", {"id": request_id, "agency": agency, "status": status})
        return request_id

    def list_public_requests(self, limit: int = 100, status: str | None = None, agency: str | None = None, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        clauses = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if agency:
            clauses.append("agency LIKE ?")
            params.append(f"%{agency}%")
        query = "SELECT * FROM public_requests WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "agency": row["agency"],
                "subject": row["subject"],
                "request_date": row["request_date"],
                "due_date": row["due_date"],
                "status": row["status"],
                "response_date": row["response_date"],
                "notes": row["notes"],
                "attachments": json.loads(row["attachments"]),
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_public_request(self, request_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM public_requests WHERE id = ? AND workspace_id = ?",
                (request_id, self.active_workspace_id),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "agency": row["agency"],
            "subject": row["subject"],
            "request_date": row["request_date"],
            "due_date": row["due_date"],
            "status": row["status"],
            "response_date": row["response_date"],
            "notes": row["notes"],
            "attachments": json.loads(row["attachments"]),
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_public_request(
        self,
        request_id: int,
        agency: str,
        subject: str,
        request_date: str,
        due_date: str,
        status: str,
        response_date: str = "",
        notes: str = "",
        attachments: list[str] | None = None,
        payload: dict | None = None,
        workspace_id: int | None = None,
    ) -> bool:
        attachments = attachments or []
        payload = payload or {}
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE public_requests
                SET agency = ?, subject = ?, request_date = ?, due_date = ?, status = ?, response_date = ?,
                    notes = ?, attachments = ?, payload = ?, updated_at = datetime('now'), workspace_id = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    agency,
                    subject,
                    request_date,
                    due_date,
                    status,
                    response_date,
                    notes,
                    json.dumps(attachments),
                    json.dumps(payload),
                    workspace_id,
                    request_id,
                    workspace_id,
                ),
            )
            updated = cursor.rowcount > 0
        if updated:
            self.emit_graph_event("public_request_updated", {"id": request_id, "agency": agency, "status": status})
        return updated

    def save_source_citation(
        self,
        entity_type: str,
        entity_id: str,
        source_type: str,
        source_url: str = "",
        uploaded_path: str = "",
        screenshot_path: str = "",
        confidence: float = 1.0,
        retrieved_at: str = "",
        notes: str = "",
        payload: dict | None = None,
        workspace_id: int | None = None,
    ) -> int:
        payload = payload or {}
        retrieved_at = retrieved_at or ""
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_citations (
                    workspace_id, entity_type, entity_id, source_type, source_url, uploaded_path, screenshot_path,
                    confidence, retrieved_at, notes, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    workspace_id,
                    entity_type,
                    entity_id,
                    source_type,
                    source_url,
                    uploaded_path,
                    screenshot_path,
                    confidence,
                    retrieved_at,
                    notes,
                    json.dumps(payload),
                ),
            )
            citation_id = int(cursor.lastrowid)
        self.emit_graph_event("source_citation_saved", {"id": citation_id, "entity_type": entity_type, "entity_id": entity_id})
        return citation_id

    def list_source_citations(self, limit: int = 200, entity_type: str | None = None, entity_id: str | None = None, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        clauses = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        query = "SELECT * FROM source_citations WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "source_type": row["source_type"],
                "source_url": row["source_url"],
                "uploaded_path": row["uploaded_path"],
                "screenshot_path": row["screenshot_path"],
                "confidence": row["confidence"],
                "retrieved_at": row["retrieved_at"],
                "notes": row["notes"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_signature_preset(
        self,
        name: str,
        mode: str,
        query: str = "",
        center_label: str = "",
        radius_km: float = 1.5,
        notes: str = "",
        payload: dict | None = None,
        workspace_id: int | None = None,
    ) -> int:
        payload = payload or {}
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signature_presets (
                    workspace_id, name, mode, query, center_label, radius_km, notes, payload, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (workspace_id, name, mode, query, center_label, radius_km, notes, json.dumps(payload)),
            )
            preset_id = int(cursor.lastrowid)
        self.emit_graph_event("signature_preset_saved", {"id": preset_id, "name": name, "mode": mode})
        return preset_id

    def list_signature_presets(self, limit: int = 100, mode: str | None = None, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        clauses = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        query = "SELECT * FROM signature_presets WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "name": row["name"],
                "mode": row["mode"],
                "query": row["query"],
                "center_label": row["center_label"],
                "radius_km": row["radius_km"],
                "notes": row["notes"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_signature_preset(self, preset_id: int, workspace_id: int | None = None) -> dict | None:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM signature_presets WHERE id = ? AND workspace_id = ?",
                (preset_id, workspace_id),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "name": row["name"],
            "mode": row["mode"],
            "query": row["query"],
            "center_label": row["center_label"],
            "radius_km": row["radius_km"],
            "notes": row["notes"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_signature_preset(self, preset_id: int, workspace_id: int | None = None) -> bool:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM signature_presets WHERE id = ? AND workspace_id = ?",
                (preset_id, workspace_id),
            )
            deleted = cursor.rowcount > 0
        if deleted:
            self.emit_graph_event("signature_preset_deleted", {"id": preset_id, "workspace_id": workspace_id})
        return deleted

    def public_timeline_events(self, limit: int = 200) -> list[dict]:
        events: list[dict] = []
        for row in self.recent_discoveries(limit=limit):
            events.append(
                {
                    "kind": "discovery",
                    "created_at": row["created_at"],
                    "title": row["query"],
                    "label": row["query"],
                    "summary": row["summary"],
                    "payload": row,
                }
            )
        for row in self.recent_archaeology(limit=limit):
            events.append(
                {
                    "kind": "archaeology",
                    "created_at": row["created_at"],
                    "title": row["target"],
                    "label": row["target"],
                    "summary": row["source"],
                    "payload": row,
                }
            )
        for row in self.list_public_requests(limit=limit):
            events.append(
                {
                    "kind": "public_request",
                    "created_at": row["created_at"],
                    "title": row["agency"],
                    "label": row["subject"],
                    "summary": row["status"],
                    "payload": row,
                }
            )
        for row in self.list_source_citations(limit=limit):
            events.append(
                {
                    "kind": "citation",
                    "created_at": row["created_at"],
                    "title": row["entity_id"],
                    "label": row["entity_type"],
                    "summary": row["source_type"],
                    "payload": row,
                }
            )
        events.sort(key=lambda row: row.get("created_at", ""), reverse=True)
        return events[:limit]

    def recent_comparisons(self, limit: int = 25) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM comparisons ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": row["id"],
                "left_id": row["left_id"],
                "right_id": row["right_id"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_comparison(self, comparison_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comparisons WHERE id = ?", (comparison_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "left_id": row["left_id"],
            "right_id": row["right_id"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    def list_nodes(self, limit: int = 500) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM nodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "kind": row["kind"],
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_edges(self, limit: int = 1000) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "source": row["source"],
                "target": row["target"],
                "relation": row["relation"],
                "confidence": row["confidence"],
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_node(self, node_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "label": row["label"],
            "kind": row["kind"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
        }

    def neighbors(self, node_id: str, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id AS edge_id, e.source, e.target, e.relation, e.confidence, e.metadata, e.created_at,
                       s.label AS source_label, s.kind AS source_kind,
                       t.label AS target_label, t.kind AS target_kind
                FROM edges e
                LEFT JOIN nodes s ON s.id = e.source
                LEFT JOIN nodes t ON t.id = e.target
                WHERE e.source = ? OR e.target = ?
                ORDER BY e.confidence DESC, e.created_at DESC
                LIMIT ?
                """,
                (node_id, node_id, limit),
            ).fetchall()
        return [
            {
                "edge_id": row["edge_id"],
                "source": row["source"],
                "target": row["target"],
                "relation": row["relation"],
                "confidence": row["confidence"],
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
                "source_label": row["source_label"],
                "source_kind": row["source_kind"],
                "target_label": row["target_label"],
                "target_kind": row["target_kind"],
            }
            for row in rows
        ]

    def search_nodes(self, query: str, limit: int = 25) -> list[dict]:
        term = f"%{query.strip()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM nodes
                WHERE label LIKE ? OR kind LIKE ? OR metadata LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (term, term, term, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "kind": row["kind"],
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def node_degree_map(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT n.id, n.label, n.kind,
                       COUNT(e.id) AS degree
                FROM nodes n
                LEFT JOIN edges e ON e.source = n.id OR e.target = n.id
                GROUP BY n.id, n.label, n.kind
                ORDER BY degree DESC, n.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            discovery_count = conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]
            artifact_count = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            annotation_count = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
            public_layer_count = conn.execute("SELECT COUNT(*) FROM public_layers").fetchone()[0]
            public_request_count = conn.execute("SELECT COUNT(*) FROM public_requests").fetchone()[0]
            source_citation_count = conn.execute("SELECT COUNT(*) FROM source_citations").fetchone()[0]
        return {
            "nodes": int(node_count),
            "edges": int(edge_count),
            "discoveries": int(discovery_count),
            "artifacts": int(artifact_count),
            "annotations": int(annotation_count),
            "public_layers": int(public_layer_count),
            "public_requests": int(public_request_count),
            "source_citations": int(source_citation_count),
        }

    def recent_discoveries(self, limit: int = 25, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM discoveries WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_archaeology(self, limit: int = 50, workspace_id: int | None = None) -> list[dict]:
        workspace_id = workspace_id or self.active_workspace_id
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archaeology_snapshots WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def backend_info(self) -> BackendInfo:
        info = self.backend.info()
        if self._backend_warning:
            return BackendInfo(
                kind=info.kind,
                status="degraded",
                description=f"{info.description} | mirror warning: {self._backend_warning}",
            )
        return info

    def backend_snapshot(self) -> dict[str, int | str]:
        nodes_reader = getattr(self.backend, "list_nodes", None)
        edges_reader = getattr(self.backend, "list_edges", None)
        if not callable(nodes_reader) or not callable(edges_reader):
            return {"nodes": 0, "edges": 0, "mode": "unavailable"}
        try:
            nodes = nodes_reader(limit=200)
            edges = edges_reader(limit=500)
            return {
                "nodes": len(nodes),
                "edges": len(edges),
                "mode": "readable",
                "sample": nodes[0]["label"] if nodes else "none",
            }
        except Exception as exc:  # pragma: no cover - optional backend read path
            return {"nodes": 0, "edges": 0, "mode": f"error: {exc}"}

    def export_graph(self) -> dict:
        nodes = self.list_nodes()
        edges = self.list_edges()
        latest_discovery = next(iter(self.recent_artifacts(limit=1, kind="discovery")), None)
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": self.stats(),
            "bookmarks": self.list_bookmarks(),
            "annotations": self.list_annotations(limit=200),
            "investigations": self.list_investigations(),
            "comparisons": self.recent_comparisons(),
            "public_layers": self.list_public_layers(limit=200),
            "public_requests": self.list_public_requests(limit=200),
            "source_citations": self.list_source_citations(limit=200),
            "timeline_events": self.public_timeline_events(limit=200),
            "latest_discovery_profile": (latest_discovery or {}).get("payload", {}).get("tech_profile", {}),
        }
