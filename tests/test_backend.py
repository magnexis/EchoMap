from pathlib import Path

from echomap.db import Database


def test_backend_info_defaults_to_sqlite(tmp_path: Path):
    db = Database(tmp_path / "echomap.sqlite3")
    info = db.backend_info()
    assert info.kind == "sqlite"
    assert "SQLite" in info.description

