from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ..db import Database
from .discovery import discover


@dataclass(slots=True)
class ScanJob:
    query: str
    depth: int = 0


class BackgroundScanner:
    def __init__(
        self,
        db: Database,
        max_depth: int = 1,
        on_result: Callable[[], None] | None = None,
        auto_start: bool = True,
    ) -> None:
        self.db = db
        self.max_depth = max_depth
        self.on_result = on_result
        self._queue: deque[ScanJob] = deque()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._processed = 0
        self._errors = 0
        self._last_job: ScanJob | None = None
        self._last_error: str | None = None
        self._started = False
        if auto_start:
            self.start()

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        self._started = True

    def enqueue(self, query: str, depth: int = 0) -> None:
        normalized = query.strip()
        if not normalized:
            return
        with self._lock:
            if normalized in self._seen:
                return
            self._seen.add(normalized)
            self._queue.append(ScanJob(normalized, depth))

    def enqueue_many(self, queries: list[str], depth: int = 0) -> None:
        for query in queries:
            self.enqueue(query, depth)

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def set_max_depth(self, depth: int) -> None:
        self.max_depth = max(0, depth)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._seen.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "queued": len(self._queue),
                "seen": len(self._seen),
                "processed": self._processed,
                "errors": self._errors,
                "paused": self._paused.is_set(),
                "last_job": None if not self._last_job else {"query": self._last_job.query, "depth": self._last_job.depth},
                "last_error": self._last_error,
                "max_depth": self.max_depth,
                "queue": [{"query": job.query, "depth": job.depth} for job in list(self._queue)],
            }

    def _pop_job(self) -> ScanJob | None:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.4)
                continue
            job = self._pop_job()
            if not job:
                self._stop.wait(0.4)
                continue
            try:
                with self._lock:
                    self._last_job = job
                result = discover(job.query)
                self.db.upsert_nodes(result.nodes)
                self.db.upsert_edges(result.edges)
                self.db.add_discovery(result.root_query, f"Background scan: {result.summary}")
                self.db.add_artifact(
                    None,
                    "background_scan",
                    {
                        "query": result.root_query,
                        "summary": result.summary,
                        "related_targets": result.related_targets,
                        "archaeology": result.archaeology,
                    },
                )
                if result.related_targets and job.depth < self.max_depth:
                    for target in result.related_targets[:8]:
                        self.enqueue(target, job.depth + 1)
                if self.on_result:
                    self.on_result()
                with self._lock:
                    self._processed += 1
                    self._last_error = None
            except Exception as exc:  # pragma: no cover - resilient background work
                self.db.add_artifact(None, "background_scan_error", {"query": job.query, "error": str(exc)})
                with self._lock:
                    self._errors += 1
                    self._last_error = str(exc)
