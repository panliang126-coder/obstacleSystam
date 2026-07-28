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

_SCHEMA_LABELS = {
    "control.ack/1.0": "控制确认",
    "decision/1.0": "决策",
    "environment/1.0": "环境",
    "health/1.0": "系统健康",
    "path/1.0": "规划路径",
    "risk/1.0": "风险",
    "sensor/1.0": "传感器",
    "target/1.0": "目标航迹",
    "twin.snapshot/1.0": "数字孪生快照",
    "vehicle.state/1.0": "飞行器状态",
}
_CONNECTION_LABELS = {
    "CONNECTING": "正在连接",
    "SYNCING": "正在同步",
    "LIVE": "已连接",
    "REPLAY": "回放中",
    "DEGRADED": "降级运行",
    "DISCONNECTED": "未连接",
}
_MODE_LABELS = {
    "SIM": "仿真",
    "REPLAY": "回放",
    "HIL": "硬件在环",
    "LIVE": "实机",
}
_STATUS_LABELS = {
    "ACCEPTED": "已接受",
    "ACTIVE": "已启用",
    "AIRBORNE": "飞行中",
    "APPROVED": "已批准",
    "AUTHORIZED": "已授权",
    "AVOID": "避让",
    "CANDIDATE": "候选",
    "COASTING": "暂时丢失",
    "COMPLETED": "已完成",
    "CONFIRMED": "已确认",
    "CONTINUE": "继续",
    "CRITICAL": "严重",
    "DEGRADED": "降级",
    "DISCONNECTED": "未连接",
    "DRAFT": "草稿",
    "FAILED": "失败",
    "HEALTHY": "健康",
    "HIGH": "高",
    "HOLD": "悬停",
    "INFO": "提示",
    "INVALID": "无效",
    "LAND": "降落",
    "LOST": "已丢失",
    "LOW": "低",
    "MODERATE": "中等",
    "NONE": "无",
    "PENDING": "待处理",
    "READY": "就绪",
    "REJECTED": "已拒绝",
    "RETURN": "返航",
    "ROLLED_BACK": "已回滚",
    "SCHEMA_VALIDATED": "结构已验证",
    "SHADOW": "影子运行",
    "SUCCEEDED": "成功",
    "TENTATIVE": "待确认",
    "UNKNOWN": "未知",
    "UNHEALTHY": "异常",
    "VALID": "有效",
    "VALIDATED": "已验证",
    "WARNING": "警告",
}
_TAB_LABELS = {
    "Overview": "总览",
    "Situation": "态势",
    "Risk & Decision": "风险与决策",
    "Health": "健康",
    "Replay": "回放",
}


def _display_schema(schema: str) -> str:
    return _SCHEMA_LABELS.get(schema, schema)


def _display_status(status: str) -> str:
    if status.startswith("REVISION "):
        return f"修订版 {status.removeprefix('REVISION ')}"
    return _STATUS_LABELS.get(status, status)


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
    _HEADERS = ("类型", "实体", "状态", "数据年龄(毫秒)")

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
            _display_schema(entity.schema),
            entity.key,
            _display_status(entity.status),
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
        self.setWindowTitle("低空智能感知与自主决策系统")
        self.resize(1100, 700)
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.connection_label = QLabel("未连接")
        self.mode_label = QLabel("模式: --")
        self.risk_label = QLabel("风险: 无")
        self.freshness_label = QLabel("数据年龄: --")
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
                self.replay_watermark = QLabel("回放模式——实机控制已禁用")
                replay_layout.addWidget(self.replay_watermark)
                replay_layout.addWidget(table)
                self.tabs.addTab(replay_widget, _TAB_LABELS[name])
            else:
                self.tabs.addTab(table, _TAB_LABELS[name])
        self.entity_model = self._models["Overview"]
        self.entity_table = self._tables["Overview"]
        self.management_table = QTableWidget(0, 4)
        self.management_table.setHorizontalHeaderLabels(
            ("类型", "名称", "版本/修订", "状态")
        )
        self.tabs.addTab(self.management_table, "系统管理")
        layout.addWidget(self.tabs)

        footer = QHBoxLayout()
        self.command_button = QPushButton("下发指令")
        self.ack_button = QPushButton("确认告警")
        self.ack_button.clicked.connect(
            lambda: self.alertAcknowledgementRequested.emit("selected-alert")
        )
        footer.addWidget(self.command_button)
        footer.addWidget(self.ack_button)
        footer.addStretch(1)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def update_snapshot(self, snapshot: UiSnapshot) -> None:
        self.connection_label.setText(
            _CONNECTION_LABELS.get(snapshot.connection.value, snapshot.connection.value)
        )
        self.mode_label.setText(
            f"模式: {_MODE_LABELS.get(snapshot.mode.value, snapshot.mode.value)}"
        )
        self.risk_label.setText(f"风险: {_display_status(snapshot.highest_risk)}")
        maximum_age = max(
            (entity.age_ms for entity in snapshot.entities),
            default=0.0,
        )
        self.freshness_label.setText(f"最大数据年龄: {maximum_age:.1f} 毫秒")
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
            ("配置", item.namespace, str(item.revision), _display_status(item.state))
            for item in configs
        ]
        rows.extend(
            ("插件", item.name, item.version, _display_status(item.state))
            for item in plugins
        )
        self.management_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.management_table.setItem(row, column, QTableWidgetItem(value))
