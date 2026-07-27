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
        lambda: window.connection_label.text() == "LIVE",
        timeout=1_000,
    )

    assert window.connection_label.text() == "LIVE"
    assert window.risk_label.text() == "Risk: CRITICAL"
    assert window.command_button.isEnabled()
    assert window.entity_model.rowCount() == 1
    assert window.tabs.count() == 6
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
    assert window.connection_label.text() == "DISCONNECTED"
    assert not window.command_button.isEnabled()


@pytest.mark.integration
def test_gui_offscreen_entrypoint_renders_and_exits(qtbot: object) -> None:
    del qtbot
    assert gui_main(["--offscreen", "--smoke-test"]) == 0
