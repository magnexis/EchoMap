from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .db import Database
from .ui.main_window import MainWindow


def app_data_dir() -> Path:
    base = Path.home() / ".echomap"
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("EchoMap")
    backend_kind = os.environ.get("ECHOMAP_BACKEND", "sqlite")
    backend_dsn = os.environ.get("ECHOMAP_BACKEND_DSN")
    db = Database(app_data_dir() / "echomap.sqlite3", backend_kind=backend_kind, backend_dsn=backend_dsn)
    window = MainWindow(db)
    window.show()
    sys.exit(app.exec())
