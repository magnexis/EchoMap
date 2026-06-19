from pathlib import Path

from echomap.db import Database
from echomap.services.scanner import BackgroundScanner


def test_scanner_queue_controls(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    scanner = BackgroundScanner(db, max_depth=2, auto_start=False)
    try:
        scanner.pause()
        scanner.enqueue("example.com")
        snapshot = scanner.snapshot()
        assert snapshot["queued"] == 1
        assert snapshot["paused"] is True
        scanner.clear_queue()
        snapshot = scanner.snapshot()
        assert snapshot["queued"] == 0
    finally:
        scanner.stop()
