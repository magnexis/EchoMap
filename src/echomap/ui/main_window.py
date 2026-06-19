from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow,
    QListWidget,
    QStackedWidget,
    QToolBar,
    QWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from ..db import Database
from ..services.scanner import BackgroundScanner
from ..services.live import LiveGraphSync
from .command_palette import CommandPalette, PaletteAction
from .panels import (
    ArchaeologyPanel,
    DashboardPanel,
    DiscoverPanel,
    GraphPanel,
    InvestigationsPanel,
    OverviewPanel,
    OperationsPanel,
    PublicIntelPanel,
    ReportsPanel,
    SettingsPanel,
    TechnologiesPanel,
    TimelinePanel,
)


class MainWindow(QMainWindow):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setWindowTitle("EchoMap")
        self.resize(1520, 960)
        self.scanner = BackgroundScanner(db, max_depth=1)
        self.auto_scan_enabled = True
        self.current_query = ""
        self.current_selected_node = None
        self._shortcuts = []

        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav_items = [
            "Overview",
            "Dashboard",
            "Discover",
            "Operations",
            "Graph",
            "Investigations",
            "Timeline",
            "Archaeology",
            "Public Intel",
            "Technologies",
            "Reports",
            "Settings",
        ]
        self.nav.addItems(self.nav_items)
        self.nav.setFixedWidth(220)
        self.nav.setStyleSheet(
            """
            QListWidget { background: #020617; color: #e5e7eb; border: 0; padding: 12px; }
            QListWidget::item { padding: 12px; margin-bottom: 6px; border-radius: 8px; }
            QListWidget::item:selected { background: #1e293b; color: #f8fafc; }
            """
        )
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.overview_panel = OverviewPanel(db)
        self.dashboard = DashboardPanel(db, self.refresh_all)
        self.discover_panel = DiscoverPanel(db, self.on_discovery_ready, self.refresh_all)
        self.operations_panel = OperationsPanel(db, self.scanner)
        self.graph_panel = GraphPanel(db, self.on_graph_selection_changed)
        self.investigations_panel = InvestigationsPanel(
            db,
            get_selected_node=lambda: self.current_selected_node,
            get_current_query=lambda: self.current_query,
            focus_node_callback=self.focus_node,
        )
        self.timeline_panel = TimelinePanel(db)
        self.archaeology_panel = ArchaeologyPanel(db)
        self.public_intel_panel = PublicIntelPanel(db)
        self.technologies_panel = TechnologiesPanel(db)
        self.reports_panel = ReportsPanel(db, get_graph_widget=lambda: self.graph_panel.view)
        self.settings_panel = SettingsPanel(self.apply_theme, self.set_auto_scan_enabled, self.auto_scan_enabled)
        self.live_sync = LiveGraphSync(
            refresh_callback=self._refresh_from_live_event,
            status_callback=self._set_live_sync_status,
        )

        for panel in [
            self.overview_panel,
            self.dashboard,
            self.discover_panel,
            self.operations_panel,
            self.graph_panel,
            self.investigations_panel,
            self.timeline_panel,
            self.archaeology_panel,
            self.public_intel_panel,
            self.technologies_panel,
            self.reports_panel,
            self.settings_panel,
        ]:
            self.stack.addWidget(panel)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.apply_theme(True)
        self._build_toolbar()
        self._build_shortcuts()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh_all)
        self._refresh_timer.start()
        self.settings_panel.set_backend_info(self.db.backend_info().description)
        self.settings_panel.set_backend_snapshot(self.db.backend_snapshot())
        self.settings_panel.set_live_stream_status("starting...")
        self.settings_panel.set_auto_scan_state(self.auto_scan_enabled)
        self.investigations_panel.set_selected_node(self.current_selected_node)
        self.operations_panel.reload()
        self.live_sync.start()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Global")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addWidget(QLabel("Quick Search:"))
        self.quick_search = QLineEdit()
        self.quick_search.setPlaceholderText("Seed discovery or search the workspace")
        self.quick_search.returnPressed.connect(self._run_quick_search)
        toolbar.addWidget(self.quick_search)
        run_btn = QPushButton("Discover")
        run_btn.clicked.connect(self._run_quick_search)
        toolbar.addWidget(run_btn)

    def _build_shortcuts(self) -> None:
        for sequence, callback in [
            ("Ctrl+K", self.open_command_palette),
            ("Ctrl+1", lambda: self.nav.setCurrentRow(0)),
            ("Ctrl+2", lambda: self.nav.setCurrentRow(2)),
            ("Ctrl+3", lambda: self.nav.setCurrentRow(self.nav_items.index("Operations"))),
            ("Ctrl+4", lambda: self.nav.setCurrentRow(self.nav_items.index("Graph"))),
        ]:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _run_quick_search(self) -> None:
        self.nav.setCurrentRow(self.nav_items.index("Discover"))
        self.discover_panel.query.setText(self.quick_search.text())
        self.discover_panel.start_discovery()

    def on_discovery_ready(self, result=None) -> None:
        if result is not None:
            self.current_query = result.root_query
        if self.auto_scan_enabled and result is not None and getattr(result, "related_targets", None):
            self.scanner.enqueue_many(result.related_targets, depth=0)
        self.refresh_all()

    def on_graph_selection_changed(self, node) -> None:
        self.current_selected_node = node
        self.investigations_panel.set_selected_node(node)

    def focus_node(self, node_id: str) -> None:
        self.refresh_all()
        self.nav.setCurrentRow(self.nav_items.index("Graph"))
        self.graph_panel.select_node_id(node_id)
        self.current_selected_node = self.graph_panel.selected_node
        self.investigations_panel.set_selected_node(self.current_selected_node)

    def open_command_palette(self) -> None:
        actions: list[PaletteAction] = [
            PaletteAction(
                label="Open Overview",
                description="Jump to the ecosystem scorecards and anomaly flags.",
                keywords=("overview", "scorecards", "anomalies"),
                callback=lambda: self.nav.setCurrentRow(self.nav_items.index("Overview")),
            ),
            PaletteAction(
                label="Expand Selected Node",
                description="Grow the neighborhood around the current graph node.",
                keywords=("expand", "node", "graph"),
                callback=self.graph_panel.expand_selected,
            ),
            PaletteAction(
                label="Focus Selected Type",
                description="Filter the graph to the selected node's entity type.",
                keywords=("focus", "type", "filter"),
                callback=self.graph_panel.focus_selected_type,
            ),
            PaletteAction(
                label="Trace Relationship Path",
                description="Find the shortest relationship path between two selected graph entities.",
                keywords=("trace", "path", "relationship", "graph"),
                callback=self.graph_panel.trace_path,
            ),
            PaletteAction(
                label="Save Investigation",
                description="Persist the current graph snapshot and selected context.",
                keywords=("save", "investigation", "snapshot"),
                callback=self.investigations_panel.save_investigation,
            ),
            PaletteAction(
                label="Load Selected Investigation",
                description="Restore the currently selected saved investigation into the graph.",
                keywords=("load", "investigation", "restore"),
                callback=self.investigations_panel.load_investigation,
            ),
            PaletteAction(
                label="Diff Selected Investigation",
                description="Compare the saved investigation graph against the live workspace.",
                keywords=("diff", "compare", "investigation", "graph"),
                callback=self.investigations_panel.compare_selected_investigation_to_current,
            ),
            PaletteAction(
                label="Edit Selected Investigation",
                description="Rename the active investigation and update its notes.",
                keywords=("edit", "investigation", "rename"),
                callback=self.investigations_panel.edit_investigation,
            ),
            PaletteAction(
                label="Export Selected Investigation",
                description="Write the active investigation to a JSON file.",
                keywords=("export", "investigation", "json"),
                callback=self.investigations_panel.export_investigation,
            ),
            PaletteAction(
                label="Bookmark Selected Node",
                description="Keep the current node in the bookmark list.",
                keywords=("bookmark", "save", "node"),
                callback=self.investigations_panel.bookmark_selected,
            ),
            PaletteAction(
                label="Edit Selected Bookmark Note",
                description="Revise the note attached to the selected bookmark.",
                keywords=("bookmark", "note", "edit"),
                callback=self.investigations_panel.edit_bookmark_note,
            ),
            PaletteAction(
                label="Remove Selected Bookmark",
                description="Delete the selected bookmark from the workspace.",
                keywords=("bookmark", "remove", "delete"),
                callback=self.investigations_panel.remove_bookmark,
            ),
            PaletteAction(
                label="Open Public Intel",
                description="Open civic layers, FOIA requests, agendas, citations, and playback.",
                keywords=("public", "intel", "foia", "agenda"),
                callback=lambda: self.nav.setCurrentRow(self.nav_items.index("Public Intel")),
            ),
            PaletteAction(
                label="Open Signature Tools",
                description="Jump to EchoTrail, Agency Radar, and surveillance radius analysis.",
                keywords=("echotrail", "radar", "radius", "signature"),
                callback=lambda: (
                    self.nav.setCurrentRow(self.nav_items.index("Public Intel")),
                    self.public_intel_panel.tabs.setCurrentWidget(self.public_intel_panel.signature_tab),
                ),
            ),
            PaletteAction(
                label="Build EchoTrail",
                description="Trace how a clue was discovered through the workspace.",
                keywords=("echotrail", "trail", "discovered"),
                callback=self.public_intel_panel.build_echo_trail,
            ),
            PaletteAction(
                label="Run Agency Radar",
                description="Search for public clues tied to a vendor, agency, or technology.",
                keywords=("radar", "agency", "vendor", "search"),
                callback=self.public_intel_panel.run_agency_radar,
            ),
            PaletteAction(
                label="Analyze Surveillance Radius",
                description="Inspect nearby schools, roads, neighborhoods, and cameras around a location.",
                keywords=("radius", "surveillance", "camera", "coverage"),
                callback=self.public_intel_panel.analyze_surveillance_radius,
            ),
            PaletteAction(
                label="Export GIS Package",
                description="Write the current surveillance radius analysis to a portable ZIP bundle.",
                keywords=("export", "gis", "package", "radius", "geojson"),
                callback=self.public_intel_panel.export_radius_package,
            ),
            PaletteAction(
                label="Analyze Radius + Export GIS Package",
                description="Run the radius analysis and immediately export the GIS package bundle.",
                keywords=("paired", "radius", "gis", "export", "workflow"),
                callback=self.public_intel_panel.analyze_and_export_radius_package,
            ),
            PaletteAction(
                label="Load Radar Preset",
                description="Load the selected radar preset into the radar search fields.",
                keywords=("load", "preset", "radar"),
                callback=self.public_intel_panel.load_radar_preset,
            ),
            PaletteAction(
                label="Delete Radar Preset",
                description="Delete the selected radar preset from the workspace.",
                keywords=("delete", "preset", "radar", "remove"),
                callback=self.public_intel_panel.delete_radar_preset,
            ),
            PaletteAction(
                label="Generate Report",
                description="Create a quick Markdown report in the local reports folder.",
                keywords=("report", "export", "generate"),
                callback=self.reports_panel.quick_generate_report,
            ),
            PaletteAction(
                label="Export Graph Snapshot",
                description="Capture the current graph canvas as a PNG snapshot.",
                keywords=("snapshot", "png", "graph", "export"),
                callback=lambda: self.reports_panel.export("png"),
            ),
            PaletteAction(
                label="Open Investigations",
                description="Switch to saved investigations, bookmarks, and comparisons.",
                keywords=("investigations", "bookmarks", "compare"),
                callback=lambda: self.nav.setCurrentRow(self.nav_items.index("Investigations")),
            ),
            PaletteAction(
                label="Open Operations Console",
                description="View and control the background scan queue.",
                keywords=("queue", "scanner", "operations"),
                callback=lambda: self.nav.setCurrentRow(self.nav_items.index("Operations")),
            ),
            PaletteAction(
                label="Pause Scanner",
                description="Temporarily stop background scans.",
                keywords=("pause", "scanner", "queue"),
                callback=self.scanner.pause,
            ),
            PaletteAction(
                label="Resume Scanner",
                description="Resume background scans.",
                keywords=("resume", "scanner", "queue"),
                callback=self.scanner.resume,
            ),
            PaletteAction(
                label="Clear Scan Queue",
                description="Drop queued background scan jobs.",
                keywords=("clear", "queue", "scanner"),
                callback=self.scanner.clear_queue,
            ),
        ]
        dialog = CommandPalette(actions, self)
        dialog.exec()

    def refresh_all(self) -> None:
        self.overview_panel.reload()
        self.dashboard.reload()
        self.operations_panel.reload()
        self.graph_panel.reload()
        self.investigations_panel.reload()
        self.timeline_panel.reload()
        self.archaeology_panel.reload()
        self.public_intel_panel.reload()
        self.technologies_panel.reload()
        self.settings_panel.set_backend_info(self.db.backend_info().description)
        self.settings_panel.set_backend_snapshot(self.db.backend_snapshot())
        self.settings_panel.set_auto_scan_state(self.auto_scan_enabled)

    def _refresh_from_live_event(self) -> None:
        self.refresh_all()

    def _set_live_sync_status(self, text: str) -> None:
        self.settings_panel.set_live_stream_status(text)

    def set_auto_scan_enabled(self, enabled: bool) -> None:
        self.auto_scan_enabled = enabled
        self.settings_panel.set_auto_scan_state(enabled)

    def apply_theme(self, dark: bool) -> None:
        if dark:
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #0f172a; color: #e5e7eb; }
                QPushButton { background: #2563eb; color: white; padding: 8px 12px; border-radius: 8px; border: none; }
                QPushButton:hover { background: #1d4ed8; }
                QLineEdit, QTextEdit, QListWidget, QComboBox { background: #111827; color: #e5e7eb; border: 1px solid #334155; border-radius: 8px; padding: 8px; }
                QLabel { color: #e5e7eb; }
                """
            )
        else:
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #f8fafc; color: #0f172a; }
                QPushButton { background: #0f766e; color: white; padding: 8px 12px; border-radius: 8px; border: none; }
                QPushButton:hover { background: #115e59; }
                QLineEdit, QTextEdit, QListWidget, QComboBox { background: white; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 8px; padding: 8px; }
                QLabel { color: #0f172a; }
                """
            )

    def closeEvent(self, event) -> None:  # pragma: no cover - UI lifecycle
        self.scanner.stop()
        self.live_sync.stop()
        super().closeEvent(event)
