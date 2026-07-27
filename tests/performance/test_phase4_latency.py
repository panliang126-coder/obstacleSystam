import asyncio
import statistics
import time
from datetime import timedelta

import pytest

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.planning import PlannerConfig, PlanRequest, RuleBasedPlannerPlugin
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


@pytest.mark.performance
def test_phase4_local_planner_hot_path_p95_is_within_replanning_budget(
    risk_event: Envelope,
) -> None:
    async def benchmark() -> list[float]:
        clock = SimClock(risk_event.received_at)
        bus = InMemoryEventBus()
        planner = RuleBasedPlannerPlugin(
            config=PlannerConfig(),
            event_ids=DeterministicUuid7Factory(RandomStream(9, "planner-performance")),
        )
        await planner.initialize(
            PluginContext(
                run_id=str(risk_event.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        request = PlanRequest(
            mission_id="performance-test",
            vehicle_id="uav-001",
            twin_revision=int(risk_event.payload["twin_revision"]),
            start_enu_m=(0.0, 0.0, 20.0),
            goal_enu_m=(100.0, 0.0, 20.0),
            frame_id="site-alpha-enu-v1",
            risk=risk_event,
            deadline=clock.now() + timedelta(seconds=1),
        )

        await planner.plan(request)
        latencies_ms: list[float] = []
        for _ in range(100):
            started = time.perf_counter_ns()
            await planner.plan(request)
            latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)

        await planner.shutdown(1.0)
        await bus.close()
        return latencies_ms

    latencies_ms = asyncio.run(benchmark())
    p95_ms = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]
    assert p95_ms <= 100.0
