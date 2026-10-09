"""The live Nav2 evaluator stays bounded and fails closed before ROS imports."""

from __future__ import annotations

import asyncio

import pytest
from aigc_lite_ros2.nav2_graph_eval import _metrics, _require_receipt, run_graph_acceptance


def test_graph_evaluation_rejects_implicit_or_hardware_domain(monkeypatch) -> None:
    monkeypatch.delenv("ROS_DOMAIN_ID", raising=False)
    monkeypatch.setenv("AIGC_LITE_ROS2_ENVIRONMENT", "simulation")
    with pytest.raises(ValueError, match="ROS_DOMAIN_ID"):
        asyncio.run(run_graph_acceptance(execute_motion=False, domain_id=87))

    monkeypatch.setenv("ROS_DOMAIN_ID", "87")
    monkeypatch.setenv("ROS_AUTOMATIC_DISCOVERY_RANGE", "SUBNET")
    with pytest.raises(ValueError, match="LOCALHOST"):
        asyncio.run(run_graph_acceptance(execute_motion=False, domain_id=87))

    monkeypatch.setenv("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST")
    monkeypatch.delenv("ROS_STATIC_PEERS", raising=False)
    monkeypatch.setenv("AIGC_LITE_ROS2_ENVIRONMENT", "hardware")
    with pytest.raises(ValueError, match="simulation"):
        asyncio.run(run_graph_acceptance(execute_motion=False, domain_id=87))


def test_graph_receipt_requires_real_goal_and_confirmed_stop() -> None:
    valid = {
        "contract_version": "robot.action-receipt.v1",
        "environment": "simulation",
        "status": "cancelled",
        "stop_confirmed": True,
        "provider_action_id": "ros-goal-123",
    }
    _require_receipt(valid, status="cancelled", phase="test")
    for change in (
        {"stop_confirmed": False},
        {"provider_action_id": None},
        {"status": "indeterminate"},
        {"environment": "hardware"},
    ):
        with pytest.raises(RuntimeError, match="confirmed Nav2"):
            _require_receipt({**valid, **change}, status="cancelled", phase="test")


def test_graph_metrics_separate_goal_success_from_cancel_and_uncertainty() -> None:
    trials = [
        {"passed": True, "receipt": {"status": "succeeded", "stop_confirmed": True}},
        {
            "passed": True,
            "receipt": {"status": "cancelled", "stop_confirmed": True},
            "stop_observation": {"drift_m": 0.06},
        },
        {
            "passed": False,
            "receipt": {"status": "indeterminate", "stop_confirmed": False},
        },
    ]
    assert _metrics(trials) == {
        "trial_count": 3,
        "passed_count": 2,
        "succeeded_count": 1,
        "cancelled_count": 1,
        "unresolved_stop_count": 1,
        "max_observed_stop_drift_m": 0.06,
    }
