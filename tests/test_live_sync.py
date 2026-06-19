import json

from echomap.services.live import LiveGraphSync


def test_live_sync_refreshes_on_graph_event():
    refresh_calls = []
    statuses = []

    sync = LiveGraphSync(
        refresh_callback=lambda: refresh_calls.append("refresh"),
        status_callback=statuses.append,
        websocket_url="ws://localhost:8000/ws/graph",
        enabled=True,
        refresh_delay_ms=0,
    )

    sync.handle_message(json.dumps({"type": "nodes_upserted", "payload": {"count": 2}}))
    sync.handle_message(json.dumps({"type": "snapshot", "payload": {}}))
    sync.handle_message("not-json")

    assert refresh_calls == ["refresh", "refresh"]
    assert sync.websocket_url == "ws://localhost:8000/ws/graph"
