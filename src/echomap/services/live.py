from __future__ import annotations

import json
import os
import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebSockets import QWebSocket

from ..models import utc_now_iso


@dataclass(slots=True)
class GraphEvent:
    type: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(slots=True, eq=False)
class GraphEventSubscription:
    hub: "GraphEventHub"
    queue: queue.Queue

    def close(self) -> None:
        self.hub.unsubscribe(self)

    def get(self, timeout: float | None = None) -> GraphEvent:
        return self.queue.get(timeout=timeout)


class GraphEventHub:
    def __init__(self) -> None:
        self._subscribers: set[GraphEventSubscription] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> GraphEventSubscription:
        subscription = GraphEventSubscription(self, queue.Queue())
        with self._lock:
            self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: GraphEventSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def emit(self, event_type: str, payload: dict[str, Any]) -> GraphEvent:
        event = GraphEvent(type=event_type, payload=payload)
        with self._lock:
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            try:
                subscription.queue.put_nowait(event)
            except Exception:  # pragma: no cover - queue is best effort
                continue
        return event

    @contextmanager
    def subscription(self) -> Iterator[GraphEventSubscription]:
        subscription = self.subscribe()
        try:
            yield subscription
        finally:
            subscription.close()


class LiveGraphSync(QObject):
    def __init__(
        self,
        refresh_callback: Callable[[], None],
        status_callback: Callable[[str], None] | None = None,
        websocket_url: str | None = None,
        enabled: bool = True,
        reconnect_ms: int = 4000,
        refresh_delay_ms: int = 200,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.refresh_callback = refresh_callback
        self.status_callback = status_callback
        self.websocket_url = websocket_url or self._default_websocket_url()
        self.enabled = enabled and bool(self.websocket_url)
        self.reconnect_ms = reconnect_ms
        self.refresh_delay_ms = refresh_delay_ms
        self._socket: QWebSocket | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_callback)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self.connect)
        self._connected = False

    def _default_websocket_url(self) -> str:
        if os.environ.get("ECHOMAP_DISABLE_LIVE_SYNC", "").strip().lower() in {"1", "true", "yes", "on"}:
            return ""
        explicit = os.environ.get("ECHOMAP_WS_URL")
        if explicit:
            return explicit
        host = os.environ.get("ECHOMAP_API_HOST", "127.0.0.1")
        port = os.environ.get("ECHOMAP_API_PORT", "8000")
        return f"ws://{host}:{port}/ws/graph"

    def set_status(self, text: str) -> None:
        if self.status_callback:
            self.status_callback(text)

    def start(self) -> None:
        if not self.enabled:
            self.set_status("Live sync disabled")
            return
        self.connect()

    def connect(self) -> None:
        if not self.enabled or not self.websocket_url:
            self.set_status("Live sync disabled")
            return
        if self._socket is not None:
            self._socket.deleteLater()
        self._socket = QWebSocket()
        self._socket.connected.connect(self._handle_connected)
        self._socket.disconnected.connect(self._handle_disconnected)
        self._socket.textMessageReceived.connect(self.handle_message)
        error_signal = getattr(self._socket, "errorOccurred", None)
        if error_signal is not None:
            error_signal.connect(self._handle_error)  # type: ignore[arg-type]
        self.set_status(f"Connecting to {self.websocket_url} ...")
        self._socket.open(QUrl(self.websocket_url))

    def stop(self) -> None:
        self._reconnect_timer.stop()
        self._refresh_timer.stop()
        if self._socket is not None:
            self._socket.close()
            self._socket.deleteLater()
            self._socket = None
        self._connected = False

    def _handle_connected(self) -> None:
        self._connected = True
        self.set_status(f"Connected to {self.websocket_url}")

    def _handle_disconnected(self) -> None:
        was_connected = self._connected
        self._connected = False
        if was_connected:
            self.set_status("Live sync disconnected")
        if self.enabled:
            self._reconnect_timer.start(self.reconnect_ms)

    def _handle_error(self, *_args) -> None:
        self.set_status("Live sync connection error")
        if self.enabled:
            self._reconnect_timer.start(self.reconnect_ms)

    def handle_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        event_type = str(payload.get("type", ""))
        if event_type == "snapshot":
            self._schedule_refresh(force=True)
            return
        if event_type.endswith("_upserted") or event_type.endswith("_added") or event_type.endswith("_saved") or event_type.endswith("_updated") or event_type.endswith("_removed") or event_type.endswith("_deleted"):
            self._schedule_refresh()

    def _schedule_refresh(self, force: bool = False) -> None:
        if force or self.refresh_delay_ms <= 0:
            self._refresh_timer.stop()
            self.refresh_callback()
            return
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(self.refresh_delay_ms)
