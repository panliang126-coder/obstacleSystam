"""PyQt6 adapter over immutable UI snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from low_altitude_ai.management import ConfigSnapshot, PluginRecord
from low_altitude_ai.ui.state import EntityView, UiSnapshot


class SnapshotBridge(QObject):
    """Queued signal boundary for worker-produced immutable snapshots."""

    snapshotReceived = pyqtSignal(object)

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        # Qt AutoConnection becomes queued whenever the emitter is on a worker thread.
        self.snapshotReceived.connect(window.update_snapshot)

    def publish(self, snapshot: UiSnapshot) -> None:
        self.snapshotReceived.emit(snapshot)


class EntityTableModel(QAbstractTableModel):
    _HEADERS = ("Type", "Entity", "Status", "Age (ms)")

    def __init__(self) -> None:
        super().__init__()
        self._rows: tuple[EntityView, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._HEADERS)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        entity = self._rows[index.row()]
        values = (
            entity.schema,
            entity.key,
            entity.status,
            f"{entity.age_ms:.1f}",
        )
        return values[index.column()]

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._HEADERS[section]
        return None

    def set_snapshot(self, snapshot: UiSnapshot) -> None:
        self.beginResetModel()
        self._rows = snapshot.entities
        self.endResetModel()


class MainWindow(QMainWindow):
    alertAcknowledgementRequested = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Low Altitude AI System")
        self.resize(1100, 700)
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.connection_label = QLabel("DISCONNECTED")
        self.mode_label = QLabel("Mode: --")
        self.risk_label = QLabel("Risk: NONE")
        self.freshness_label = QLabel("Age: --")
        header.addWidget(self.connection_label)
        header.addWidget(self.mode_label)
        header.addWidget(self.risk_label)
        header.addWidget(self.freshness_label)
        header.addStretch(1)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self._models = {
            "Overview": EntityTableModel(),
            "Situation": EntityTableModel(),
            "Risk & Decision": EntityTableModel(),
            "Health": EntityTableModel(),
            "Replay": EntityTableModel(),
        }
        self._tables: dict[str, QTableView] = {}
        for name, model in self._models.items():
            table = QTableView()
            table.setModel(model)
            self._tables[name] = table
            if name == "Replay":
                replay_widget = QWidget()
                replay_layout = QVBoxLayout(replay_widget)
                self.replay_watermark = QLabel("REPLAY — LIVE controls disabled")
                replay_layout.addWidget(self.replay_watermark)
                replay_layout.addWidget(table)
                self.tabs.addTab(replay_widget, name)
            else:
                self.tabs.addTab(table, name)
        self.entity_model = self._models["Overview"]
        self.entity_table = self._tables["Overview"]
        self.management_table = QTableWidget(0, 4)
        self.management_table.setHorizontalHeaderLabels(
            ("Type", "Name", "Version/Revision", "State")
        )
        self.tabs.addTab(self.management_table, "Management")
        layout.addWidget(self.tabs)

        footer = QHBoxLayout()
        self.command_button = QPushButton("Issue command")
        self.ack_button = QPushButton("Acknowledge alert")
        self.ack_button.clicked.connect(
            lambda: self.alertAcknowledgementRequested.emit("selected-alert")
        )
        footer.addWidget(self.command_button)
        footer.addWidget(self.ack_button)
        footer.addStretch(1)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def update_snapshot(self, snapshot: UiSnapshot) -> None:
        self.connection_label.setText(snapshot.connection.value)
        self.mode_label.setText(f"Mode: {snapshot.mode.value}")
        self.risk_label.setText(f"Risk: {snapshot.highest_risk}")
        maximum_age = max(
            (entity.age_ms for entity in snapshot.entities),
            default=0.0,
        )
        self.freshness_label.setText(f"Max age: {maximum_age:.1f} ms")
        self.command_button.setEnabled(snapshot.commands_enabled)
        self.entity_model.set_snapshot(snapshot)
        filters = {
            "Situation": {
                "environment/1.0",
                "path/1.0",
                "target/1.0",
                "twin.snapshot/1.0",
                "vehicle.state/1.0",
            },
            "Risk & Decision": {
                "control.ack/1.0",
                "decision/1.0",
                "risk/1.0",
            },
            "Health": {"health/1.0"},
            "Replay": {entity.schema for entity in snapshot.entities},
        }
        for name, schemas in filters.items():
            filtered = tuple(
                entity for entity in snapshot.entities if entity.schema in schemas
            )
            self._models[name].set_snapshot(replace(snapshot, entities=filtered))
        self.replay_watermark.setVisible(snapshot.mode.value == "REPLAY")

    def update_management(
        self,
        configs: tuple[ConfigSnapshot, ...],
        plugins: tuple[PluginRecord, ...],
    ) -> None:
        rows = [
            ("Config", item.namespace, str(item.revision), item.state)
            for item in configs
        ]
        rows.extend(
            ("Plugin", item.name, item.version, item.state) for item in plugins
        )
        self.management_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.management_table.setItem(row, column, QTableWidgetItem(value))
