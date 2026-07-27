import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from low_altitude_ai.adapters.event_bus import InMemoryEventBus
from low_altitude_ai.domain import Envelope, RuntimeMode
from low_altitude_ai.planning import PlannerConfig, PlanRequest, RuleBasedPlannerPlugin
from low_altitude_ai.ports.plugins import PluginContext
from low_altitude_ai.simulator import DeterministicUuid7Factory, RandomStream, SimClock


def test_planner_rejects_expired_risk_and_reports_infeasible_path(
    risk_event: Envelope,
) -> None:
    async def exercise() -> Envelope:
        clock = SimClock(risk_event.received_at)
        bus = InMemoryEventBus()
        plugin = RuleBasedPlannerPlugin(
            config=PlannerConfig(
                geofence_min_enu_m=(0.0, -5.0, 0.0),
                geofence_max_enu_m=(100.0, 5.0, 100.0),
            ),
            event_ids=DeterministicUuid7Factory(RandomStream(8, "planner")),
        )
        await plugin.initialize(
            PluginContext(
                run_id=str(risk_event.run_id),
                mode=RuntimeMode.SIM,
                clock=clock,
                event_bus=bus,
                config={},
            )
        )
        request = PlanRequest(
            mission_id="test",
            vehicle_id="uav-001",
            twin_revision=int(risk_event.payload["twin_revision"]),
            start_enu_m=(0.0, 0.0, 20.0),
            goal_enu_m=(100.0, 0.0, 20.0),
            frame_id="site-alpha-enu-v1",
            risk=risk_event,
            deadline=clock.now() + timedelta(seconds=1),
        )
        rejected = await plugin.plan(request)
        expired_payload = dict(risk_event.payload)
        expired_payload["valid_until"] = (
            clock.now() - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        expired = replace(risk_event, payload=expired_payload)
        with pytest.raises(ValueError, match="risk input has expired"):
            await plugin.plan(replace(request, risk=expired))
        await plugin.shutdown(1.0)
        await bus.close()
        return rejected

    rejected = asyncio.run(exercise())

    assert rejected.payload["status"] == "REJECTED"
    assert not rejected.quality.valid
    assert "NO_FEASIBLE_PATH" in rejected.quality.flags
    assert not rejected.payload["validation"]["collision_free"]
