from echomap.services.live import GraphEventHub


def test_graph_event_hub_broadcasts():
    hub = GraphEventHub()
    subscription = hub.subscribe()
    try:
        emitted = hub.emit("graph_changed", {"count": 3, "kind": "discovery"})
        received = subscription.get(timeout=0.1)
        assert received.type == emitted.type
        assert received.payload == emitted.payload
        assert received.created_at == emitted.created_at
    finally:
        subscription.close()
