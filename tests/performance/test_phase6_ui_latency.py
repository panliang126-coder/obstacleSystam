import statistics
import time
from dataclasses import replace

import pytest

from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.ui import UiStateStore
from low_altitude_ai.ui.qt import EntityTableModel


@pytest.mark.performance
def test_phase6_1000_track_qt_model_update_p95_is_within_frame_budget(
    qtbot: object,
    risk_event: Envelope,
) -> None:
    del qtbot
    targets = [
        {
            "track_id": f"track-{index:04d}",
            "track_state": "CONFIRMED",
            "position_enu_m": [float(index), 0.0, 20.0],
        }
        for index in range(1_000)
    ]
    events = [
        replace(
            risk_event,
            schema="target/1.0",
            sequence=sequence,
            payload={"targets": targets},
        )
        for sequence in range(30)
    ]
    store = UiStateStore(mode=RuntimeMode.SIM, max_entities=1_000)
    store.begin_sync()
    model = EntityTableModel()
    latencies_ms: list[float] = []
    for event in events:
        started = time.perf_counter_ns()
        assert store.apply(event)
        model.set_snapshot(store.snapshot(risk_event.received_at))
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    p95_ms = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]

    assert model.rowCount() == 1_000
    assert p95_ms <= 16.0
