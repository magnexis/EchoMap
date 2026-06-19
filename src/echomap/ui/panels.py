from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from dataclasses import asdict

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSlider,
    QTextEdit,
    QPlainTextEdit,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QGraphicsView,
)

from ..db import Database
from ..services.comparison import compare_graphs, compare_nodes
from ..services.insights import analyze_graph
from ..services.discovery import discover
from ..services.public_intelligence import (
    AgencyProfile,
    AgencyRadarResult,
    ChangeDetectionResult,
    EchoTrailResult,
    GeocodeResult,
    PublicIntelLayer,
    PublicRecordRequest,
    SurveillanceRadiusResult,
    SourceCitation,
    AgendaScanResult,
    SpreadsheetImportResult,
    build_agency_profile,
    build_echotrail,
    agency_radar,
    compare_public_snapshots,
    confidence_from_source,
    confidence_label,
    detect_duplicate_points,
    export_public_map_bundle,
    export_public_map_csv,
    export_public_map_geojson,
    export_public_map_html,
    export_public_radius_package,
    geocode_value,
    import_tabular_data,
    build_playback_frames,
    _read_text_file,
    ingest_document_file,
    ingest_document_text,
    scan_agenda_text,
    snapshot_public_source,
    surveillance_radius,
    summarize_heatmap,
)
from ..services.reporting import export_csv, export_html, export_json, export_markdown
from ..services.relationship import build_relationship_chains, trace_relationship_path
from .graph_view import GraphNode, GraphView


class DiscoveryWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query = query

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(discover(self.query))
        except Exception as exc:  # pragma: no cover - UI worker
            self.failed.emit(f"{exc.__class__.__name__}: {exc}")


class DashboardPanel(QWidget):
    def __init__(self, db: Database, on_refresh) -> None:
        super().__init__()
        self.db = db
        self.on_refresh = on_refresh
        layout = QVBoxLayout(self)
        title = QLabel("EchoMap Dashboard")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #e5e7eb;")
        subtitle = QLabel("Discover how the internet connects.")
        subtitle.setStyleSheet("color: #94a3b8;")
        self.tech_profile = QLabel()
        self.tech_profile.setWordWrap(True)
        self.tech_profile.setStyleSheet("color: #cbd5e1; padding: 6px 0 10px 0;")
        self.stats = QLabel()
        self.stats.setStyleSheet("font-size: 14px; color: #cbd5e1;")
        self.backend = QLabel()
        self.backend.setStyleSheet("font-size: 13px; color: #cbd5e1;")
        self.insight_lines = QListWidget()
        self.insight_lines.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.recent = QListWidget()
        self.recent.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.tech_profile)
        layout.addWidget(self.stats)
        layout.addWidget(self.backend)
        layout.addWidget(QLabel("Ecosystem Pulse"))
        layout.addWidget(self.insight_lines, 1)
        layout.addWidget(QLabel("Recent discoveries"))
        layout.addWidget(self.recent, 1)
        self.reload()

    def reload(self) -> None:
        stats = self.db.stats()
        self.stats.setText(
            f"Nodes: {stats['nodes']}   Edges: {stats['edges']}   Discoveries: {stats['discoveries']}   Artifacts: {stats['artifacts']}"
        )
        backend = self.db.backend_info()
        self.backend.setText(f"Backend: {backend.kind} ({backend.status}) - {backend.description}")
        graph = self.db.export_graph()
        insights = analyze_graph(graph["nodes"], graph["edges"])
        latest_artifact = next(iter(self.db.recent_artifacts(limit=1, kind="discovery")), None)
        if latest_artifact:
            profile = latest_artifact["payload"].get("tech_profile", {})
            dna = profile.get("dna", {})
            summary = profile.get("summary") or "No stack DNA available."
            confidence = profile.get("confidence_score")
            confidence_text = f"{confidence:.1f}" if isinstance(confidence, (int, float)) else "n/a"
            dna_parts = ", ".join(f"{key}={value}" for key, value in dna.get("categories", {}).items()) if dna.get("categories") else "no categories"
            self.tech_profile.setText(f"Latest tech DNA: {summary} | confidence {confidence_text} | {dna_parts}")
        else:
            self.tech_profile.setText("Latest tech DNA: no discoveries yet.")
        self.insight_lines.clear()
        for line in insights.summaries:
            self.insight_lines.addItem(line)
        for hub in insights.top_hubs[:5]:
            self.insight_lines.addItem(f"Hub: {hub['label']} ({hub['kind']}) - {hub['degree']} links")
        self.recent.clear()
        for row in self.db.recent_discoveries():
            self.recent.addItem(QListWidgetItem(f"{row['query']} - {row['summary']}"))


class DiscoverPanel(QWidget):
    def __init__(self, db: Database, on_discovery_ready, on_refresh) -> None:
        super().__init__()
        self.db = db
        self.on_discovery_ready = on_discovery_ready
        self.on_refresh = on_refresh
        self.thread: QThread | None = None
        self.worker: DiscoveryWorker | None = None

        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Enter a domain, URL, company, GitHub repo, or keyword")
        self.discover_button = QPushButton("Discover")
        self.discover_button.clicked.connect(self.start_discovery)
        form.addWidget(self.query, 1)
        form.addWidget(self.discover_button)
        layout.addLayout(form)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(self.summary, 2)

        self.technologies = QListWidget()
        self.technologies.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(QLabel("Technology Fingerprint"))
        layout.addWidget(self.technologies, 1)

    def start_discovery(self) -> None:
        query = self.query.text().strip()
        if not query:
            return
        self.discover_button.setEnabled(False)
        self.summary.setPlainText("Running discovery...")
        self.thread = QThread(self)
        self.worker = DiscoveryWorker(query)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._handle_result)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _handle_result(self, result) -> None:
        self.db.upsert_nodes(result.nodes)
        self.db.upsert_edges(result.edges)
        self.db.add_discovery(result.root_query, result.summary)
        self.db.add_artifact(
            None,
            "discovery",
            {
                "query": result.root_query,
                "summary": result.summary,
                "timeline": result.timeline,
                "technologies": result.technologies,
                "tech_profile": result.tech_profile,
                "archaeology": result.archaeology,
                "related_targets": result.related_targets,
            },
        )
        for snapshot in result.archaeology:
            self.db.add_archaeology_snapshot(result.root_query, snapshot.get("type", "archaeology"), snapshot)
        self.summary.setPlainText(
            result.summary
            + "\n\nTimeline:\n"
            + json.dumps(result.timeline, indent=2)
            + "\n\nTech Profile:\n"
            + json.dumps(result.tech_profile, indent=2)
        )
        self.technologies.clear()
        if result.tech_profile:
            self.technologies.addItem(
                f"Confidence Score: {result.tech_profile.get('confidence_score', 0):.1f}"
            )
            for category, entries in result.tech_profile.get("categories", {}).items():
                if not entries:
                    continue
                names = ", ".join(entry["technology"] for entry in entries[:4])
                self.technologies.addItem(f"{category.title()}: {names}")
        for tech in result.technologies:
            tech_name = tech.get("technology") or "Unknown"
            self.technologies.addItem(f"{tech_name} ({tech['confidence']:.2f}) - {tech['evidence']}")
        self.discover_button.setEnabled(True)
        self.on_discovery_ready(result)
        self.on_refresh()

    def _handle_error(self, message: str) -> None:
        self.summary.setPlainText(message)
        self.discover_button.setEnabled(True)


class OperationsPanel(QWidget):
    def __init__(self, db: Database, scanner) -> None:
        super().__init__()
        self.db = db
        self.scanner = scanner
        layout = QVBoxLayout(self)
        header = QLabel("Operations Console")
        header.setStyleSheet("font-size: 24px; font-weight: 700; color: #e5e7eb;")
        layout.addWidget(header)

        controls = QGridLayout()
        self.depth_label = QLabel()
        self.status_label = QLabel()
        self.queue_label = QLabel()
        self.pause_button = QPushButton("Pause Scanner")
        self.pause_button.setCheckable(True)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.depth_control = QComboBox()
        self.depth_control.addItems(["0", "1", "2", "3"])
        self.depth_control.currentIndexChanged.connect(self.update_depth)
        controls.addWidget(QLabel("Max Depth"), 0, 0)
        controls.addWidget(self.depth_control, 0, 1)
        controls.addWidget(self.pause_button, 0, 2)
        controls.addWidget(self.queue_label, 1, 0, 1, 3)
        controls.addWidget(self.status_label, 2, 0, 1, 3)
        controls.addWidget(self.depth_label, 3, 0, 1, 3)
        layout.addLayout(controls)

        layout.addWidget(QLabel("Scan Queue"))
        self.queue = QListWidget()
        self.queue.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(self.queue, 1)

        bottom = QHBoxLayout()
        self.seed_input = QLineEdit()
        self.seed_input.setPlaceholderText("Seed a new scan query")
        self.enqueue_button = QPushButton("Enqueue")
        self.enqueue_button.clicked.connect(self.enqueue_query)
        self.clear_button = QPushButton("Clear Queue")
        self.clear_button.clicked.connect(self.clear_queue)
        bottom.addWidget(self.seed_input, 1)
        bottom.addWidget(self.enqueue_button)
        bottom.addWidget(self.clear_button)
        layout.addLayout(bottom)
        self.reload()

    def reload(self) -> None:
        snapshot = self.scanner.snapshot()
        self.queue_label.setText(f"Queued: {snapshot['queued']}   Seen: {snapshot['seen']}   Processed: {snapshot['processed']}   Errors: {snapshot['errors']}")
        self.status_label.setText(f"Paused: {'yes' if snapshot['paused'] else 'no'}   Last job: {snapshot['last_job'] or 'none'}")
        self.depth_label.setText(f"Max depth: {snapshot['max_depth']}   Last error: {snapshot['last_error'] or 'none'}")
        self.pause_button.setChecked(snapshot["paused"])
        self.depth_control.setCurrentText(str(snapshot["max_depth"]))
        self.queue.clear()
        for job in snapshot["queue"][:50]:
            self.queue.addItem(f"depth={job['depth']} | {job['query']}")

    def toggle_pause(self, checked: bool) -> None:
        if checked:
            self.scanner.pause()
            self.pause_button.setText("Resume Scanner")
        else:
            self.scanner.resume()
            self.pause_button.setText("Pause Scanner")

    def update_depth(self, *_args) -> None:
        try:
            self.scanner.set_max_depth(int(self.depth_control.currentText()))
        except ValueError:
            pass

    def enqueue_query(self) -> None:
        query = self.seed_input.text().strip()
        if not query:
            return
        self.scanner.enqueue(query)
        self.seed_input.clear()
        self.reload()

    def clear_queue(self) -> None:
        self.scanner.clear_queue()
        self.reload()


class GraphPanel(QWidget):
    def __init__(self, db: Database, on_selection_changed=None) -> None:
        super().__init__()
        self.db = db
        self.on_selection_changed = on_selection_changed
        self.thread: QThread | None = None
        self.worker: DiscoveryWorker | None = None
        self.selected_node: GraphNode | None = None
        self.selected_edge_id: str | None = None
        self._current_edge_lookup: dict[str, dict] = {}

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Graph")
        self.refresh_button.clicked.connect(self.reload)
        self.expand_button = QPushButton("Expand Selected")
        self.expand_button.clicked.connect(self.expand_selected)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter graph by label or metadata")
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All", "Website", "Repository", "Technology", "Person", "Domain", "Company", "Organization"])
        self.kind_filter.currentIndexChanged.connect(self.reload)
        self.search.returnPressed.connect(self.reload)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.expand_button)
        controls.addWidget(QLabel("Kind"))
        controls.addWidget(self.kind_filter)
        controls.addWidget(self.search, 1)
        layout.addLayout(controls)

        path_controls = QHBoxLayout()
        self.path_start = QComboBox()
        self.path_end = QComboBox()
        self.trace_button = QPushButton("Trace Path")
        self.trace_button.clicked.connect(self.trace_path)
        path_controls.addWidget(QLabel("From"))
        path_controls.addWidget(self.path_start, 1)
        path_controls.addWidget(QLabel("To"))
        path_controls.addWidget(self.path_end, 1)
        path_controls.addWidget(self.trace_button)
        layout.addLayout(path_controls)

        splitter = QSplitter()
        self.view = GraphView()
        self.view.nodeClicked.connect(self._node_clicked)
        self.view.nodeActivated.connect(self._node_activated)
        splitter.addWidget(self.view)

        right_wrap = QWidget()
        right_layout = QVBoxLayout(right_wrap)
        self.minimap = QGraphicsView(self.view.scene)
        self.minimap.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.minimap.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.minimap.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.minimap.setInteractive(False)
        self.minimap.setFixedHeight(180)
        self.minimap.setStyleSheet("background: #020617; border: 1px solid #334155; border-radius: 10px;")
        right_layout.addWidget(QLabel("Navigation Minimap"))
        right_layout.addWidget(self.minimap)

        self.detail_tabs = QTabWidget()

        node_tab = QWidget()
        node_layout = QVBoxLayout(node_tab)
        self.node_summary = QTextEdit()
        self.node_summary.setReadOnly(True)
        self.node_summary.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        node_layout.addWidget(QLabel("Selected Node"))
        node_layout.addWidget(self.node_summary, 1)
        self.insights = QListWidget()
        self.insights.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        node_layout.addWidget(QLabel("Neighborhood Intelligence"))
        node_layout.addWidget(self.insights, 1)
        self.related = QListWidget()
        self.related.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        node_layout.addWidget(QLabel("Related Targets"))
        node_layout.addWidget(self.related, 1)

        edge_tab = QWidget()
        edge_layout = QVBoxLayout(edge_tab)
        self.edge_list = QListWidget()
        self.edge_list.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.edge_list.itemClicked.connect(self._edge_clicked)
        edge_layout.addWidget(QLabel("Incident Edges"))
        edge_layout.addWidget(self.edge_list, 1)
        self.edge_details = QTextEdit()
        self.edge_details.setReadOnly(True)
        self.edge_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        edge_layout.addWidget(QLabel("Edge Details"))
        edge_layout.addWidget(self.edge_details, 1)

        anomaly_tab = QWidget()
        anomaly_layout = QVBoxLayout(anomaly_tab)
        self.anomaly_flags = QListWidget()
        self.anomaly_flags.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        anomaly_layout.addWidget(QLabel("Anomaly Flags"))
        anomaly_layout.addWidget(self.anomaly_flags, 1)
        self.anomaly_explanations = QListWidget()
        self.anomaly_explanations.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        anomaly_layout.addWidget(QLabel("Why It Matters"))
        anomaly_layout.addWidget(self.anomaly_explanations, 1)

        self.detail_tabs.addTab(node_tab, "Node")
        self.detail_tabs.addTab(edge_tab, "Edge")
        chains_tab = QWidget()
        chains_layout = QVBoxLayout(chains_tab)
        chain_controls = QHBoxLayout()
        self.chain_depth = QComboBox()
        self.chain_depth.addItems(["1", "2", "3"])
        self.chain_refresh = QPushButton("Refresh Chains")
        self.chain_refresh.clicked.connect(self.refresh_chains)
        chain_controls.addWidget(QLabel("Depth"))
        chain_controls.addWidget(self.chain_depth)
        chain_controls.addWidget(self.chain_refresh)
        chain_controls.addStretch(1)
        chains_layout.addLayout(chain_controls)
        self.chain_summary = QLabel("Select a node to explore relationship chains.")
        self.chain_summary.setWordWrap(True)
        chains_layout.addWidget(self.chain_summary)
        self.chain_tree = QTreeWidget()
        self.chain_tree.setHeaderLabels(["Expandable Relationship Chains", "Type", "Depth", "Hops"])
        self.chain_tree.itemDoubleClicked.connect(self._chain_item_activated)
        chains_layout.addWidget(self.chain_tree, 1)

        node_ann_controls = QHBoxLayout()
        self.add_node_annotation_button = QPushButton("Add Node Annotation")
        self.add_node_annotation_button.clicked.connect(self.add_node_annotation)
        self.delete_node_annotation_button = QPushButton("Delete Node Annotation")
        self.delete_node_annotation_button.clicked.connect(self.delete_node_annotation)
        node_ann_controls.addWidget(self.add_node_annotation_button)
        node_ann_controls.addWidget(self.delete_node_annotation_button)
        node_ann_controls.addStretch(1)
        node_layout.addLayout(node_ann_controls)
        self.node_annotations = QListWidget()
        self.node_annotations.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        node_layout.addWidget(QLabel("Saved Node Annotations"))
        node_layout.addWidget(self.node_annotations, 1)

        edge_ann_controls = QHBoxLayout()
        self.add_edge_annotation_button = QPushButton("Add Edge Annotation")
        self.add_edge_annotation_button.clicked.connect(self.add_edge_annotation)
        self.delete_edge_annotation_button = QPushButton("Delete Edge Annotation")
        self.delete_edge_annotation_button.clicked.connect(self.delete_edge_annotation)
        edge_ann_controls.addWidget(self.add_edge_annotation_button)
        edge_ann_controls.addWidget(self.delete_edge_annotation_button)
        edge_ann_controls.addStretch(1)
        edge_layout.addLayout(edge_ann_controls)
        self.edge_annotations = QListWidget()
        self.edge_annotations.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        edge_layout.addWidget(QLabel("Saved Edge Annotations"))
        edge_layout.addWidget(self.edge_annotations, 1)

        self.detail_tabs.addTab(chains_tab, "Chains")
        self.detail_tabs.addTab(anomaly_tab, "Anomalies")
        right_layout.addWidget(self.detail_tabs, 1)

        self.path_summary = QLabel("Path trace: select two nodes and trace the shortest relationship path.")
        self.path_summary.setWordWrap(True)
        right_layout.addWidget(self.path_summary)
        self.path_steps = QListWidget()
        self.path_steps.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        right_layout.addWidget(QLabel("Trace Steps"))
        right_layout.addWidget(self.path_steps, 1)
        splitter.addWidget(right_wrap)
        splitter.setSizes([1100, 500])
        layout.addWidget(splitter, 1)
        self.reload()

    def _node_clicked(self, node: GraphNode) -> None:
        self.selected_node = node
        self._show_node(node)
        self.view.select_node(node.id)
        if self.on_selection_changed:
            self.on_selection_changed(node)

    def _node_activated(self, node: GraphNode) -> None:
        self.selected_node = node
        self._show_node(node)
        self.view.select_node(node.id)
        if self.on_selection_changed:
            self.on_selection_changed(node)
        self._start_expansion(node)

    def _show_node(self, node: GraphNode) -> None:
        self.node_summary.setPlainText(json.dumps({"id": node.id, "label": node.label, "kind": node.kind, "metadata": node.metadata}, indent=2))
        self.selected_edge_id = None
        self.related.clear()
        for value in self._derive_related_targets(node):
            self.related.addItem(value)
        self.insights.clear()
        self.edge_list.clear()
        self.edge_details.clear()
        self._current_edge_lookup = {}
        self.view.select_edge(None)
        node_data = self.db.get_node(node.id)
        if not node_data:
            self.refresh_node_annotations()
            self.refresh_edge_annotations()
            self.refresh_chains()
            return
        neighbors = self.db.neighbors(node.id)
        degree = len(neighbors)
        self.insights.addItem(f"Degree: {degree}")
        self.insights.addItem(f"Type: {node_data['kind']}")
        self.insights.addItem(f"Label: {node_data['label']}")
        for relation in neighbors[:10]:
            other_label = relation["target_label"] if relation["source"] == node.id else relation["source_label"]
            other_kind = relation["target_kind"] if relation["source"] == node.id else relation["source_kind"]
            self.insights.addItem(f"{relation['relation']} -> {other_label} ({other_kind})")
            edge_id = relation["edge_id"]
            self._current_edge_lookup[edge_id] = relation
            label = f"{relation['relation']} | {other_label} | confidence={relation['confidence']:.2f}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, edge_id)
            self.edge_list.addItem(item)
        if neighbors:
            self.edge_list.setCurrentRow(0)
            self._show_edge(neighbors[0])
        self.refresh_node_annotations()
        self.refresh_chains()

    def _show_edge(self, relation: dict) -> None:
        self.edge_details.setPlainText(
            json.dumps(
                {
                    "edge_id": relation["edge_id"],
                    "source": {
                        "id": relation["source"],
                        "label": relation["source_label"],
                        "kind": relation["source_kind"],
                    },
                    "target": {
                        "id": relation["target"],
                        "label": relation["target_label"],
                        "kind": relation["target_kind"],
                    },
                    "relation": relation["relation"],
                    "confidence": relation["confidence"],
                    "metadata": relation["metadata"],
                    "created_at": relation["created_at"],
                },
                indent=2,
            )
        )
        self.view.select_edge(relation["edge_id"])
        self.selected_edge_id = relation["edge_id"]
        self.refresh_edge_annotations()

    def _edge_clicked(self, item: QListWidgetItem) -> None:
        edge_id = item.data(Qt.ItemDataRole.UserRole)
        if not edge_id:
            return
        relation = self._current_edge_lookup.get(str(edge_id))
        if relation:
            self._show_edge(relation)

    def _selected_edge_record(self) -> dict | None:
        if not self.selected_edge_id:
            return None
        return self._current_edge_lookup.get(self.selected_edge_id)

    def refresh_node_annotations(self) -> None:
        self.node_annotations.clear()
        if not self.selected_node:
            return
        rows = self.db.list_annotations(target_type="node", target_id=self.selected_node.id, limit=100)
        for row in rows:
            item = QListWidgetItem(f"{row['title']} | {row['body']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.node_annotations.addItem(item)
        if not rows:
            self.node_annotations.addItem("No node annotations yet.")

    def refresh_edge_annotations(self) -> None:
        self.edge_annotations.clear()
        edge = self._selected_edge_record()
        if not edge:
            self.edge_annotations.addItem("Select an edge to view annotations.")
            return
        rows = self.db.list_annotations(target_type="edge", target_id=edge["edge_id"], limit=100)
        for row in rows:
            item = QListWidgetItem(f"{row['title']} | {row['body']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.edge_annotations.addItem(item)
        if not rows:
            self.edge_annotations.addItem("No edge annotations yet.")

    def refresh_chains(self) -> None:
        self.chain_tree.clear()
        if not self.selected_node:
            self.chain_summary.setText("Select a node to explore relationship chains.")
            return
        try:
            max_depth = int(self.chain_depth.currentText())
        except ValueError:
            max_depth = 3
        chains = build_relationship_chains(self.db.list_nodes(limit=5000), self.db.list_edges(limit=10000), self.selected_node.id, limit=8, max_depth=max_depth)
        self.chain_summary.setText(
            f"{len(chains)} expandable chain(s) from {self.selected_node.label}. Depth controls the visible hop radius; each chain shows its hop count."
        )
        if not chains:
            item = QTreeWidgetItem(["No relationship chains found for the current node.", "", "", ""])
            self.chain_tree.addTopLevelItem(item)
            return
        for chain in chains:
            root = QTreeWidgetItem([
                f"{chain.summary}",
                chain.target_kind,
                str(chain.hop_count),
                str(chain.hop_count),
            ])
            root.setData(0, Qt.ItemDataRole.UserRole, {
                "target_id": chain.target_id,
                "node_ids": chain.node_ids,
                "edge_ids": chain.edge_ids,
                "hop_count": chain.hop_count,
                "depth": chain.hop_count,
                "summary": chain.summary,
            })
            for step_index, step in enumerate(chain.steps, start=1):
                left = step.get("from", "?")
                right = step.get("to", "?")
                child = QTreeWidgetItem([
                    f"{step_index}. {left} --{step.get('relation', 'rel')}--> {right}",
                    str(step.get("relation", "")),
                    str(step_index),
                    str(step_index),
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, {
                    "target_id": right,
                    "node_ids": chain.node_ids,
                    "edge_ids": chain.edge_ids,
                    "hop_count": step_index,
                    "depth": step_index,
                    "step": step,
                })
                root.addChild(child)
            self.chain_tree.addTopLevelItem(root)
            root.setExpanded(True)

    def _chain_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        target_id = data.get("target_id")
        if target_id:
            self.select_node_id(str(target_id))
        node_ids = data.get("node_ids", [])
        edge_ids = data.get("edge_ids", [])
        if node_ids and edge_ids:
            self.view.set_path_highlight(node_ids, edge_ids)

    def add_node_annotation(self) -> None:
        if not self.selected_node:
            return
        title, ok = QInputDialog.getText(self, "Add Node Annotation", "Annotation title:", text=self.selected_node.label)
        if not ok or not title.strip():
            return
        body, ok = QInputDialog.getMultiLineText(self, "Add Node Annotation", "Annotation details:", text="")
        if not ok:
            return
        self.db.save_annotation(
            "node",
            self.selected_node.id,
            title.strip(),
            body.strip(),
            {"label": self.selected_node.label, "kind": self.selected_node.kind},
        )
        self.refresh_node_annotations()

    def delete_node_annotation(self) -> None:
        item = self.node_annotations.currentItem()
        if not item:
            return
        annotation_id = item.data(Qt.ItemDataRole.UserRole)
        if annotation_id is None:
            return
        self.db.delete_annotation(int(annotation_id))
        self.refresh_node_annotations()

    def add_edge_annotation(self) -> None:
        edge = self._selected_edge_record()
        if not edge:
            return
        title, ok = QInputDialog.getText(self, "Add Edge Annotation", "Annotation title:", text=edge["relation"])
        if not ok or not title.strip():
            return
        body, ok = QInputDialog.getMultiLineText(self, "Add Edge Annotation", "Annotation details:", text="")
        if not ok:
            return
        self.db.save_annotation(
            "edge",
            edge["edge_id"],
            title.strip(),
            body.strip(),
            {
                "relation": edge["relation"],
                "source": edge["source"],
                "target": edge["target"],
            },
        )
        self.refresh_edge_annotations()

    def delete_edge_annotation(self) -> None:
        item = self.edge_annotations.currentItem()
        if not item:
            return
        annotation_id = item.data(Qt.ItemDataRole.UserRole)
        if annotation_id is None:
            return
        self.db.delete_annotation(int(annotation_id))
        self.refresh_edge_annotations()

    def _derive_related_targets(self, node: GraphNode) -> list[str]:
        targets: list[str] = []
        metadata = node.metadata or {}
        for key in ("url", "homepage", "source"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                targets.append(value)
        if node.kind in {"Website", "Domain"}:
            targets.append(node.label)
        elif node.kind == "Repository":
            targets.append(node.label)
            if "/" in node.label:
                targets.append(f"https://github.com/{node.label}")
        elif node.kind in {"Company", "Organization", "Person", "Technology"}:
            targets.append(node.label)
        return list(dict.fromkeys(targets))

    def _start_expansion(self, node: GraphNode) -> None:
        target = next(iter(self._derive_related_targets(node)), node.label)
        self._launch_discovery(target)

    def expand_selected(self) -> None:
        if self.selected_node:
            self._start_expansion(self.selected_node)

    def focus_selected_type(self) -> None:
        if not self.selected_node:
            return
        self.focus_kind(self.selected_node.kind)

    def focus_kind(self, kind: str) -> None:
        index = self.kind_filter.findText(kind)
        if index >= 0:
            self.kind_filter.setCurrentIndex(index)

    def select_node_id(self, node_id: str | None) -> None:
        if not node_id:
            return
        node = self.db.get_node(node_id)
        if not node:
            return
        selected = GraphNode(node["id"], node["label"], node["kind"], node["metadata"])
        self.selected_node = selected
        self._show_node(selected)
        self.view.select_node(node_id)

    def _launch_discovery(self, query: str) -> None:
        self.thread = QThread(self)
        self.worker = DiscoveryWorker(query)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._handle_expansion_result)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _handle_expansion_result(self, result) -> None:
        self.db.upsert_nodes(result.nodes)
        self.db.upsert_edges(result.edges)
        self.db.add_discovery(result.root_query, f"Expanded graph node: {result.summary}")
        for snapshot in result.archaeology:
            self.db.add_archaeology_snapshot(result.root_query, snapshot.get("type", "archaeology"), snapshot)
        self.reload()

    def _handle_error(self, message: str) -> None:
        QMessageBox.warning(self, "Expansion failed", message)

    def reload(self) -> None:
        nodes = self.db.list_nodes()
        edges = self.db.list_edges()
        selected_kind = self.kind_filter.currentText()
        search_term = self.search.text().strip().lower()
        if selected_kind != "All":
            nodes = [n for n in nodes if n["kind"] == selected_kind]
        if search_term:
            nodes = [
                n
                for n in nodes
                if search_term in n["label"].lower()
                or search_term in n["kind"].lower()
                or search_term in json.dumps(n.get("metadata", {})).lower()
        ]
        node_ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
        self.view.set_graph(nodes, edges)
        self._refresh_anomalies(nodes, edges)
        if self.selected_node and self.selected_node.id in node_ids:
            self._show_node(self.selected_node)
            self.view.select_node(self.selected_node.id)
        previous_start = self.path_start.currentData()
        previous_end = self.path_end.currentData()
        self.path_start.blockSignals(True)
        self.path_end.blockSignals(True)
        self.path_start.clear()
        self.path_end.clear()
        for node in nodes:
            label = f"{node['label']} [{node['kind']}]"
            self.path_start.addItem(label, node["id"])
            self.path_end.addItem(label, node["id"])
        if previous_start is not None and self.path_start.findData(previous_start) >= 0:
            self.path_start.setCurrentIndex(self.path_start.findData(previous_start))
        elif self.selected_node:
            index = self.path_start.findData(self.selected_node.id)
            if index >= 0:
                self.path_start.setCurrentIndex(index)
        if previous_end is not None and self.path_end.findData(previous_end) >= 0:
            self.path_end.setCurrentIndex(self.path_end.findData(previous_end))
        elif len(nodes) > 1:
            fallback_index = 1 if self.selected_node and self.path_end.findData(self.selected_node.id) == 0 else 0
            if fallback_index < self.path_end.count():
                self.path_end.setCurrentIndex(fallback_index)
        self.path_start.blockSignals(False)
        self.path_end.blockSignals(False)
        self._refresh_minimap()

    def _refresh_anomalies(self, nodes: list[dict], edges: list[dict]) -> None:
        insights = analyze_graph(nodes, edges)
        self.anomaly_flags.clear()
        for summary in insights.summaries:
            self.anomaly_flags.addItem(summary)
        if not self.anomaly_flags.count():
            self.anomaly_flags.addItem("No obvious anomaly flags in the current slice.")
        self.anomaly_explanations.clear()
        for explanation in insights.anomaly_explanations:
            self.anomaly_explanations.addItem(explanation)
        if not self.anomaly_explanations.count():
            self.anomaly_explanations.addItem("The current graph slice looks structurally healthy.")

    def _refresh_minimap(self) -> None:
        if self.view.scene.items():
            self.minimap.resetTransform()
            self.minimap.fitInView(self.view.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def trace_path(self) -> None:
        start_id = self.path_start.currentData()
        end_id = self.path_end.currentData()
        if not start_id or not end_id or start_id == end_id:
            self.path_summary.setText("Choose two different nodes to trace.")
            self.path_steps.clear()
            self.view.set_path_highlight([], [])
            return
        nodes = self.db.list_nodes()
        edges = self.db.list_edges()
        result = trace_relationship_path(nodes, edges, start_id, end_id)
        self.path_summary.setText(result.summary)
        self.path_steps.clear()
        if result.node_ids and result.edge_ids:
            node_index = {node["id"]: node for node in nodes}
            self.view.set_path_highlight(result.node_ids, result.edge_ids)
            for step in result.steps:
                left = node_index.get(step["from"], {})
                right = node_index.get(step["to"], {})
                self.path_steps.addItem(
                    f"{left.get('label', step['from'])} --{step['relation']}--> {right.get('label', step['to'])}"
                )
        else:
            self.view.set_path_highlight([], [])
            self.path_steps.addItem(result.summary)


class TimelinePanel(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(QLabel("Timeline"))
        layout.addWidget(self.list, 1)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        discoveries = [
            {"created_at": row["created_at"], "label": row["query"], "detail": row["summary"], "kind": "Discovery"}
            for row in self.db.recent_discoveries(limit=100)
        ]
        archaeology = [
            {"created_at": row["created_at"], "label": row["target"], "detail": row["source"], "kind": "Archaeology"}
            for row in self.db.recent_archaeology(limit=100)
        ]
        rows = sorted(discoveries + archaeology, key=lambda row: row["created_at"], reverse=True)
        for row in rows:
            self.list.addItem(f"{row['created_at']} | {row['kind']} | {row['label']} | {row['detail']}")


class ArchaeologyPanel(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(QLabel("Internet Archaeology"))
        layout.addWidget(self.output, 1)
        self.reload()

    def reload(self) -> None:
        snapshots = self.db.recent_archaeology(limit=200)
        self.output.setPlainText(json.dumps(snapshots, indent=2))


class TechnologiesPanel(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout.addWidget(QLabel("Technology Inventory"))
        layout.addWidget(self.list, 1)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for node in self.db.list_nodes():
            if node["kind"] == "Technology":
                confidence = node["metadata"].get("confidence")
                self.list.addItem(f"{node['label']}  confidence={confidence}")


class ReportsPanel(QWidget):
    def __init__(self, db: Database, get_graph_widget=None) -> None:
        super().__init__()
        self.db = db
        self.get_graph_widget = get_graph_widget
        layout = QVBoxLayout(self)
        self.status = QLabel("Export the current graph as JSON, Markdown, HTML, or CSV.")
        self.status.setWordWrap(True)
        layout.addWidget(QLabel("Reports"))
        layout.addWidget(self.status)
        buttons = QGridLayout()
        self.json_button = QPushButton("Export JSON")
        self.md_button = QPushButton("Export Markdown")
        self.html_button = QPushButton("Export HTML")
        self.csv_button = QPushButton("Export CSV")
        self.png_button = QPushButton("Export Graph Snapshot")
        self.json_button.clicked.connect(lambda: self.export("json"))
        self.md_button.clicked.connect(lambda: self.export("md"))
        self.html_button.clicked.connect(lambda: self.export("html"))
        self.csv_button.clicked.connect(lambda: self.export("csv"))
        self.png_button.clicked.connect(lambda: self.export("png"))
        buttons.addWidget(self.json_button, 0, 0)
        buttons.addWidget(self.md_button, 0, 1)
        buttons.addWidget(self.html_button, 1, 0)
        buttons.addWidget(self.csv_button, 1, 1)
        buttons.addWidget(self.png_button, 2, 0, 1, 2)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def export(self, kind: str) -> None:
        base = QFileDialog.getExistingDirectory(self, "Choose export directory")
        if not base:
            return
        self._export_to(Path(base), kind)

    def quick_generate_report(self) -> None:
        output = Path.home() / ".echomap" / "reports"
        output.mkdir(parents=True, exist_ok=True)
        self._export_to(output, "md")
        self._export_to(output, "png")
        self.status.setText(f"Generated quick report at {output}")

    def _export_to(self, output: Path, kind: str) -> None:
        graph = self.db.export_graph()
        if kind == "json":
            export_json(graph, output / "echomap-graph.json")
        elif kind == "md":
            export_markdown(graph, output / f"echomap-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
        elif kind == "html":
            export_html(graph, output / "echomap-report.html")
        elif kind == "csv":
            export_csv(graph, output / "echomap-nodes.csv", output / "echomap-edges.csv")
        elif kind == "png":
            self._export_snapshot(output / "echomap-graph.png")
            return
        self.status.setText(f"Exported {kind.upper()} to {output}")

    def _export_snapshot(self, path: Path) -> None:
        widget = self.get_graph_widget() if callable(self.get_graph_widget) else None
        if widget is None:
            self.status.setText("Graph snapshot unavailable: graph view not attached.")
            return
        pixmap = widget.grab()
        if pixmap.isNull():
            self.status.setText("Graph snapshot unavailable: capture failed.")
            return
        pixmap.save(str(path), "PNG")
        self.status.setText(f"Exported PNG snapshot to {path}")


class PublicIntelPanel(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self._playback_frames: list[dict] = []
        self._playback_index = 0
        self._echotrail_result: EchoTrailResult | None = None
        self._radar_result: AgencyRadarResult | None = None
        self._radius_result: SurveillanceRadiusResult | None = None
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(750)
        self._play_timer.timeout.connect(self._advance_playback)

        layout = QVBoxLayout(self)
        header = QLabel("Public Intelligence")
        header.setStyleSheet("font-size: 24px; font-weight: 700; color: #e5e7eb;")
        subheader = QLabel("Layers, FOIA requests, agenda scanning, citations, and timeline playback.")
        subheader.setWordWrap(True)
        subheader.setStyleSheet("color: #94a3b8;")
        layout.addWidget(header)
        layout.addWidget(subheader)

        toolbar = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.reload)
        self.save_layer_button = QPushButton("Save Layer")
        self.save_layer_button.clicked.connect(self.save_layer_from_selection)
        self.scan_agenda_button = QPushButton("Scan Agenda")
        self.scan_agenda_button.clicked.connect(self.scan_agenda)
        self.ingest_button = QPushButton("Ingest Document")
        self.ingest_button.clicked.connect(self.ingest_document)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.save_layer_button)
        toolbar.addWidget(self.scan_agenda_button)
        toolbar.addWidget(self.ingest_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.layers_tab = QWidget()
        layers_layout = QHBoxLayout(self.layers_tab)
        left_layers = QVBoxLayout()
        self.layer_list = QListWidget()
        self.layer_list.currentItemChanged.connect(self._show_layer_details)
        self.layer_list.itemChanged.connect(self._toggle_layer_visibility)
        left_layers.addWidget(QLabel("Layers"))
        left_layers.addWidget(self.layer_list, 1)
        self.layer_visibility = QCheckBox("Visible")
        self.layer_visibility.stateChanged.connect(self._apply_layer_visibility)
        left_layers.addWidget(self.layer_visibility)
        layers_layout.addLayout(left_layers, 1)
        self.layer_details = QTextEdit()
        self.layer_details.setReadOnly(True)
        self.layer_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layers_layout.addWidget(self.layer_details, 2)
        self.tabs.addTab(self.layers_tab, "Layers")

        self.requests_tab = QWidget()
        requests_layout = QHBoxLayout(self.requests_tab)
        request_form_wrap = QWidget()
        request_form = QFormLayout(request_form_wrap)
        self.request_agency = QLineEdit()
        self.request_subject = QLineEdit()
        self.request_date = QLineEdit()
        self.request_due = QLineEdit()
        self.request_status = QComboBox()
        self.request_status.addItems(["Pending", "Overdue", "Completed", "Denied", "Needs appeal"])
        self.request_response = QLineEdit()
        self.request_amount = QLineEdit()
        self.request_vendor = QLineEdit()
        self.request_retention = QLineEdit()
        self.request_sharing = QLineEdit()
        self.request_termination = QLineEdit()
        self.request_source = QLineEdit()
        self.request_confidence = QLineEdit("0.75")
        self.request_notes = QPlainTextEdit()
        self.request_notes.setPlaceholderText("Contract intelligence notes, response status, or follow-up leads.")
        request_form.addRow("Agency", self.request_agency)
        request_form.addRow("Subject", self.request_subject)
        request_form.addRow("Request Date", self.request_date)
        request_form.addRow("Due Date", self.request_due)
        request_form.addRow("Status", self.request_status)
        request_form.addRow("Response Date", self.request_response)
        request_form.addRow("Contract Amount", self.request_amount)
        request_form.addRow("Vendor Contact", self.request_vendor)
        request_form.addRow("Retention", self.request_retention)
        request_form.addRow("Sharing", self.request_sharing)
        request_form.addRow("Termination", self.request_termination)
        request_form.addRow("Source URL", self.request_source)
        request_form.addRow("Confidence", self.request_confidence)
        request_form.addRow("Notes", self.request_notes)
        self.save_request_button = QPushButton("Save Request")
        self.save_request_button.clicked.connect(self.save_public_request)
        request_form.addRow(self.save_request_button)
        requests_layout.addWidget(request_form_wrap, 1)
        request_side = QVBoxLayout()
        self.request_list = QListWidget()
        self.request_list.currentItemChanged.connect(self._show_request_details)
        self.request_details = QTextEdit()
        self.request_details.setReadOnly(True)
        self.request_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        request_side.addWidget(QLabel("Tracked Requests"))
        request_side.addWidget(self.request_list, 1)
        request_side.addWidget(QLabel("Contract Intelligence"))
        request_side.addWidget(self.request_details, 1)
        requests_layout.addLayout(request_side, 1)
        self.tabs.addTab(self.requests_tab, "FOIA / Contracts")

        self.documents_tab = QWidget()
        docs_layout = QVBoxLayout(self.documents_tab)
        self.agenda_title = QLineEdit()
        self.agenda_title.setPlaceholderText("Meeting title")
        self.agenda_source = QLineEdit()
        self.agenda_source.setPlaceholderText("Agenda source URL")
        self.agenda_input = QPlainTextEdit()
        self.agenda_input.setPlaceholderText("Paste agenda text here to scan for civic-tech and surveillance leads.")
        docs_row = QHBoxLayout()
        self.scan_agenda_file_button = QPushButton("Load File")
        self.scan_agenda_file_button.clicked.connect(self.load_agenda_file)
        self.scan_agenda_text_button = QPushButton("Scan Text")
        self.scan_agenda_text_button.clicked.connect(self.scan_agenda)
        docs_row.addWidget(self.scan_agenda_file_button)
        docs_row.addWidget(self.scan_agenda_text_button)
        docs_row.addStretch(1)
        self.document_output = QTextEdit()
        self.document_output.setReadOnly(True)
        self.document_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        docs_layout.addWidget(QLabel("Meeting Agenda Scanner"))
        docs_layout.addWidget(self.agenda_title)
        docs_layout.addWidget(self.agenda_source)
        docs_layout.addWidget(self.agenda_input, 2)
        docs_layout.addLayout(docs_row)
        docs_layout.addWidget(self.document_output, 1)
        self.tabs.addTab(self.documents_tab, "Agenda / Documents")

        self.playback_tab = QWidget()
        playback_layout = QVBoxLayout(self.playback_tab)
        playback_top = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.playback_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_slider.valueChanged.connect(self._show_playback_frame)
        playback_top.addWidget(self.play_button)
        playback_top.addWidget(self.playback_slider, 1)
        playback_layout.addLayout(playback_top)
        self.playback_summary = QLabel("Playback a timeline slice from discoveries, requests, and citations.")
        self.playback_summary.setWordWrap(True)
        self.playback_details = QTextEdit()
        self.playback_details.setReadOnly(True)
        self.playback_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.heatmap_summary = QLabel("")
        self.heatmap_summary.setWordWrap(True)
        self.heatmap_hotspots = QListWidget()
        self.heatmap_hotspots.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        playback_layout.addWidget(self.playback_summary)
        playback_layout.addWidget(QLabel("Current Frame"))
        playback_layout.addWidget(self.playback_details, 1)
        playback_layout.addWidget(QLabel("Heatmap Summary"))
        playback_layout.addWidget(self.heatmap_summary)
        playback_layout.addWidget(QLabel("Heatmap Hotspots"))
        playback_layout.addWidget(self.heatmap_hotspots, 1)
        self.tabs.addTab(self.playback_tab, "Timeline / Heatmap")

        self.citations_tab = QWidget()
        citations_layout = QHBoxLayout(self.citations_tab)
        self.citation_list = QListWidget()
        self.citation_list.currentItemChanged.connect(self._show_citation_details)
        self.citation_details = QTextEdit()
        self.citation_details.setReadOnly(True)
        self.citation_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        citations_layout.addWidget(self.citation_list, 1)
        citations_layout.addWidget(self.citation_details, 1)
        self.tabs.addTab(self.citations_tab, "Source Citations")

        self.workspaces_tab = QWidget()
        workspaces_layout = QHBoxLayout(self.workspaces_tab)
        workspace_left = QVBoxLayout()
        self.workspace_list = QListWidget()
        self.workspace_list.currentItemChanged.connect(self._show_workspace_details)
        workspace_left.addWidget(QLabel("Projects / Workspaces"))
        workspace_left.addWidget(self.workspace_list, 1)
        workspace_form = QFormLayout()
        self.workspace_name = QLineEdit()
        self.workspace_description = QLineEdit()
        self.workspace_notes = QPlainTextEdit()
        self.workspace_notes.setPlaceholderText("Project notes, scope, and source context.")
        self.workspace_create_button = QPushButton("Create Workspace")
        self.workspace_create_button.clicked.connect(self.create_workspace)
        self.workspace_activate_button = QPushButton("Activate Workspace")
        self.workspace_activate_button.clicked.connect(self.activate_workspace)
        workspace_form.addRow("Name", self.workspace_name)
        workspace_form.addRow("Description", self.workspace_description)
        workspace_form.addRow("Notes", self.workspace_notes)
        workspace_form.addRow(self.workspace_create_button)
        workspace_form.addRow(self.workspace_activate_button)
        workspace_left.addLayout(workspace_form)
        workspaces_layout.addLayout(workspace_left, 1)
        self.workspace_details = QTextEdit()
        self.workspace_details.setReadOnly(True)
        self.workspace_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        workspaces_layout.addWidget(self.workspace_details, 1)
        self.tabs.addTab(self.workspaces_tab, "Workspaces")

        self.profile_tab = QWidget()
        profile_layout = QHBoxLayout(self.profile_tab)
        profile_left = QVBoxLayout()
        self.profile_query = QLineEdit()
        self.profile_query.setPlaceholderText("Enter an agency, town, company, or organization")
        self.profile_button = QPushButton("Build Profile")
        self.profile_button.clicked.connect(self.build_profile)
        self.profile_geocode_button = QPushButton("Geocode Input")
        self.profile_geocode_button.clicked.connect(self.geocode_profile_input)
        self.profile_snapshot_button = QPushButton("Snapshot Source")
        self.profile_snapshot_button.clicked.connect(self.snapshot_profile_source)
        profile_left.addWidget(QLabel("Agency / Entity Profile"))
        profile_left.addWidget(self.profile_query)
        profile_left.addWidget(self.profile_button)
        profile_left.addWidget(self.profile_geocode_button)
        profile_left.addWidget(self.profile_snapshot_button)
        profile_left.addStretch(1)
        profile_layout.addLayout(profile_left, 1)
        self.profile_output = QTextEdit()
        self.profile_output.setReadOnly(True)
        self.profile_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        profile_layout.addWidget(self.profile_output, 2)
        self.tabs.addTab(self.profile_tab, "Profiles")

        self.io_tab = QWidget()
        io_layout = QVBoxLayout(self.io_tab)
        io_top = QHBoxLayout()
        self.import_table_button = QPushButton("Import CSV / Excel")
        self.import_table_button.clicked.connect(self.import_tabular_file)
        self.change_detect_button = QPushButton("Detect Change")
        self.change_detect_button.clicked.connect(self.detect_change)
        self.export_map_button = QPushButton("Export Public Map")
        self.export_map_button.clicked.connect(self.export_public_map)
        io_top.addWidget(self.import_table_button)
        io_top.addWidget(self.change_detect_button)
        io_top.addWidget(self.export_map_button)
        io_top.addStretch(1)
        io_layout.addLayout(io_top)
        self.import_source_path = QLineEdit()
        self.import_source_path.setPlaceholderText("CSV or Excel file path")
        self.import_source_title = QLineEdit()
        self.import_source_title.setPlaceholderText("Optional dataset title")
        self.import_source_kind = QLineEdit("csv")
        self.change_source_key = QLineEdit()
        self.change_source_key.setPlaceholderText("Source key for change detection")
        self.change_before = QPlainTextEdit()
        self.change_before.setPlaceholderText("Previous snapshot / text")
        self.change_after = QPlainTextEdit()
        self.change_after.setPlaceholderText("Current snapshot / text")
        self.io_output = QTextEdit()
        self.io_output.setReadOnly(True)
        self.io_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        io_layout.addWidget(QLabel("Importer / Change Detection"))
        io_layout.addWidget(self.import_source_path)
        io_layout.addWidget(self.import_source_title)
        io_layout.addWidget(self.import_source_kind)
        io_layout.addWidget(self.change_source_key)
        io_layout.addWidget(self.change_before, 1)
        io_layout.addWidget(self.change_after, 1)
        io_layout.addWidget(self.io_output, 1)
        self.tabs.addTab(self.io_tab, "Import / Export")

        self.signature_tab = QWidget()
        signature_layout = QVBoxLayout(self.signature_tab)
        dashboard_frame = QFrame()
        dashboard_frame.setStyleSheet("QFrame { background: #111827; border: 1px solid #334155; border-radius: 14px; }")
        dashboard_layout = QHBoxLayout(dashboard_frame)
        self.signature_workspace_label = QLabel("Workspace: Default Workspace")
        self.signature_workspace_label.setStyleSheet("font-weight: 700; color: #e5e7eb;")
        self.signature_counts_label = QLabel("Presets: 0 | Requests: 0 | Citations: 0 | Layers: 0")
        self.signature_counts_label.setStyleSheet("color: #94a3b8;")
        dashboard_layout.addWidget(self.signature_workspace_label)
        dashboard_layout.addWidget(self.signature_counts_label)
        dashboard_layout.addStretch(1)
        signature_layout.addWidget(dashboard_frame)
        self.signature_tabs = QTabWidget()
        signature_layout.addWidget(self.signature_tabs, 1)

        self.echotrail_tab = QWidget()
        echotrail_layout = QVBoxLayout(self.echotrail_tab)
        trail_top = QHBoxLayout()
        self.echotrail_seed = QLineEdit()
        self.echotrail_seed.setPlaceholderText("Seed a trail with a vendor, agency, technology, or keyword")
        self.echotrail_button = QPushButton("Build EchoTrail")
        self.echotrail_button.clicked.connect(self.build_echo_trail)
        trail_top.addWidget(self.echotrail_seed, 1)
        trail_top.addWidget(self.echotrail_button)
        echotrail_layout.addLayout(trail_top)
        trail_split = QSplitter()
        trail_left = QWidget()
        trail_left_layout = QVBoxLayout(trail_left)
        self.echotrail_tree = QTreeWidget()
        self.echotrail_tree.setHeaderLabels(["Step", "Type"])
        self.echotrail_tree.itemClicked.connect(self._show_echotrail_step)
        trail_left_layout.addWidget(QLabel("Discovery Trail"))
        trail_left_layout.addWidget(self.echotrail_tree, 1)
        trail_right = QWidget()
        trail_right_layout = QVBoxLayout(trail_right)
        self.echotrail_details = QTextEdit()
        self.echotrail_details.setReadOnly(True)
        self.echotrail_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        trail_right_layout.addWidget(QLabel("Selected Step Evidence"))
        trail_right_layout.addWidget(self.echotrail_details, 1)
        trail_split.addWidget(trail_left)
        trail_split.addWidget(trail_right)
        trail_split.setSizes([380, 420])
        echotrail_layout.addWidget(trail_split, 1)
        self.echotrail_output = QTextEdit()
        self.echotrail_output.setReadOnly(True)
        self.echotrail_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.echotrail_output.setPlaceholderText("Raw EchoTrail payload will appear here.")
        echotrail_layout.addWidget(self.echotrail_output, 1)
        self.signature_tabs.addTab(self.echotrail_tab, "EchoTrail")

        self.radar_tab = QWidget()
        radar_layout = QVBoxLayout(self.radar_tab)
        radar_top = QHBoxLayout()
        self.radar_query = QLineEdit()
        self.radar_query.setPlaceholderText("Search for an agency, vendor, technology, or clue")
        self.radar_button = QPushButton("Run Agency Radar")
        self.radar_button.clicked.connect(self.run_agency_radar)
        radar_top.addWidget(self.radar_query, 1)
        radar_top.addWidget(self.radar_button)
        radar_layout.addLayout(radar_top)
        preset_row = QHBoxLayout()
        self.radar_preset_name = QLineEdit()
        self.radar_preset_name.setPlaceholderText("Preset name")
        self.save_radar_preset_button = QPushButton("Save Preset")
        self.save_radar_preset_button.clicked.connect(self.save_radar_preset)
        self.load_radar_preset_button = QPushButton("Load Preset")
        self.load_radar_preset_button.clicked.connect(self.load_radar_preset)
        self.delete_radar_preset_button = QPushButton("Delete Preset")
        self.delete_radar_preset_button.clicked.connect(self.delete_radar_preset)
        preset_row.addWidget(self.radar_preset_name, 1)
        preset_row.addWidget(self.save_radar_preset_button)
        preset_row.addWidget(self.load_radar_preset_button)
        preset_row.addWidget(self.delete_radar_preset_button)
        radar_layout.addLayout(preset_row)
        self.radar_preset_list = QListWidget()
        self.radar_preset_list.currentItemChanged.connect(self._show_radar_preset)
        radar_layout.addWidget(QLabel("Saved Radar Presets"))
        radar_layout.addWidget(self.radar_preset_list, 1)
        self.radar_output = QTextEdit()
        self.radar_output.setReadOnly(True)
        self.radar_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        radar_layout.addWidget(self.radar_output, 1)
        self.signature_tabs.addTab(self.radar_tab, "Agency Radar")

        self.radius_tab = QWidget()
        radius_layout = QVBoxLayout(self.radius_tab)
        radius_top = QHBoxLayout()
        self.radius_center = QLineEdit()
        self.radius_center.setPlaceholderText("Center place name or geocodable address")
        self.radius_km = QLineEdit("1.5")
        self.radius_km.setPlaceholderText("Radius km")
        self.radius_button = QPushButton("Analyze Radius")
        self.radius_button.clicked.connect(self.analyze_surveillance_radius)
        radius_top.addWidget(self.radius_center, 1)
        radius_top.addWidget(self.radius_km)
        radius_top.addWidget(self.radius_button)
        self.radius_package_button = QPushButton("Export GIS Package")
        self.radius_package_button.clicked.connect(self.export_radius_package)
        radius_top.addWidget(self.radius_package_button)
        radius_layout.addLayout(radius_top)
        self.radius_overlays = QListWidget()
        self.radius_overlays.currentItemChanged.connect(self._show_radius_overlay)
        radius_layout.addWidget(QLabel("GIS Overlays"))
        radius_layout.addWidget(self.radius_overlays, 1)
        self.radius_overlay_details = QTextEdit()
        self.radius_overlay_details.setReadOnly(True)
        self.radius_overlay_details.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        radius_layout.addWidget(self.radius_overlay_details, 1)
        self.radius_output = QTextEdit()
        self.radius_output.setReadOnly(True)
        self.radius_output.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        radius_layout.addWidget(self.radius_output, 1)
        self.signature_tabs.addTab(self.radius_tab, "Surveillance Radius")

        self.tabs.addTab(self.signature_tab, "Signature")

        self.reload()

    def reload(self) -> None:
        self._reload_layers()
        self._reload_requests()
        self._reload_citations()
        self._reload_playback()
        self._reload_workspaces()
        self._reload_profile()
        self._reload_signature_dashboard()
        self._reload_radar_presets()

    def _reload_layers(self) -> None:
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        rows = self.db.list_public_layers(limit=200)
        if not rows:
            self.layer_list.addItem("No public layers yet.")
        for row in rows:
            item = QListWidgetItem(f"{row['name']} [{row['kind']}]")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row["visible"] else Qt.CheckState.Unchecked)
            self.layer_list.addItem(item)
        self.layer_list.blockSignals(False)
        self._show_layer_details(self.layer_list.currentItem(), None)

    def _reload_requests(self) -> None:
        self.request_list.clear()
        rows = self.db.list_public_requests(limit=200)
        if not rows:
            self.request_list.addItem("No tracked requests yet.")
        for row in rows:
            item = QListWidgetItem(f"{row['agency']} | {row['subject']} | {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.request_list.addItem(item)
        self._show_request_details(self.request_list.currentItem(), None)

    def _reload_citations(self) -> None:
        self.citation_list.clear()
        rows = self.db.list_source_citations(limit=200)
        if not rows:
            self.citation_list.addItem("No source citations yet.")
        for row in rows:
            item = QListWidgetItem(f"{row['entity_type']}:{row['entity_id']} | {row['source_type']} | {row['confidence']:.2f}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.citation_list.addItem(item)
        self._show_citation_details(self.citation_list.currentItem(), None)

    def _reload_workspaces(self) -> None:
        self.workspace_list.clear()
        rows = self.db.list_workspaces(limit=100)
        if not rows:
            self.workspace_list.addItem("No workspaces yet.")
        for row in rows:
            label = f"{row['name']}{' (active)' if row.get('is_active') else ''}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.workspace_list.addItem(item)
        self._show_workspace_details(self.workspace_list.currentItem(), None)

    def _reload_profile(self) -> None:
        if not self.profile_query.text().strip():
            self.profile_output.setPlainText("Pick an entity and build a profile to see related agencies, vendors, requests, and citations.")
        if not self.echotrail_seed.text().strip():
            self.echotrail_details.setPlainText("Build a visible discovery trail from a seed entity, clue, or technology.")
        if not self.radar_query.text().strip():
            self.radar_output.setPlainText("Search for public clues across agencies, vendors, contracts, agendas, and citations.")
        if not self.radius_center.text().strip():
            self.radius_output.setPlainText("Geocode a place and show nearby schools, roads, neighborhoods, government buildings, and cameras.")

    def _reload_signature_dashboard(self) -> None:
        workspace = self.db.active_workspace()
        self.signature_workspace_label.setText(f"Workspace: {workspace.get('name', 'Default Workspace')}")
        self.signature_counts_label.setText(
            "Presets: "
            f"{len(self.db.list_signature_presets(limit=200))} | "
            f"Requests: {len(self.db.list_public_requests(limit=200))} | "
            f"Citations: {len(self.db.list_source_citations(limit=200))} | "
            f"Layers: {len(self.db.list_public_layers(limit=200))}"
        )

    def _reload_radar_presets(self) -> None:
        self.radar_preset_list.clear()
        presets = self.db.list_signature_presets(limit=200, mode="radar")
        if not presets:
            self.radar_preset_list.addItem("No saved radar presets yet.")
            return
        for preset in presets:
            item = QListWidgetItem(f"{preset['name']} | {preset['query']} | {preset['center_label'] or 'no center'}")
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            self.radar_preset_list.addItem(item)

    def _reload_playback(self) -> None:
        events = self.db.public_timeline_events(limit=200)
        self._playback_frames = build_playback_frames(events)
        self.playback_slider.blockSignals(True)
        self.playback_slider.setMinimum(0)
        self.playback_slider.setMaximum(max(0, len(self._playback_frames) - 1))
        self.playback_slider.setValue(0)
        self.playback_slider.blockSignals(False)
        self._show_playback_frame(0)
        points = []
        for citation in self.db.list_source_citations(limit=200):
            payload = citation.get("payload", {})
            if isinstance(payload, dict):
                if "latitude" in payload and "longitude" in payload:
                    points.append({"label": payload.get("label", citation["entity_id"]), "latitude": payload["latitude"], "longitude": payload["longitude"], "kind": citation["entity_type"]})
                for point in payload.get("points", []) if isinstance(payload.get("points", []), list) else []:
                    if "latitude" in point and "longitude" in point:
                        points.append(point)
        summary = summarize_heatmap(points)
        self.heatmap_summary.setText(summary.summary)
        self.heatmap_hotspots.clear()
        for hotspot in summary.hotspots:
            self.heatmap_hotspots.addItem(f"{hotspot['latitude']:.4f}, {hotspot['longitude']:.4f} | count={hotspot['count']}")

    def save_layer_from_selection(self) -> None:
        item = self.layer_list.currentItem()
        if not item:
            return
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        if layer_id is None:
            return
        self.db.set_public_layer_visibility(int(layer_id), item.checkState() == Qt.CheckState.Checked)
        self.reload()

    def _apply_layer_visibility(self, *_args) -> None:
        item = self.layer_list.currentItem()
        if not item:
            return
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        if layer_id is None:
            return
        self.db.set_public_layer_visibility(int(layer_id), self.layer_visibility.isChecked())
        self.reload()

    def _toggle_layer_visibility(self, item: QListWidgetItem) -> None:
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        if layer_id is None:
            return
        self.db.set_public_layer_visibility(int(layer_id), item.checkState() == Qt.CheckState.Checked)

    def _show_layer_details(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self.layer_details.setPlainText("Select a layer to see details.")
            self.layer_visibility.setChecked(False)
            return
        layer_id = current.data(Qt.ItemDataRole.UserRole)
        if layer_id is None:
            self.layer_details.setPlainText(str(current.text()))
            return
        layer = next((row for row in self.db.list_public_layers(limit=500) if row["id"] == int(layer_id)), None)
        if not layer:
            self.layer_details.setPlainText("Layer not found.")
            return
        self.layer_visibility.blockSignals(True)
        self.layer_visibility.setChecked(layer["visible"])
        self.layer_visibility.blockSignals(False)
        self.layer_details.setPlainText(json.dumps(layer, indent=2))

    def save_public_request(self) -> None:
        payload = {
            "contract_amount": self.request_amount.text().strip(),
            "vendor_contact": self.request_vendor.text().strip(),
            "retention_policy": self.request_retention.text().strip(),
            "sharing_policy": self.request_sharing.text().strip(),
            "termination_clause": self.request_termination.text().strip(),
            "public_source": self.request_source.text().strip(),
            "confidence_score": self.request_confidence.text().strip(),
        }
        request_id = self.db.save_public_request(
            self.request_agency.text().strip() or "Unknown Agency",
            self.request_subject.text().strip() or "Untitled Request",
            self.request_date.text().strip() or date.today().isoformat(),
            self.request_due.text().strip() or date.today().isoformat(),
            self.request_status.currentText(),
            self.request_response.text().strip(),
            self.request_notes.toPlainText().strip(),
            [],
            payload,
        )
        self.request_details.setPlainText(f"Saved request #{request_id}\n\n{json.dumps(payload, indent=2)}")
        self.reload()

    def _show_request_details(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self.request_details.setPlainText("Select a request to see contract intelligence.")
            return
        request_id = current.data(Qt.ItemDataRole.UserRole)
        if request_id is None:
            self.request_details.setPlainText(str(current.text()))
            return
        request = self.db.get_public_request(int(request_id))
        self.request_details.setPlainText(json.dumps(request, indent=2) if request else "Request not found.")

    def load_agenda_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Agenda",
            "",
            "Documents (*.txt *.md *.html *.htm *.pdf *.csv *.json);;All Files (*.*)",
        )
        if not path:
            return
        text = _read_text_file(Path(path))
        self.agenda_input.setPlainText(text)
        if not self.agenda_title.text().strip():
            self.agenda_title.setText(Path(path).stem.replace("_", " ").strip())
        self.document_output.setPlainText(f"Loaded agenda file: {path}")

    def scan_agenda(self) -> None:
        text = self.agenda_input.toPlainText().strip()
        if not text:
            self.document_output.setPlainText("Paste or load agenda text first.")
            return
        result = scan_agenda_text(
            text,
            title=self.agenda_title.text().strip() or "Public Meeting Agenda",
            source_url=self.agenda_source.text().strip(),
        )
        self.db.upsert_nodes(result.nodes)
        self.db.upsert_edges(result.edges)
        if result.suggested_layer:
            self.db.save_public_layer(
                result.suggested_layer.name,
                result.suggested_layer.kind,
                result.suggested_layer.visible,
                result.suggested_layer.color,
                result.suggested_layer.notes,
                result.suggested_layer.payload,
            )
        for citation in result.citations:
            self.db.save_source_citation(
                citation.entity_type,
                citation.entity_id,
                citation.source_type,
                citation.source_url,
                citation.uploaded_path,
                citation.screenshot_path,
                citation.confidence,
                citation.retrieved_at,
                citation.notes,
                citation.payload,
            )
        self.document_output.setPlainText(
            result.summary
            + "\n\nMatches:\n"
            + json.dumps(result.matches, indent=2)
            + "\n\nAgencies:\n"
            + json.dumps(result.agencies, indent=2)
            + "\n\nVendors:\n"
            + json.dumps(result.vendors, indent=2)
        )
        self.reload()

    def ingest_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Document",
            "",
            "Documents (*.txt *.md *.html *.htm *.pdf *.csv *.json);;All Files (*.*)",
        )
        if not path:
            return
        result = ingest_document_file(Path(path), layer_name=Path(path).stem.replace("_", " "))
        self.db.upsert_nodes(result.nodes)
        self.db.upsert_edges(result.edges)
        if result.suggested_layer:
            self.db.save_public_layer(
                result.suggested_layer.name,
                result.suggested_layer.kind,
                result.suggested_layer.visible,
                result.suggested_layer.color,
                result.suggested_layer.notes,
                result.suggested_layer.payload,
            )
        for citation in result.citations:
            self.db.save_source_citation(
                citation.entity_type,
                citation.entity_id,
                citation.source_type,
                citation.source_url,
                citation.uploaded_path,
                citation.screenshot_path,
                citation.confidence,
                citation.retrieved_at,
                citation.notes,
                citation.payload,
            )
        self.document_output.setPlainText(
            result.summary
            + "\n\nEntities:\n"
            + json.dumps(result.entities[:50], indent=2)
            + "\n\nPoints:\n"
            + json.dumps(result.points[:50], indent=2)
        )
        self.reload()

    def toggle_playback(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self.play_button.setText("Play")
            return
        self._play_timer.start()
        self.play_button.setText("Pause")

    def _advance_playback(self) -> None:
        if not self._playback_frames:
            return
        next_value = self.playback_slider.value() + 1
        if next_value > self.playback_slider.maximum():
            next_value = 0
        self.playback_slider.setValue(next_value)

    def _show_playback_frame(self, index: int) -> None:
        if not self._playback_frames:
            self.playback_summary.setText("No timeline events yet.")
            self.playback_details.setPlainText("Add discoveries, requests, or citations to populate the timeline.")
            return
        index = max(0, min(index, len(self._playback_frames) - 1))
        frame = self._playback_frames[index]
        self._playback_index = index
        self.playback_summary.setText(f"{index + 1}/{len(self._playback_frames)} | {frame['kind']} | {frame['timestamp']}")
        self.playback_details.setPlainText(json.dumps(frame["payload"], indent=2))

    def _show_citation_details(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self.citation_details.setPlainText("Select a citation to see evidence details.")
            return
        citation_id = current.data(Qt.ItemDataRole.UserRole)
        if citation_id is None:
            self.citation_details.setPlainText(str(current.text()))
            return
        citation = next((row for row in self.db.list_source_citations(limit=500) if row["id"] == int(citation_id)), None)
        self.citation_details.setPlainText(json.dumps(citation, indent=2) if citation else "Citation not found.")

    def _show_workspace_details(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self.workspace_details.setPlainText("Create a workspace to isolate a research project, map, or investigation.")
            return
        workspace_id = current.data(Qt.ItemDataRole.UserRole)
        if workspace_id is None:
            self.workspace_details.setPlainText(str(current.text()))
            return
        workspace = self.db.get_workspace(int(workspace_id))
        self.workspace_details.setPlainText(json.dumps(workspace, indent=2) if workspace else "Workspace not found.")

    def create_workspace(self) -> None:
        name = self.workspace_name.text().strip()
        if not name:
            self.workspace_details.setPlainText("Enter a workspace name first.")
            return
        workspace_id = self.db.save_workspace(name, self.workspace_description.text().strip(), self.workspace_notes.toPlainText().strip())
        self.workspace_details.setPlainText(f"Created workspace #{workspace_id}\n\n{name}")
        self.workspace_name.clear()
        self.workspace_description.clear()
        self.workspace_notes.clear()
        self.reload()

    def activate_workspace(self) -> None:
        item = self.workspace_list.currentItem()
        if not item:
            self.workspace_details.setPlainText("Select a workspace first.")
            return
        workspace_id = item.data(Qt.ItemDataRole.UserRole)
        if workspace_id is None:
            self.workspace_details.setPlainText(str(item.text()))
            return
        if self.db.set_active_workspace(int(workspace_id)):
            self.workspace_details.setPlainText(f"Activated workspace #{int(workspace_id)}")
            self.reload()
        else:
            self.workspace_details.setPlainText("Workspace not found.")

    def build_profile(self) -> None:
        query = self.profile_query.text().strip()
        if not query:
            self.profile_output.setPlainText("Enter an agency, town, company, or organization name first.")
            return
        profile = build_agency_profile(self.db, query)
        self.profile_output.setPlainText(json.dumps(asdict(profile), indent=2))

    def geocode_profile_input(self) -> None:
        query = self.profile_query.text().strip()
        if not query:
            self.profile_output.setPlainText("Enter a place, agency, or business name first.")
            return
        result = geocode_value(query, fallback_label=query)
        payload = asdict(result)
        self.profile_output.setPlainText(json.dumps(payload, indent=2))
        if result.latitude is not None and result.longitude is not None:
            self.db.save_source_citation(
                "location",
                result.label or query,
                "geocode",
                "",
                "",
                "",
                result.confidence,
                datetime.now().isoformat(),
                f"Geocoded from profile input: {query}",
                payload,
            )
            self.reload()

    def snapshot_profile_source(self) -> None:
        source_key = self.profile_query.text().strip()
        if not source_key:
            self.profile_output.setPlainText("Enter a source key or entity name first.")
            return
        text = self.profile_output.toPlainText().strip() or self.profile_query.text().strip()
        snapshot = snapshot_public_source(
            self.db,
            source_key,
            text,
            title=source_key,
            source_type="profile_snapshot",
        )
        self.profile_output.setPlainText(json.dumps(snapshot, indent=2))
        self.reload()

    def import_tabular_file(self) -> None:
        path_text = self.import_source_path.text().strip()
        if not path_text:
            path_text, _ = QFileDialog.getOpenFileName(
                self,
                "Import CSV / Excel",
                "",
                "Tabular Files (*.csv *.xlsx *.xls);;All Files (*.*)",
            )
        if not path_text:
            return
        path = Path(path_text)
        if not path.exists():
            self.io_output.setPlainText(f"File not found: {path}")
            return
        result = import_tabular_data(
            path,
            title=self.import_source_title.text().strip() or None,
            source_type=self.import_source_kind.text().strip() or None,
        )
        self.db.upsert_nodes(result.nodes)
        self.db.upsert_edges(result.edges)
        if result.points:
            self.db.save_public_layer(
                result.title,
                "dataset",
                True,
                "#38bdf8",
                result.summary,
                {"points": result.points, **result.payload},
            )
        for citation in result.citations:
            self.db.save_source_citation(
                citation.entity_type,
                citation.entity_id,
                citation.source_type,
                citation.source_url,
                citation.uploaded_path,
                citation.screenshot_path,
                citation.confidence,
                citation.retrieved_at,
                citation.notes,
                citation.payload,
            )
        self.io_output.setPlainText(
            result.summary
            + "\n\nRows:\n"
            + json.dumps(result.rows[:20], indent=2)
            + "\n\nPoints:\n"
            + json.dumps(result.points[:20], indent=2)
        )
        self.reload()

    def detect_change(self) -> None:
        source_key = self.change_source_key.text().strip()
        if not source_key:
            self.io_output.setPlainText("Enter a source key for change detection.")
            return
        before = self.change_before.toPlainText()
        after = self.change_after.toPlainText()
        result = compare_public_snapshots(
            before,
            after,
            title=source_key,
            source_key=source_key,
        )
        snapshot_public_source(
            self.db,
            source_key,
            after,
            title=source_key,
            source_type="change_detection",
        )
        self.io_output.setPlainText(json.dumps(asdict(result), indent=2))
        self.reload()

    def export_public_map(self) -> None:
        path_text, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Public Map",
            f"{self.profile_query.text().strip() or 'echomap-public-map'}.html",
            "HTML (*.html);;CSV (*.csv);;GeoJSON (*.geojson);;ZIP (*.zip)",
        )
        if not path_text:
            return
        output = Path(path_text)
        points: list[dict[str, object]] = []
        for citation in self.db.list_source_citations(limit=500):
            payload = citation.get("payload", {})
            if isinstance(payload, dict):
                if "latitude" in payload and "longitude" in payload:
                    points.append(
                        {
                            "label": payload.get("label", citation["entity_id"]),
                            "latitude": payload["latitude"],
                            "longitude": payload["longitude"],
                            "kind": citation["entity_type"],
                        }
                    )
                payload_points = payload.get("points", [])
                if isinstance(payload_points, list):
                    for point in payload_points:
                        if isinstance(point, dict) and "latitude" in point and "longitude" in point:
                            points.append(point)
        if output.suffix.lower() == ".csv":
            path = export_public_map_csv(points, output)
        elif output.suffix.lower() == ".geojson":
            path = export_public_map_geojson(points, output, self.profile_query.text().strip() or "Public Map")
        elif output.suffix.lower() == ".zip":
            path = export_public_map_bundle(
                title=self.profile_query.text().strip() or "Public Map",
                layers=self.db.list_public_layers(limit=200),
                requests=self.db.list_public_requests(limit=200),
                citations=self.db.list_source_citations(limit=200),
                points=points,
                output=output,
            )
        else:
            path = export_public_map_html(
                title=self.profile_query.text().strip() or "Public Map",
                layers=self.db.list_public_layers(limit=200),
                requests=self.db.list_public_requests(limit=200),
                citations=self.db.list_source_citations(limit=200),
                points=points,
                output=output,
            )
        self.io_output.setPlainText(f"Exported public map to {path}")
        self.reload()

    def build_echo_trail(self) -> None:
        seed = self.echotrail_seed.text().strip()
        if not seed:
            self.echotrail_details.setPlainText("Enter a seed entity, vendor, agency, or keyword first.")
            return
        result = build_echotrail(self.db, seed)
        self._echotrail_result = result
        self._populate_echotrail_tree(result)
        self.echotrail_output.setPlainText(json.dumps(asdict(result), indent=2))
        self.reload()

    def run_agency_radar(self) -> None:
        query = self.radar_query.text().strip()
        if not query:
            self.radar_output.setPlainText("Enter a vendor, agency, technology, or clue first.")
            return
        result = agency_radar(self.db, query)
        self._radar_result = result
        self.radar_output.setPlainText(json.dumps(asdict(result), indent=2))
        self.reload()

    def analyze_surveillance_radius(self) -> None:
        center = self.radius_center.text().strip()
        if not center:
            self.radius_output.setPlainText("Enter a center place name or address first.")
            return
        try:
            radius_km = float(self.radius_km.text().strip() or "1.5")
        except ValueError:
            self.radius_output.setPlainText("Radius must be a number.")
            return
        geocoded = geocode_value(center, fallback_label=center, force=True)
        if geocoded.latitude is None or geocoded.longitude is None:
            self.radius_output.setPlainText(f"Could not geocode '{center}'.")
            return
        result = surveillance_radius(
            self.db,
            latitude=geocoded.latitude,
            longitude=geocoded.longitude,
            radius_km=radius_km,
            center_label=geocoded.label or center,
        )
        self._radius_result = result
        self._populate_radius_overlays(result)
        self.radius_output.setPlainText(json.dumps(asdict(result), indent=2))
        self.reload()

    def export_radius_package(self) -> None:
        result = self._radius_result
        if not result:
            self.radius_output.setPlainText("Run a surveillance radius analysis before exporting a GIS package.")
            return
        default_name = f"{result.center_label.replace(' ', '-').lower()}-gis-package.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Export GIS Layer Package", default_name, "ZIP (*.zip)")
        if not path:
            return
        package_path = export_public_radius_package(result, Path(path))
        self.radius_output.setPlainText(f"Exported GIS package to {package_path}")
        self.reload()

    def analyze_and_export_radius_package(self) -> None:
        self.analyze_surveillance_radius()
        if self._radius_result is not None:
            self.export_radius_package()

    def save_radar_preset(self) -> None:
        query = self.radar_query.text().strip()
        if not query:
            self.radar_output.setPlainText("Enter a radar query before saving a preset.")
            return
        preset_name = self.radar_preset_name.text().strip() or query
        result_payload = asdict(self._radar_result) if self._radar_result else {}
        preset_id = self.db.save_signature_preset(
            preset_name,
            "radar",
            query=query,
            notes=f"Saved in workspace {self.db.active_workspace().get('name', '')}",
            payload=result_payload,
        )
        self.radar_output.setPlainText(f"Saved radar preset #{preset_id}: {preset_name}")
        self.reload()

    def load_radar_preset(self) -> None:
        item = self.radar_preset_list.currentItem()
        if not item:
            self.radar_output.setPlainText("Select a radar preset first.")
            return
        preset_id = item.data(Qt.ItemDataRole.UserRole)
        if preset_id is None:
            self.radar_output.setPlainText(str(item.text()))
            return
        preset = self.db.get_signature_preset(int(preset_id))
        if not preset:
            self.radar_output.setPlainText("Preset not found.")
            return
        self.radar_query.setText(preset.get("query", ""))
        self.radar_preset_name.setText(preset.get("name", ""))
        self.radar_output.setPlainText(json.dumps(preset, indent=2))

    def delete_radar_preset(self) -> None:
        item = self.radar_preset_list.currentItem()
        if not item:
            self.radar_output.setPlainText("Select a radar preset first.")
            return
        preset_id = item.data(Qt.ItemDataRole.UserRole)
        if preset_id is None:
            self.radar_output.setPlainText(str(item.text()))
            return
        if self.db.delete_signature_preset(int(preset_id)):
            self.radar_output.setPlainText(f"Deleted radar preset #{int(preset_id)}")
            self.reload()
        else:
            self.radar_output.setPlainText("Preset not found.")

    def _show_radar_preset(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            return
        preset_id = current.data(Qt.ItemDataRole.UserRole)
        if preset_id is None:
            self.radar_output.setPlainText(str(current.text()))
            return
        preset = self.db.get_signature_preset(int(preset_id))
        if preset:
            self.radar_output.setPlainText(json.dumps(preset, indent=2))

    def _populate_echotrail_tree(self, result: EchoTrailResult) -> None:
        self.echotrail_tree.clear()
        self.echotrail_details.clear()
        self.echotrail_tree.setHeaderLabels(["Step", "Type", "Depth", "Hops"])
        for index, step in enumerate(result.steps):
            title = step.get("label", f"Step {index + 1}")
            kind = step.get("kind", step.get("stage", "step"))
            stage = step.get("stage", "step")
            depth = 0
            hops = 0
            payload = step.get("payload", {})
            if stage == "relationship" and isinstance(payload, dict):
                hops = len(payload.get("steps", [])) or max(len(payload.get("node_ids", [])) - 1, 0)
                depth = hops
            elif stage == "neighborhood" and isinstance(payload, dict):
                depth = 1
                hops = max(int(payload.get("neighbor_count", 0)), 1 if payload.get("neighbors") else 0)
            item = QTreeWidgetItem([title, str(kind), str(depth), str(hops)])
            item.setData(0, Qt.ItemDataRole.UserRole, step)
            if isinstance(payload, dict):
                node_ids = [str(node_id) for node_id in payload.get("node_ids", [])]
                edge_ids = [str(edge_id) for edge_id in payload.get("edge_ids", [])]
                substeps = [substep for substep in payload.get("steps", []) if isinstance(substep, dict)]
                for node_id in node_ids:
                    child = QTreeWidgetItem([f"Node {node_id}", "node", str(depth), str(hops)])
                    child.setData(0, Qt.ItemDataRole.UserRole, {"node_id": node_id, "parent_step": title, "depth": depth, "hop_count": hops})
                    item.addChild(child)
                for edge_id in edge_ids:
                    child = QTreeWidgetItem([f"Edge {edge_id}", "edge", str(depth), str(hops)])
                    child.setData(0, Qt.ItemDataRole.UserRole, {"edge_id": edge_id, "parent_step": title, "depth": depth, "hop_count": hops})
                    item.addChild(child)
                for step_index, substep in enumerate(substeps, start=1):
                    child = QTreeWidgetItem(
                        [
                            f"{step_index}. {substep.get('relation', 'chain step')}",
                            str(substep.get("to", "")),
                            str(step_index),
                            str(step_index),
                        ]
                    )
                    enriched_step = dict(substep)
                    enriched_step.setdefault("parent_step", title)
                    enriched_step.setdefault("depth", step_index)
                    enriched_step.setdefault("hop_count", step_index)
                    child.setData(0, Qt.ItemDataRole.UserRole, enriched_step)
                    item.addChild(child)
            self.echotrail_tree.addTopLevelItem(item)
        self.echotrail_tree.expandAll()
        self.echotrail_details.setPlainText(f"{result.summary}\n\nTop-level steps: {len(result.steps)}")

    def _show_echotrail_step(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            self.echotrail_details.setPlainText(item.text(0))
            return
        self.echotrail_details.setPlainText(json.dumps(data, indent=2))

    def _populate_radius_overlays(self, result: SurveillanceRadiusResult) -> None:
        self.radius_overlays.clear()
        self.radius_overlay_details.clear()
        for overlay in result.overlays:
            item = QListWidgetItem(f"{overlay['category']} | {overlay['count']} point(s)")
            item.setData(Qt.ItemDataRole.UserRole, overlay)
            self.radius_overlays.addItem(item)
        self.radius_overlay_details.setPlainText(result.summary)

    def _show_radius_overlay(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            return
        overlay = current.data(Qt.ItemDataRole.UserRole)
        if overlay is None:
            self.radius_overlay_details.setPlainText(str(current.text()))
            return
        self.radius_overlay_details.setPlainText(json.dumps(overlay, indent=2))

class SettingsPanel(QWidget):
    def __init__(self, theme_changed_callback=None, auto_scan_callback=None, auto_scan_enabled: bool = True) -> None:
        super().__init__()
        self._theme_changed_callback = theme_changed_callback
        self._auto_scan_callback = auto_scan_callback
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Settings"))
        self.backend_info = QLabel()
        layout.addWidget(self.backend_info)
        self.backend_snapshot = QLabel()
        self.backend_snapshot.setWordWrap(True)
        layout.addWidget(self.backend_snapshot)
        self.live_stream = QLabel()
        self.live_stream.setWordWrap(True)
        layout.addWidget(self.live_stream)
        self.dark = QPushButton("Toggle Dark Mode")
        self.dark.setCheckable(True)
        self.dark.setChecked(True)
        self.dark.clicked.connect(self._handle_theme_toggle)
        layout.addWidget(self.dark)
        self.auto_scan = QPushButton("Background Scanning")
        self.auto_scan.setCheckable(True)
        self.auto_scan.setChecked(auto_scan_enabled)
        self.auto_scan.clicked.connect(self._handle_auto_scan_toggle)
        layout.addWidget(self.auto_scan)
        self.auto_scan_state = QLabel()
        layout.addWidget(self.auto_scan_state)
        layout.addStretch(1)
        self._sync_auto_scan_label(auto_scan_enabled)

    def set_backend_info(self, text: str) -> None:
        self.backend_info.setText(text)

    def set_backend_snapshot(self, snapshot: dict[str, int | str]) -> None:
        if not snapshot:
            self.backend_snapshot.setText("Backend read snapshot: unavailable")
            return
        self.backend_snapshot.setText(
            "Backend read snapshot: "
            f"{snapshot.get('nodes', 0)} nodes, {snapshot.get('edges', 0)} edges, "
            f"mode={snapshot.get('mode', 'unknown')}, sample={snapshot.get('sample', 'none')}"
        )

    def set_live_stream_status(self, text: str) -> None:
        self.live_stream.setText(f"Live graph sync: {text}")

    def set_auto_scan_state(self, enabled: bool) -> None:
        self.auto_scan.setChecked(enabled)
        self._sync_auto_scan_label(enabled)

    def _handle_theme_toggle(self, checked: bool) -> None:
        if self._theme_changed_callback:
            self._theme_changed_callback(bool(checked))

    def _handle_auto_scan_toggle(self, checked: bool) -> None:
        self._sync_auto_scan_label(bool(checked))
        if self._auto_scan_callback:
            self._auto_scan_callback(bool(checked))

    def _sync_auto_scan_label(self, enabled: bool) -> None:
        self.auto_scan_state.setText(f"Background scanning: {'enabled' if enabled else 'disabled'}")


class OverviewPanel(QWidget):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        header = QLabel("Ecosystem Overview")
        header.setStyleSheet("font-size: 24px; font-weight: 700; color: #e5e7eb;")
        subheader = QLabel("A high-level pulse of the local internet graph.")
        subheader.setStyleSheet("color: #94a3b8;")
        layout.addWidget(header)
        layout.addWidget(subheader)

        self.metric_labels: dict[str, QLabel] = {}
        cards = QGridLayout()
        metrics = [
            ("nodes", "Nodes"),
            ("edges", "Edges"),
            ("avg_degree", "Avg Degree"),
            ("bookmarks", "Bookmarks"),
            ("investigations", "Investigations"),
            ("comparisons", "Comparisons"),
        ]
        for index, (key, title) in enumerate(metrics):
            card = self._create_card(title)
            self.metric_labels[key] = card["value"]
            cards.addWidget(card["frame"], index // 3, index % 3)
        layout.addLayout(cards)

        lower = QGridLayout()
        self.anomalies = QListWidget()
        self.anomalies.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.hubs = QListWidget()
        self.hubs.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.relations = QListWidget()
        self.relations.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        lower.addWidget(QLabel("Anomaly Flags"), 0, 0)
        lower.addWidget(QLabel("Top Hubs"), 0, 1)
        lower.addWidget(QLabel("Relation Mix"), 0, 2)
        lower.addWidget(self.anomalies, 1, 0)
        lower.addWidget(self.hubs, 1, 1)
        lower.addWidget(self.relations, 1, 2)
        layout.addLayout(lower, 1)
        self.reload()

    def _create_card(self, title: str) -> dict[str, QWidget]:
        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame { background: #111827; border: 1px solid #334155; border-radius: 14px; }
            """
        )
        inner = QVBoxLayout(frame)
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #94a3b8; font-size: 12px; text-transform: uppercase;")
        value_label = QLabel("0")
        value_label.setStyleSheet("font-size: 30px; font-weight: 700; color: #f8fafc;")
        inner.addWidget(title_label)
        inner.addWidget(value_label)
        return {"frame": frame, "value": value_label}

    def reload(self) -> None:
        graph = self.db.export_graph()
        nodes = graph["nodes"]
        edges = graph["edges"]
        insights = analyze_graph(nodes, edges)
        degree_map = self.db.node_degree_map()
        total_degree = sum(row["degree"] for row in degree_map)
        avg_degree = round(total_degree / max(1, len(nodes)), 2)

        self.metric_labels["nodes"].setText(str(len(nodes)))
        self.metric_labels["edges"].setText(str(len(edges)))
        self.metric_labels["avg_degree"].setText(str(avg_degree))
        self.metric_labels["bookmarks"].setText(str(len(self.db.list_bookmarks())))
        self.metric_labels["investigations"].setText(str(len(self.db.list_investigations())))
        self.metric_labels["comparisons"].setText(str(len(self.db.recent_comparisons())))

        self.anomalies.clear()
        isolated = [row for row in degree_map if row["degree"] == 0]
        orphan_tech = [node for node in nodes if node.get("kind") == "Technology" and node["id"] in {row["id"] for row in isolated}]
        if isolated:
            self.anomalies.addItem(f"{len(isolated)} isolated nodes detected")
        if orphan_tech:
            self.anomalies.addItem(f"{len(orphan_tech)} orphan technologies detected")
        if insights.top_hubs and insights.top_hubs[0]["degree"] > max(6, len(edges) // 4):
            top = insights.top_hubs[0]
            self.anomalies.addItem(f"High hub concentration around {top['label']}")
        if not self.anomalies.count():
            self.anomalies.addItem("No obvious anomalies detected")
        for explanation in insights.anomaly_explanations:
            self.anomalies.addItem(f"Why: {explanation}")

        self.hubs.clear()
        for hub in insights.top_hubs[:8]:
            self.hubs.addItem(f"{hub['label']} ({hub['kind']}) - {hub['degree']}")

        self.relations.clear()
        for relation, count in sorted(insights.relation_counts.items(), key=lambda item: item[1], reverse=True)[:8]:
            self.relations.addItem(f"{relation} - {count}")


class InvestigationsPanel(QWidget):
    def __init__(self, db: Database, get_selected_node, get_current_query, focus_node_callback=None) -> None:
        super().__init__()
        self.db = db
        self.get_selected_node = get_selected_node
        self.get_current_query = get_current_query
        self.focus_node_callback = focus_node_callback
        self._selected_node = None

        layout = QVBoxLayout(self)
        header = QLabel("Investigations")
        header.setStyleSheet("font-size: 24px; font-weight: 700; color: #e5e7eb;")
        layout.addWidget(header)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search investigations by title, query, notes, or tags")
        self.search.textChanged.connect(self.reload)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        top = QHBoxLayout()
        self.save_button = QPushButton("Save Investigation")
        self.save_button.clicked.connect(self.save_investigation)
        self.load_button = QPushButton("Load Investigation")
        self.load_button.clicked.connect(self.load_investigation)
        self.edit_button = QPushButton("Edit Investigation")
        self.edit_button.clicked.connect(self.edit_investigation)
        self.delete_button = QPushButton("Delete Investigation")
        self.delete_button.clicked.connect(self.delete_investigation)
        self.export_button = QPushButton("Export Investigation")
        self.export_button.clicked.connect(self.export_investigation)
        self.bookmark_button = QPushButton("Bookmark Selected")
        self.bookmark_button.clicked.connect(self.bookmark_selected)
        self.edit_bookmark_button = QPushButton("Edit Bookmark Note")
        self.edit_bookmark_button.clicked.connect(self.edit_bookmark_note)
        self.remove_bookmark_button = QPushButton("Remove Bookmark")
        self.remove_bookmark_button.clicked.connect(self.remove_bookmark)
        top.addWidget(self.save_button)
        top.addWidget(self.load_button)
        top.addWidget(self.edit_button)
        top.addWidget(self.delete_button)
        top.addWidget(self.export_button)
        top.addWidget(self.bookmark_button)
        top.addWidget(self.edit_bookmark_button)
        top.addWidget(self.remove_bookmark_button)
        top.addStretch(1)
        layout.addLayout(top)

        selected_row = QHBoxLayout()
        self.selected_label = QLabel("Selected: none")
        self.selected_label.setStyleSheet("color: #cbd5e1;")
        selected_row.addWidget(self.selected_label)
        selected_row.addStretch(1)
        layout.addLayout(selected_row)

        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Saved Investigations"))
        self.investigation_list = QListWidget()
        self.investigation_list.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.investigation_list.itemClicked.connect(self._show_investigation)
        self.investigation_list.itemDoubleClicked.connect(self._load_selected_investigation)
        left_layout.addWidget(self.investigation_list, 1)
        left_layout.addWidget(QLabel("Bookmarks"))
        self.bookmark_list = QListWidget()
        self.bookmark_list.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        self.bookmark_list.itemDoubleClicked.connect(self._focus_bookmark)
        left_layout.addWidget(self.bookmark_list, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        compare_header = QLabel("Comparison Mode")
        compare_header.setStyleSheet("font-size: 18px; font-weight: 700; color: #e5e7eb;")
        right_layout.addWidget(compare_header)
        pick_row = QHBoxLayout()
        self.left_combo = QComboBox()
        self.right_combo = QComboBox()
        self.compare_button = QPushButton("Compare")
        self.compare_button.clicked.connect(self.compare_entities)
        self.compare_graph_button = QPushButton("Diff With Live Graph")
        self.compare_graph_button.clicked.connect(self.compare_selected_investigation_to_current)
        self.save_comparison_button = QPushButton("Save Comparison")
        self.save_comparison_button.clicked.connect(self.save_comparison)
        self.sync_button = QPushButton("Sync To Graph")
        self.sync_button.clicked.connect(self.sync_selected_investigation_to_graph)
        pick_row.addWidget(QLabel("Left"))
        pick_row.addWidget(self.left_combo, 1)
        pick_row.addWidget(QLabel("Right"))
        pick_row.addWidget(self.right_combo, 1)
        pick_row.addWidget(self.compare_button)
        pick_row.addWidget(self.compare_graph_button)
        pick_row.addWidget(self.save_comparison_button)
        pick_row.addWidget(self.sync_button)
        right_layout.addLayout(pick_row)
        self.comparison_summary = QTextEdit()
        self.comparison_summary.setReadOnly(True)
        self.comparison_summary.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        right_layout.addWidget(self.comparison_summary, 1)
        self.shared_neighbors = QListWidget()
        self.shared_neighbors.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        right_layout.addWidget(QLabel("Shared Neighborhood"))
        right_layout.addWidget(self.shared_neighbors, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([500, 900])
        layout.addWidget(split, 1)
        self.reload()

    def set_selected_node(self, node) -> None:
        self._selected_node = node
        if node is None:
            self.selected_label.setText("Selected: none")
        else:
            self.selected_label.setText(f"Selected: {node.label} ({node.kind})")

    def reload(self) -> None:
        query = self.search.text().strip() if hasattr(self, "search") else ""
        self.investigation_list.clear()
        rows = self.db.search_investigations(query) if query else self.db.list_investigations()
        for row in rows:
            tags = row.get("tags", "").strip()
            tag_suffix = f" | tags: {tags}" if tags else ""
            item = QListWidgetItem(f"{row['title']} | {row['query']} | {row['created_at']}{tag_suffix}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.investigation_list.addItem(item)
        self.bookmark_list.clear()
        for row in self.db.list_bookmarks():
            item = QListWidgetItem(f"{row['label']} ({row['kind']}) | {row['note']}")
            item.setData(Qt.ItemDataRole.UserRole, row["node_id"])
            self.bookmark_list.addItem(item)

        current_selection = self._selected_node or self.get_selected_node()
        nodes = self.db.list_nodes()
        self.left_combo.clear()
        self.right_combo.clear()
        for node in nodes:
            label = f"{node['label']} [{node['kind']}]"
            self.left_combo.addItem(label, node["id"])
            self.right_combo.addItem(label, node["id"])
        if current_selection:
            self.set_selected_node(current_selection)
            index = self.left_combo.findData(current_selection.id)
            if index >= 0:
                self.left_combo.setCurrentIndex(index)

    def _current_node_dict(self):
        node = self._selected_node or self.get_selected_node()
        if not node:
            return None
        return {"id": node.id, "label": node.label, "kind": node.kind, "metadata": node.metadata}

    def _selected_investigation_id(self) -> int | None:
        item = self.investigation_list.currentItem()
        if not item:
            return None
        investigation_id = item.data(Qt.ItemDataRole.UserRole)
        return int(investigation_id) if investigation_id is not None else None

    def _selected_bookmark_id(self) -> str | None:
        item = self.bookmark_list.currentItem()
        if not item:
            return None
        node_id = item.data(Qt.ItemDataRole.UserRole)
        return str(node_id) if node_id is not None else None

    def save_investigation(self) -> None:
        selected = self._current_node_dict()
        title = selected["label"] if selected else "Investigation"
        query = self.get_current_query() or (selected["label"] if selected else "Manual investigation")
        graph = self.db.export_graph()
        payload = {
            "graph": {
                "nodes": graph.get("nodes", []),
                "edges": graph.get("edges", []),
                "stats": graph.get("stats", {}),
                "bookmarks": graph.get("bookmarks", []),
            },
            "selected_node": selected,
            "saved_at": datetime.now().isoformat(),
        }
        saved_title, ok = QInputDialog.getText(self, "Save Investigation", "Investigation title:", text=title)
        if not ok:
            return
        if saved_title.strip():
            title = saved_title.strip()
        notes, ok = QInputDialog.getText(
            self,
            "Save Investigation",
            "Notes:",
            text="Saved from the EchoMap workspace.",
        )
        if not ok:
            return
        tags, ok = QInputDialog.getText(
            self,
            "Save Investigation",
            "Tags (comma-separated):",
            text="ecosystem,analysis",
        )
        if not ok:
            return
        notes_text = notes.strip()
        payload["notes"] = notes_text
        self.db.save_investigation(title, query, selected["id"] if selected else None, notes_text, payload, tags.strip())
        self.reload()

    def load_investigation(self) -> None:
        investigation_id = self._selected_investigation_id()
        investigation = self.db.get_investigation(investigation_id) if investigation_id is not None else None
        if not investigation:
            return
        payload = investigation["payload"]
        graph = payload.get("graph", {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if nodes:
            from ..models import Edge, Node

            self.db.upsert_nodes(
                [Node(id=n["id"], label=n["label"], kind=n["kind"], metadata=n.get("metadata", {}), created_at=n.get("created_at", "")) for n in nodes]
            )
        if edges:
            from ..models import Edge

            self.db.upsert_edges(
                [
                    Edge(
                        id=e["id"],
                        source=e["source"],
                        target=e["target"],
                        relation=e["relation"],
                        confidence=float(e.get("confidence", 1.0)),
                        metadata=e.get("metadata", {}),
                        created_at=e.get("created_at", ""),
                    )
                    for e in edges
                ]
            )
        selected = payload.get("selected_node")
        if selected and self.focus_node_callback:
            self.focus_node_callback(selected["id"])
        self.reload()

    def _load_selected_investigation(self) -> None:
        self.load_investigation()

    def compare_selected_investigation_to_current(self) -> None:
        investigation_id = self._selected_investigation_id()
        investigation = self.db.get_investigation(investigation_id) if investigation_id is not None else None
        if not investigation:
            return
        saved_graph = investigation["payload"].get("graph", {})
        current_graph = self.db.export_graph()
        result = compare_graphs(saved_graph, current_graph)
        saved_nodes = {node["id"]: node for node in saved_graph.get("nodes", [])}
        current_nodes = {node["id"]: node for node in current_graph.get("nodes", [])}
        self.comparison_summary.setPlainText(
            result.summary
            + "\n\n"
            + json.dumps(
                {
                    "investigation": investigation["title"],
                    "saved_nodes": result.left_node_count,
                    "current_nodes": result.right_node_count,
                    "shared_nodes": [saved_nodes[node_id]["label"] for node_id in result.shared_node_ids if node_id in saved_nodes],
                    "only_in_saved": [saved_nodes[node_id]["label"] for node_id in result.left_only_node_ids if node_id in saved_nodes],
                    "only_in_current": [current_nodes[node_id]["label"] for node_id in result.right_only_node_ids if node_id in current_nodes],
                    "shared_edges": len(result.shared_edge_ids),
                    "overlap_score": result.overlap_score,
                },
                indent=2,
            )
        )
        self.shared_neighbors.clear()
        for node_id in result.shared_node_ids[:12]:
            label = saved_nodes.get(node_id, current_nodes.get(node_id, {})).get("label", node_id)
            self.shared_neighbors.addItem(f"Shared node: {label}")

    def edit_investigation(self) -> None:
        investigation_id = self._selected_investigation_id()
        if investigation_id is None:
            return
        investigation = self.db.get_investigation(investigation_id)
        if not investigation:
            return
        title, ok = QInputDialog.getText(self, "Edit Investigation", "Investigation title:", text=investigation["title"])
        if not ok or not title.strip():
            return
        query, ok = QInputDialog.getText(self, "Edit Investigation", "Discovery query:", text=investigation["query"])
        if not ok or not query.strip():
            return
        notes, ok = QInputDialog.getMultiLineText(self, "Edit Investigation", "Notes:", text=investigation["notes"])
        if not ok:
            return
        tags, ok = QInputDialog.getText(self, "Edit Investigation", "Tags (comma-separated):", text=investigation.get("tags", ""))
        if not ok:
            return
        self.db.update_investigation(
            investigation_id,
            title.strip(),
            query.strip(),
            investigation.get("selected_node_id"),
            notes.strip(),
            investigation["payload"],
            tags.strip(),
        )
        self.reload()

    def delete_investigation(self) -> None:
        investigation_id = self._selected_investigation_id()
        if investigation_id is None:
            return
        investigation = self.db.get_investigation(investigation_id)
        if not investigation:
            return
        response = QMessageBox.question(
            self,
            "Delete Investigation",
            f"Delete '{investigation['title']}'?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_investigation(investigation_id)
        self.reload()

    def export_investigation(self) -> None:
        investigation_id = self._selected_investigation_id()
        if investigation_id is None:
            return
        investigation = self.db.get_investigation(investigation_id)
        if not investigation:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Investigation", f"{investigation['title']}.json", "JSON Files (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(investigation, indent=2), encoding="utf-8")

    def bookmark_selected(self) -> None:
        node = self._current_node_dict()
        if not node:
            return
        note, ok = QInputDialog.getText(self, "Bookmark Selected", "Bookmark note:", text="Bookmarked from the graph workspace")
        if not ok:
            return
        self.db.bookmark_node(node, note=note.strip())
        self.reload()

    def remove_bookmark(self) -> None:
        node_id = self._selected_bookmark_id() or (self._current_node_dict() or {}).get("id")
        if not node_id:
            return
        bookmark = self.db.get_bookmark(node_id)
        if bookmark is None:
            return
        response = QMessageBox.question(
            self,
            "Remove Bookmark",
            f"Remove bookmark for '{bookmark['label']}'?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self.db.remove_bookmark(node_id)
        self.reload()

    def edit_bookmark_note(self) -> None:
        node_id = self._selected_bookmark_id()
        if not node_id:
            return
        bookmark = self.db.get_bookmark(node_id)
        if not bookmark:
            return
        note, ok = QInputDialog.getMultiLineText(self, "Edit Bookmark Note", "Note:", text=bookmark["note"])
        if not ok:
            return
        self.db.update_bookmark_note(node_id, note.strip())
        self.reload()

    def _show_investigation(self, item) -> None:
        investigation_id = item.data(Qt.ItemDataRole.UserRole)
        row = self.db.get_investigation(int(investigation_id)) if investigation_id is not None else None
        if not row:
            return
        self.comparison_summary.setPlainText(json.dumps(row["payload"], indent=2))

    def _focus_bookmark(self, item) -> None:
        node_id = item.data(Qt.ItemDataRole.UserRole)
        bookmark = self.db.get_bookmark(str(node_id)) if node_id is not None else None
        if not bookmark:
            return
        if self.focus_node_callback:
            self.focus_node_callback(bookmark["node_id"])

    def compare_entities(self) -> None:
        left_id = self.left_combo.currentData()
        right_id = self.right_combo.currentData()
        if not left_id or not right_id or left_id == right_id:
            return
        left = self.db.get_node(left_id)
        right = self.db.get_node(right_id)
        if not left or not right:
            return
        left_neighbors = self.db.neighbors(left_id)
        right_neighbors = self.db.neighbors(right_id)
        result = compare_nodes(left, right, left_neighbors, right_neighbors)
        self.comparison_summary.setPlainText(result.summary + "\n\n" + json.dumps(
            {
                "score": result.score,
                "shared_relations": result.shared_relations,
                "left": left,
                "right": right,
            },
            indent=2,
        ))
        self.shared_neighbors.clear()
        for row in result.shared_neighbors:
            self.shared_neighbors.addItem(row["label"] or row["id"])
        self._last_comparison = result

    def save_comparison(self) -> None:
        result = getattr(self, "_last_comparison", None)
        left_id = self.left_combo.currentData()
        right_id = self.right_combo.currentData()
        if not result or not left_id or not right_id:
            self.compare_entities()
            result = getattr(self, "_last_comparison", None)
        if not result:
            return
        self.db.save_comparison(left_id, right_id, {
            "summary": result.summary,
            "score": result.score,
            "shared_relations": result.shared_relations,
            "shared_neighbors": result.shared_neighbors,
        })
        self.reload()

    def sync_selected_investigation_to_graph(self) -> None:
        item = self.investigation_list.currentItem()
        if not item:
            return
        investigation_id = item.data(Qt.ItemDataRole.UserRole)
        investigation = self.db.get_investigation(int(investigation_id)) if investigation_id is not None else None
        if not investigation:
            return
        payload = investigation["payload"]
        graph = payload.get("graph", {})
        from ..models import Edge, Node

        nodes = [
            Node(id=n["id"], label=n["label"], kind=n["kind"], metadata=n.get("metadata", {}), created_at=n.get("created_at", ""))
            for n in graph.get("nodes", [])
        ]
        edges = [
            Edge(
                id=e["id"],
                source=e["source"],
                target=e["target"],
                relation=e["relation"],
                confidence=float(e.get("confidence", 1.0)),
                metadata=e.get("metadata", {}),
                created_at=e.get("created_at", ""),
            )
            for e in graph.get("edges", [])
        ]
        if nodes:
            self.db.upsert_nodes(nodes)
        if edges:
            self.db.upsert_edges(edges)
        selected = payload.get("selected_node")
        if selected and self.focus_node_callback:
            self.focus_node_callback(selected["id"])
        self.reload()
