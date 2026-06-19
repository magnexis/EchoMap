from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
)


NODE_COLORS = {
    "Website": "#5eead4",
    "Domain": "#60a5fa",
    "Repository": "#f59e0b",
    "GitHub Organization": "#c084fc",
    "Technology": "#34d399",
    "Person": "#f472b6",
    "Company": "#f97316",
    "Organization": "#a78bfa",
    "Seed": "#94a3b8",
}


@dataclass(slots=True)
class GraphNode:
    id: str
    label: str
    kind: str
    metadata: dict


@dataclass(slots=True)
class GraphEdge:
    id: str
    source: str
    target: str
    relation: str
    confidence: float


class NodeItem(QGraphicsObject):
    clicked = Signal(object)
    activated = Signal(object)
    moved = Signal()

    def __init__(self, node: GraphNode, radius: float = 26.0) -> None:
        super().__init__()
        self.node = node
        self.radius = radius
        self._bounds = QRectF(-radius - 12, -radius - 12, radius * 2 + 24, radius * 2 + 40)
        self._path_highlighted = False
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(2)

    def boundingRect(self) -> QRectF:  # pragma: no cover - Qt painting hook
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # pragma: no cover - Qt painting hook
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base = QColor(NODE_COLORS.get(self.node.kind, "#94a3b8"))
        fill = base.lighter(135 if self.isSelected() or self._path_highlighted else 100)
        outline = QColor("#f8fafc" if self.isSelected() else ("#f59e0b" if self._path_highlighted else "#0f172a"))
        glow = QColor(base)
        glow.setAlpha(110 if self.isSelected() else 70 if self._path_highlighted else 35)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(0, 0), self.radius + 6, self.radius + 6)
        painter.setBrush(fill)
        painter.setPen(QPen(outline, 2.5 if self.isSelected() else 1.6))
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e5e7eb"))
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self.node.label)
        text_rect = QRectF(-width / 2 - 8, self.radius + 6, width + 16, 18)
        painter.setBrush(QColor(2, 6, 23, 200))
        painter.setPen(QColor(148, 163, 184, 120))
        painter.drawRoundedRect(text_rect, 8, 8)
        painter.setPen(QColor("#e5e7eb"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.node.label)

    def mousePressEvent(self, event) -> None:  # pragma: no cover - Qt callback
        self.clicked.emit(self.node)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # pragma: no cover - Qt callback
        self.activated.emit(self.node)
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change, value):  # pragma: no cover - Qt callback
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit()
        return super().itemChange(change, value)


class GraphView(QGraphicsView):
    nodeClicked = Signal(object)
    nodeActivated = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#020617"))
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[QGraphicsLineItem] = []
        self.edges: list[GraphEdge] = []
        self._path_nodes: set[str] = set()
        self._path_edges: set[str] = set()
        self._physics_timer = QTimer(self)
        self._physics_timer.setInterval(20)
        self._physics_timer.timeout.connect(self._tick_physics)
        self._layout_active = False
        self._selected_id: str | None = None
        self._selected_edge_id: str | None = None

    def clear_graph(self) -> None:
        self.scene.clear()
        self.node_items.clear()
        self.edge_items.clear()
        self.edges = []
        self._path_nodes = set()
        self._path_edges = set()
        self._layout_active = False
        self._selected_id = None
        self._selected_edge_id = None
        self._physics_timer.stop()

    def set_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        self.clear_graph()
        graph_nodes = [GraphNode(n["id"], n["label"], n["kind"], n.get("metadata", {})) for n in nodes]
        self.edges = [
            GraphEdge(
                e.get("id", f'{e["source"]}->{e["target"]}:{e["relation"]}'),
                e["source"],
                e["target"],
                e["relation"],
                float(e.get("confidence", 1.0)),
            )
            for e in edges
        ]

        if not graph_nodes:
            self.scene.addText("No graph data yet").setDefaultTextColor(QColor("#cbd5e1"))
            return

        grouped: dict[str, list[GraphNode]] = {}
        for node in graph_nodes:
            grouped.setdefault(node.kind, []).append(node)
        kinds = list(grouped.keys())
        cluster_radius = 340 if len(kinds) > 1 else 0
        node_radius = 120 + (12 * max(0, len(graph_nodes) - 1))
        positions: dict[str, QPointF] = {}
        if len(kinds) == 1:
            for index, node in enumerate(graph_nodes):
                angle = (2 * math.pi * index) / max(1, len(graph_nodes))
                positions[node.id] = QPointF(math.cos(angle) * node_radius, math.sin(angle) * node_radius)
        else:
            for kind_index, kind in enumerate(kinds):
                cluster_angle = (2 * math.pi * kind_index) / max(1, len(kinds))
                cluster_center = QPointF(math.cos(cluster_angle) * cluster_radius, math.sin(cluster_angle) * cluster_radius)
                nodes_for_kind = grouped[kind]
                inner_radius = 60 + (18 * len(nodes_for_kind))
                for node_index, node in enumerate(nodes_for_kind):
                    angle = (2 * math.pi * node_index) / max(1, len(nodes_for_kind))
                    offset = QPointF(math.cos(angle) * inner_radius, math.sin(angle) * inner_radius)
                    positions[node.id] = cluster_center + offset

        for node in graph_nodes:
            item = NodeItem(node)
            item.setPos(positions.get(node.id, QPointF(0, 0)))
            item.clicked.connect(self._handle_click)
            item.activated.connect(self._handle_activate)
            item.moved.connect(self._rebuild_lines)
            self.scene.addItem(item)
            self.node_items[node.id] = item

        for edge in self.edges:
            source = self.node_items.get(edge.source)
            target = self.node_items.get(edge.target)
            if not source or not target:
                continue
            line = QGraphicsLineItem()
            pen = QPen(QColor(148, 163, 184, int(80 + edge.confidence * 120)))
            pen.setWidthF(1.4)
            line.setPen(pen)
            line.setZValue(1)
            self.scene.addItem(line)
            self.edge_items.append(line)

        self._rebuild_lines()
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-120, -120, 120, 120))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        if len(self.node_items) > 1:
            self._layout_active = True
            self._physics_timer.start()

    def select_node(self, node_id: str | None) -> None:
        self._selected_id = node_id
        for item_id, item in self.node_items.items():
            item.setSelected(item_id == node_id)
        self._rebuild_lines()

    def select_edge(self, edge_id: str | None) -> None:
        self._selected_edge_id = edge_id
        self._rebuild_lines()

    def set_path_highlight(self, node_ids: list[str] | set[str], edge_ids: list[str] | set[str]) -> None:
        self._path_nodes = set(node_ids)
        self._path_edges = set(edge_ids)
        for item_id, item in self.node_items.items():
            item._path_highlighted = item_id in self._path_nodes
            item.update()
        self._rebuild_lines()

    def _handle_click(self, node: GraphNode) -> None:
        self.select_node(node.id)
        self.nodeClicked.emit(node)

    def _handle_activate(self, node: GraphNode) -> None:
        self.select_node(node.id)
        self.nodeActivated.emit(node)

    def _rebuild_lines(self) -> None:
        for edge, line in zip(self.edges, self.edge_items):
            source = self.node_items.get(edge.source)
            target = self.node_items.get(edge.target)
            if not source or not target:
                continue
            line.setLine(source.scenePos().x(), source.scenePos().y(), target.scenePos().x(), target.scenePos().y())
            if edge.id == self._selected_edge_id:
                pen = QPen(QColor("#38bdf8"))
                pen.setWidthF(3.4)
                line.setPen(pen)
            elif edge.id in self._path_edges:
                pen = QPen(QColor("#f472b6"))
                pen.setWidthF(3.2)
                line.setPen(pen)
            elif self._selected_id and self._selected_id in {edge.source, edge.target}:
                pen = QPen(QColor("#f59e0b"))
                pen.setWidthF(2.4)
                line.setPen(pen)
            else:
                pen = QPen(QColor(148, 163, 184, int(80 + edge.confidence * 120)))
                pen.setWidthF(1.4)
                line.setPen(pen)

    def _tick_physics(self) -> None:
        if not self._layout_active:
            self._physics_timer.stop()
            return
        items = list(self.node_items.values())
        if len(items) < 2:
            self._physics_timer.stop()
            return
        positions = {item: QPointF(item.pos()) for item in items}
        forces = {item: QPointF(0, 0) for item in items}
        repulsion = 12000.0
        spring = 0.02
        ideal = 170.0
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                delta = positions[a] - positions[b]
                dist = max(15.0, math.hypot(delta.x(), delta.y()))
                magnitude = repulsion / (dist * dist)
                fx = delta.x() / dist * magnitude
                fy = delta.y() / dist * magnitude
                forces[a] += QPointF(fx, fy)
                forces[b] -= QPointF(fx, fy)
        for edge in self.edges:
            a = self.node_items.get(edge.source)
            b = self.node_items.get(edge.target)
            if not a or not b:
                continue
            delta = positions[b] - positions[a]
            dist = max(15.0, math.hypot(delta.x(), delta.y()))
            magnitude = spring * (dist - ideal)
            fx = delta.x() / dist * magnitude
            fy = delta.y() / dist * magnitude
            forces[a] += QPointF(fx, fy)
            forces[b] -= QPointF(fx, fy)
        moved = 0.0
        for item in items:
            force = forces[item]
            pos = positions[item]
            new_pos = QPointF(pos.x() + force.x() * 0.01, pos.y() + force.y() * 0.01)
            moved += abs(force.x()) + abs(force.y())
            item.setPos(new_pos)
        self._rebuild_lines()
        if moved < 2.0:
            self._layout_active = False
            self._physics_timer.stop()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 0.87
        self.scale(factor, factor)
