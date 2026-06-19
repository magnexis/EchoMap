from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


@dataclass(slots=True)
class PaletteAction:
    label: str
    description: str
    keywords: tuple[str, ...]
    callback: Callable[[], None]


class CommandPalette(QDialog):
    def __init__(self, actions: list[PaletteAction], parent=None) -> None:
        super().__init__(parent)
        self.actions = actions
        self.filtered_actions = list(actions)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(760, 520)
        self.setWindowFlag(Qt.WindowType.Tool, True)

        layout = QVBoxLayout(self)
        title = QLabel("Command Palette")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type an action like expand, focus, report, bookmark, compare...")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._trigger_item)
        self.list.itemDoubleClicked.connect(self._trigger_item)
        help_row = QLabel("Enter to run, Esc to close")
        help_row.setStyleSheet("color: #94a3b8;")
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(help_row)
        self._filter("")

    def _filter(self, text: str) -> None:
        query = text.strip().lower()
        self.list.clear()
        self.filtered_actions = [
            action
            for action in self.actions
            if not query
            or query in action.label.lower()
            or query in action.description.lower()
            or any(query in keyword.lower() for keyword in action.keywords)
        ]
        for action in self.filtered_actions:
            item = QListWidgetItem(f"{action.label} - {action.description}")
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _trigger_item(self, item) -> None:
        index = self.list.row(item)
        if index < 0 or index >= len(self.filtered_actions):
            return
        action = self.filtered_actions[index]
        self.accept()
        action.callback()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - Qt UI callback
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.list.currentRow() >= 0:
            item = self.list.currentItem()
            if item is not None:
                self._trigger_item(item)
                return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
