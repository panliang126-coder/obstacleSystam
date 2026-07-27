import statistics
import time
from dataclasses import replace

import pytest

from low_altitude_ai.decision import DecisionCenter, DecisionConfig, DecisionContext
from low_altitude_ai.domain import Envelope
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


@pytest.mark.performance
def test_phase5_decision_hot_path_p95_is_within_budget(
    risk_event: Envelope,
    path_event: Envelope,
    vehicle_state_event: Envelope,
) -> None:
    clock = SimClock(risk_event.received_at)
    center = DecisionCenter(
        config=DecisionConfig(),
        clock=clock,
        event_ids=DeterministicUuid7Factory(RandomStream(14, "decision-performance")),
    )
    contexts: list[DecisionContext] = []
    for _ in range(200):
        risk_id = str(uuid7())
        risk_payload = dict(risk_event.payload)
        risk_payload["risk_id"] = risk_id
        path_payload = dict(path_event.payload)
        path_payload["path_id"] = str(uuid7())
        path_payload["risk_id"] = risk_id
        contexts.append(
            DecisionContext(
                mission_id="performance-test",
                risk=replace(risk_event, payload=risk_payload),
                path=replace(path_event, payload=path_payload),
                vehicle_state=vehicle_state_event,
            )
        )

    center.decide(contexts[0])
    latencies_ms: list[float] = []
    for context in contexts[1:]:
        started = time.perf_counter_ns()
        center.decide(context)
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)

    p95_ms = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]
    assert p95_ms <= 20.0
