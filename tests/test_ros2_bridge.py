import asyncio
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from aigc_lite_ros2.config import BridgeSettings
from aigc_lite_ros2.contracts import (
    ExecutionEnvironment,
    NavigationRequest,
    PhysicalActionStatus,
    Pose2D,
    RobotState,
)
from aigc_lite_ros2.server import APIKeyASGI, create_server
from aigc_lite_ros2.service import RobotCapabilityService
from aigc_lite_ros2.simulator import SimulatorBackend
from mcp.client.streamable_http import streamable_http_client

from app.adapters.tools.mcp import MCPToolProvider
from app.core.contracts import RequestContext, ToolRisk
from app.services.tool_catalog import ToolCatalog


def test_simulator_produces_receipt_and_rejects_idempotency_conflict() -> None:
    async def run():
        service = RobotCapabilityService(SimulatorBackend(robot_id="sim-1", travel_seconds=0.01))
        arguments = {
            "idempotency_key": "navigation-001",
            "x": 1,
            "y": 2,
            "yaw": 0.5,
            "action_timeout_seconds": 1,
        }
        first = await service.invoke("robot_navigate_to", arguments)
        replay = await service.invoke("robot_navigate_to", arguments)
        conflict = await service.invoke("robot_navigate_to", {**arguments, "x": 99})
        inspect = await service.invoke("robot_inspect", {})
        return first, replay, conflict, inspect

    first, replay, conflict, inspect = asyncio.run(run())
    assert not first.failed
    assert first.payload["contract_version"] == "robot.action-receipt.v1"
    assert first.payload["status"] == "succeeded"
    assert first.payload["environment"] == "simulation"
    assert first.payload["stop_confirmed"] is True
    assert first.payload["observation_after"]["pose"]["x"] == 1.0
    assert replay.payload["action_id"] == first.payload["action_id"]
    assert replay.payload["metadata"]["idempotent_replay"] is True
    assert conflict.failed
    assert conflict.payload["error"]["code"] == "idempotency_conflict"
    assert inspect.payload["last_action"]["action_id"] == first.payload["action_id"]


def test_simulator_cancel_confirms_stop_and_closes_navigation_wait() -> None:
    async def run():
        backend = SimulatorBackend(robot_id="sim-1", travel_seconds=1)
        service = RobotCapabilityService(backend)
        navigation = asyncio.create_task(
            service.invoke(
                "robot_navigate_to",
                {
                    "idempotency_key": "navigation-cancel-001",
                    "x": 5,
                    "y": 0,
                    "yaw": 0,
                    "action_timeout_seconds": 5,
                },
            )
        )
        for _ in range(100):
            state = await backend.get_state()
            if state.active_action_id:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("navigation never became active")
        cancelled = await service.invoke(
            "robot_cancel_action",
            {"idempotency_key": "navigation-cancel-001"},
        )
        navigated = await navigation
        return cancelled, navigated, await backend.get_state()

    cancelled, navigated, state = asyncio.run(run())
    assert not cancelled.failed
    assert cancelled.payload["status"] == "cancelled"
    assert cancelled.payload["stop_confirmed"] is True
    assert navigated.failed
    assert navigated.payload["action_id"] == cancelled.payload["action_id"]
    assert state.navigation_status == "idle"
    assert state.active_action_id is None


def test_caller_cancellation_is_propagated_and_preserves_terminal_receipt() -> None:
    async def run():
        backend = SimulatorBackend(travel_seconds=1)
        service = RobotCapabilityService(backend)
        task = asyncio.create_task(
            service.invoke(
                "robot_navigate_to",
                {
                    "idempotency_key": "navigation-caller-cancel",
                    "x": 1,
                    "y": 0,
                    "yaw": 0,
                },
            )
        )
        while (await backend.get_state()).active_action_id is None:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await backend.latest_receipt()

    receipt = asyncio.run(run())
    assert receipt is not None
    assert receipt.status is PhysicalActionStatus.CANCELLED
    assert receipt.stop_confirmed is True
    assert receipt.error_code == "caller_cancelled"


def test_simulator_preserves_indeterminate_terminal_state() -> None:
    async def run():
        service = RobotCapabilityService(
            SimulatorBackend(travel_seconds=0.01, outcome="indeterminate")
        )
        return await service.invoke(
            "robot_navigate_to",
            {
                "idempotency_key": "navigation-unknown-001",
                "x": 1,
                "y": 1,
                "yaw": 0,
            },
        )

    result = asyncio.run(run())
    assert result.failed
    assert result.payload["status"] == "indeterminate"
    assert result.payload["stop_confirmed"] is False
    assert result.payload["error"]["code"] == "robot_state_indeterminate"


def test_indeterminate_simulator_motion_blocks_until_explicit_reconciliation() -> None:
    async def run():
        backend = SimulatorBackend(travel_seconds=0.01, outcome="indeterminate")
        service = RobotCapabilityService(backend)
        first = await service.invoke(
            "robot_navigate_to",
            {"idempotency_key": "unknown-motion-001", "x": 1, "y": 0, "yaw": 0},
        )
        blocked = await service.invoke(
            "robot_navigate_to",
            {"idempotency_key": "second-motion-001", "x": 2, "y": 0, "yaw": 0},
        )
        uncertain_state = await backend.get_state()
        reconciled = await backend.confirm_stopped(first.payload["action_id"])
        backend.outcome = "success"
        resumed = await service.invoke(
            "robot_navigate_to",
            {"idempotency_key": "second-motion-001", "x": 2, "y": 0, "yaw": 0},
        )
        return first, blocked, uncertain_state, reconciled, resumed

    first, blocked, uncertain_state, reconciled, resumed = asyncio.run(run())
    assert first.payload["stop_confirmed"] is False
    assert blocked.failed and blocked.payload["error"]["code"] == "robot_busy"
    assert uncertain_state.navigation_status == "unknown"
    assert uncertain_state.active_action_id == first.payload["action_id"]
    assert reconciled.stop_confirmed is True
    assert reconciled.metadata["reconciled_by"] == "simulator_operator_control"
    assert resumed.payload["status"] == "succeeded"


def test_bridge_projects_policy_through_remote_mcp_catalog() -> None:
    backend = SimulatorBackend(robot_id="sim-mcp", travel_seconds=0.01)
    settings = BridgeSettings(robot_id="sim-mcp", api_key="bridge-secret")
    server = create_server(RobotCapabilityService(backend), settings)
    streamable_app = APIKeyASGI(
        server.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1"),
        settings.api_key,
    )

    @asynccontextmanager
    async def transport():
        url = "http://127.0.0.1/mcp"
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=streamable_app),
            base_url="http://127.0.0.1",
            headers={
                "host": "127.0.0.1:80",
                "authorization": "Bearer bridge-secret",
            },
        ) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                yield streams

    async def run():
        provider = MCPToolProvider(
            "robot",
            "http://127.0.0.1/mcp",
            workspace_id="workspace-a",
            timeout_seconds=180,
            transport_factory=transport,
        )
        consumed = []

        def authorize(context, spec, arguments):
            consumed.append((context.principal_id, spec.native_name))
            assert arguments["idempotency_key"] == "mcp-navigation-001"
            return {
                "id": "grant-robot-1",
                "qualification_receipt_id": "receipt-robot-1",
                "calls_used": 1,
                "max_calls": 1,
            }

        denied_catalog = ToolCatalog([provider])
        catalog = ToolCatalog([provider], authorization_gate=authorize)
        async with server.session_manager.run():
            ordinary = await catalog.open(
                RequestContext(request_id="r1", workspace_id="workspace-a")
            )
            denied_session = await denied_catalog.open(
                RequestContext(
                    request_id="r-denied",
                    workspace_id="workspace-a",
                    principal_id="robot-operator-a",
                    scopes=frozenset({"tools:high-risk", "robot:motion"}),
                )
            )
            elevated = await catalog.open(
                RequestContext(
                    request_id="r2",
                    workspace_id="workspace-a",
                    principal_id="robot-operator-a",
                    scopes=frozenset({"tools:high-risk", "robot:motion"}),
                )
            )
            arguments = json.dumps(
                {
                    "idempotency_key": "mcp-navigation-001",
                    "x": 2,
                    "y": 3,
                    "yaw": 0,
                }
            )
            denied = await denied_session.invoke(
                "robot__robot_navigate_to",
                arguments,
            )
            result = await elevated.invoke(
                "robot__robot_navigate_to",
                arguments,
            )
        return ordinary, elevated, denied, result, consumed

    ordinary, elevated, denied, result, consumed = asyncio.run(run())
    ordinary_names = {item.name for item in ordinary.specs}
    assert ordinary_names == {"robot__robot_get_state", "robot__robot_inspect"}
    navigate = next(item for item in elevated.specs if item.name == "robot__robot_navigate_to")
    assert navigate.risk is ToolRisk.HIGH
    assert navigate.required_scopes == frozenset({"robot:motion"})
    assert navigate.timeout_seconds == 150
    assert navigate.extensions["capability"]["effect_class"] == "physical_motion"
    assert navigate.extensions["authority_requirement"] == {
        "required": True,
        "action": "robot_navigate_to",
        "target": "robot:sim-mcp",
    }
    assert "robot__robot_cancel_action" not in {item.name for item in elevated.specs}
    assert denied.failed
    assert json.loads(denied.content) == {"error": "tool_authorization_required"}
    assert not result.failed
    assert consumed == [("robot-operator-a", "robot_navigate_to")]
    assert result.metadata["authorization"]["grant_id"] == "grant-robot-1"
    assert result.metadata["authorization"]["status"] == "consumed"
    assert result.metadata["authorization"]["target"] == "provider:robot/robot:sim-mcp"
    assert json.loads(result.content)["status"] == "succeeded"
    assert result.metadata["extensions"]["capability"]["physical_risk"] == "high"
    assert result.artifacts[0].metadata["mcp_content_type"] == "structured_content"


def test_bridge_transport_auth_and_nav2_startup_guard(monkeypatch) -> None:
    backend = SimulatorBackend()
    settings = BridgeSettings(api_key="bridge-secret")
    server = create_server(RobotCapabilityService(backend), settings)
    app = APIKeyASGI(
        server.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1"),
        settings.api_key,
    )

    async def request_without_key():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers={"host": "127.0.0.1:80"},
        ) as client:
            denied = await client.get("/mcp")
            health = await client.get("/health")
            return denied, health

    denied, health = asyncio.run(request_without_key())
    assert denied.status_code == 401
    assert denied.json() == {"error": "bridge_authentication_required"}
    assert health.status_code == 200

    monkeypatch.setenv("AIGC_LITE_ROS2_BACKEND", "nav2")
    monkeypatch.setenv("AIGC_LITE_ROS2_API_KEY", "")
    with pytest.raises(RuntimeError, match="API_KEY"):
        BridgeSettings.from_env()


def test_nav2_import_failure_is_lazy_and_actionable(monkeypatch) -> None:
    from aigc_lite_ros2 import nav2

    def unavailable(name: str):
        raise ImportError(name)

    monkeypatch.setattr(nav2.importlib, "import_module", unavailable)
    with pytest.raises(RuntimeError, match="sourced ROS 2 installation"):
        nav2._load_ros()


def _nav2_backend_for_reconciliation_test():
    from aigc_lite_ros2 import nav2

    backend = object.__new__(nav2.Nav2Backend)
    backend.robot_id = "nav2-test"
    backend.environment = ExecutionEnvironment.HARDWARE
    backend.map_id = "map-test"
    backend.action_name = "navigate_to_pose"
    backend._lock = asyncio.Lock()
    backend._feedback_lock = threading.Lock()
    backend._active = None
    backend._pending = None
    backend._dispatching = True
    backend._receipts = {}
    backend._key_to_action = {}
    backend._latest = None
    backend._reconciliation_tasks = {}
    backend._ros = {
        "GoalStatus": SimpleNamespace(
            STATUS_SUCCEEDED=1,
            STATUS_CANCELED=2,
            STATUS_ABORTED=3,
        )
    }

    async def get_state():
        active = backend._active
        return RobotState(
            robot_id=backend.robot_id,
            environment=backend.environment,
            observed_at="2026-09-30T00:00:00+00:00",
            pose=Pose2D(0, 0, 0),
            navigation_status="executing" if active else "idle",
            active_action_id=active.action_id if active else None,
            map_id=backend.map_id,
        )

    backend.get_state = get_state
    return backend


def test_nav2_late_goal_acceptance_is_cancelled_and_recorded() -> None:
    from aigc_lite_ros2 import nav2

    async def run():
        backend = _nav2_backend_for_reconciliation_test()
        loop = asyncio.get_running_loop()
        goal_future = loop.create_future()
        result_future = loop.create_future()
        cancel_future = loop.create_future()
        cancel_future.set_result(SimpleNamespace())
        cancel_calls = 0

        class GoalHandle:
            accepted = True
            goal_id = SimpleNamespace(uuid=bytes.fromhex("01" * 16))

            def get_result_async(self):
                return result_future

            def cancel_goal_async(self):
                nonlocal cancel_calls
                cancel_calls += 1
                result_future.set_result(SimpleNamespace(status=2))
                return cancel_future

        request = NavigationRequest(
            idempotency_key="late-goal-001",
            goal=Pose2D(1, 2, 0),
            action_timeout_seconds=30,
        )
        pending = nav2._PendingNavigation(
            action_id="action-late",
            request=request,
            before=await backend.get_state(),
            goal_future=goal_future,
            started_at="2026-09-30T00:00:00+00:00",
        )
        backend._pending = pending
        backend._key_to_action[request.idempotency_key] = pending.action_id
        task = asyncio.create_task(backend._reconcile_late_dispatch(pending))
        await asyncio.sleep(0)
        goal_future.set_result(GoalHandle())
        await task
        return backend, cancel_calls

    backend, cancel_calls = asyncio.run(run())
    assert cancel_calls == 1
    assert backend._pending is None
    assert backend._active is None
    assert backend._dispatching is False
    assert backend._receipts["action-late"].status is PhysicalActionStatus.CANCELLED
    assert backend._receipts["action-late"].stop_confirmed is True


def test_nav2_indeterminate_stop_keeps_motion_slot_until_terminal_result() -> None:
    from aigc_lite_ros2 import nav2

    async def run():
        backend = _nav2_backend_for_reconciliation_test()
        request = NavigationRequest(
            idempotency_key="indeterminate-001",
            goal=Pose2D(1, 2, 0),
            action_timeout_seconds=30,
        )
        active = nav2._Nav2Action(
            action_id="action-indeterminate",
            request=request,
            before=await backend.get_state(),
            goal_handle=SimpleNamespace(),
            result_future=asyncio.get_running_loop().create_future(),
            provider_action_id="provider-action",
            started_at="2026-09-30T00:00:00+00:00",
        )
        backend._active = active
        uncertain = await backend._finish(
            active,
            status=PhysicalActionStatus.INDETERMINATE,
            stop_confirmed=False,
            error_code="cancel_confirmation_timeout",
            error_message="Stop was not confirmed.",
        )
        still_blocking = backend._active
        terminal = await backend._finish(
            active,
            status=PhysicalActionStatus.CANCELLED,
            stop_confirmed=True,
            error_code="action_cancelled",
            error_message="Nav2 later confirmed cancellation.",
            nav2_status=2,
        )
        return backend, active, uncertain, still_blocking, terminal

    backend, active, uncertain, still_blocking, terminal = asyncio.run(run())
    assert uncertain.status is PhysicalActionStatus.INDETERMINATE
    assert uncertain.stop_confirmed is False
    assert still_blocking is active
    assert backend._active is None
    assert terminal.status is PhysicalActionStatus.CANCELLED
    assert terminal.stop_confirmed is True
    assert backend._receipts[active.action_id] == terminal


def test_remote_tool_cannot_lower_provider_policy() -> None:
    backend = SimulatorBackend(travel_seconds=0.01)
    settings = BridgeSettings()
    server = create_server(RobotCapabilityService(backend), settings)
    streamable_app = server.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1")

    @asynccontextmanager
    async def transport():
        url = "http://127.0.0.1/mcp"
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=streamable_app),
            base_url="http://127.0.0.1",
            headers={"host": "127.0.0.1:80"},
        ) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                yield streams

    async def run():
        provider = MCPToolProvider(
            "locked-robot",
            "http://127.0.0.1/mcp",
            risk=ToolRisk.HIGH,
            required_scopes=frozenset({"provider:locked"}),
            timeout_seconds=5,
            transport_factory=transport,
        )
        async with server.session_manager.run():
            return await provider.list_tools()

    specs = asyncio.run(run())
    state = next(item for item in specs if item.native_name == "robot_get_state")
    navigate = next(item for item in specs if item.native_name == "robot_navigate_to")
    assert state.risk is ToolRisk.HIGH
    assert state.required_scopes == frozenset({"provider:locked"})
    assert navigate.required_scopes == frozenset({"provider:locked", "robot:motion"})
    assert navigate.timeout_seconds == 5
