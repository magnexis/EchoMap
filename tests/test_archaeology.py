from echomap.services.archaeology import build_archaeology_bundle


def test_archaeology_bundle_handles_unreachable_sources(monkeypatch):
    import echomap.services.archaeology as archaeology

    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(archaeology.requests, "get", fail)
    bundle = build_archaeology_bundle("example.com", "https://example.com")
    assert bundle.snapshots == []
    assert bundle.dns_records == []
    assert bundle.certificates == []

