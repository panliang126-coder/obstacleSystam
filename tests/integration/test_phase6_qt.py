import pytest
from PyQt6.QtCore import Qt

from low_altitude_ai.app.gui import main as gui_main
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.management import ConfigSnapshot, PluginRecord
from low_altitude_ai.ui import UiStateStore
from low_altitude_ai.ui.qt import MainWindow, SnapshotBridge


@pytest.mark.integration
def test_qt_window_distinguishes_live_and_disconnected_state(
    qtbot: object,
    risk_event: Envelope,
) -> None:
    store = UiStateStore(mode=RuntimeMode.SIM)
    store.begin_sync()
    store.apply(risk_event)
    store.complete_sync(1)
    window = MainWindow()
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    bridge = SnapshotBridge(window)
    bridge.publish(store.snapshot(risk_event.received_at))
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.connection_label.text() == "已连接",
        timeout=1_000,
    )

    assert window.windowTitle() == "低空智能感知与自主决策系统"
    assert window.connection_label.text() == "已连接"
    assert window.mode_label.text() == "模式: 仿真"
    assert window.risk_label.text() == "风险: 严重"
    assert window.command_button.isEnabled()
    assert window.entity_model.rowCount() == 1
    assert window.tabs.count() == 6
    assert window.tabs.tabText(0) == "总览"
    assert window.tabs.tabText(2) == "风险与决策"
    assert window.tabs.tabText(5) == "系统管理"
    assert window.entity_model.headerData(
        0,
        Qt.Orientation.Horizontal,
    ) == "类型"
    assert window.entity_model.data(window.entity_model.index(0, 0)) == "风险"
    assert window.entity_model.data(window.entity_model.index(0, 2)) == "严重"
    assert window.command_button.text() == "下发指令"
    assert window.ack_button.text() == "确认告警"
    window.update_management(
        (
            ConfigSnapshot(
                namespace="site.alpha",
                revision=1,
                state="ACTIVE",
                values={"refresh_hz": 10},
                digest="sha256:" + "1" * 64,
                approvals=("approver",),
            ),
        ),
        (
            PluginRecord(
                name="baseline-risk",
                version="1.0.0",
                artifact_hash="sha256:" + "2" * 64,
                state="SHADOW",
                input_schema="twin.snapshot/1.0",
                output_schema="risk/1.0",
            ),
        ),
    )
    assert window.management_table.rowCount() == 2

    with qtbot.waitSignal(  # type: ignore[attr-defined]
        window.alertAcknowledgementRequested,
        timeout=1_000,
    ):
        qtbot.mouseClick(  # type: ignore[attr-defined]
            window.ack_button,
            Qt.MouseButton.LeftButton,
        )

    store.disconnect()
    window.update_snapshot(store.snapshot(risk_event.received_at))
    assert window.connection_label.text() == "未连接"
    assert not window.command_button.isEnabled()


@pytest.mark.integration
def test_gui_offscreen_entrypoint_renders_and_exits(qtbot: object) -> None:
    del qtbot
    assert gui_main(["--offscreen", "--smoke-test"]) == 0
