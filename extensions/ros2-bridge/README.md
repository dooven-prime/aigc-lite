# aigc-lite ROS 2 bridge

`aigc-lite-ros2` is an optional, separately installable physical capability
provider. It projects a deliberately small robot surface over MCP; the main
`aigc-lite` process remains unaware of ROS 2, DDS, Nav2, TF2, URDF, or hardware
drivers.

```text
Agent / scheduler / inbound MCP
              |
       aigc-lite Tool Catalog
       permission + budget + ledger
              |
        outbound MCP provider
              |
       aigc-lite-ros2 process
              |
    SimulatorBackend | Nav2Backend
```

## Capability surface

| tool | effect | policy | result |
|---|---|---|---|
| `robot_get_state` | observation | low risk | `robot.state.v1` |
| `robot_inspect` | observation | low risk | `robot.observation-receipt.v1` |
| `robot_navigate_to` | physical motion | high risk + `robot:motion` | `robot.action-receipt.v1` |
| `robot_cancel_action` | physical motion | high risk + `robot:control` | `robot.action-receipt.v1` |

The bridge does not expose arbitrary Topic, Service, Action, parameter, shell,
or launch-file access. `robot_navigate_to` requires a caller-generated
idempotency key. Reusing the key with the same intent replays the original
receipt; reusing it with a different goal fails closed.

Every action receipt distinguishes `succeeded`, `failed`, `cancelled`, and
`indeterminate`. It records simulation/hardware identity, the provider action
ID, bounded feedback, before/after observations, and whether stopping was
confirmed. A transport timeout never implies that the robot stopped.

## Run the simulator

The simulator has no ROS dependency and is the supported CI path:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e extensions/ros2-bridge
python -m aigc_lite_ros2
```

The MCP endpoint is `http://127.0.0.1:9010/mcp`; health is available at
`http://127.0.0.1:9010/health`.

Useful simulator settings:

```dotenv
AIGC_LITE_ROS2_BACKEND=simulator
AIGC_LITE_ROS2_ROBOT_ID=sim-1
AIGC_LITE_ROS2_MAP_ID=demo-map
AIGC_LITE_ROS2_SIM_TRAVEL_SECONDS=0.2
# success | failure | indeterminate
AIGC_LITE_ROS2_SIM_OUTCOME=success
# Optional for loopback simulator; required for Nav2 or non-loopback binding.
AIGC_LITE_ROS2_API_KEY=replace-with-a-long-random-secret
```

Connect it to the main process through the existing workspace MCP Server API or
through the environment-first quick-start configuration:

```dotenv
AIGC_LITE_ADMIN_TOOL_SCOPES=robot:motion,robot:control
ROBOT_MCP_AUTH=Bearer replace-with-the-same-long-random-secret
AIGC_LITE_MCP_SERVERS_JSON=[{"id":"robot","url":"http://127.0.0.1:9010/mcp","workspace_id":"default","header_env":{"Authorization":"ROBOT_MCP_AUTH"},"risk":"low","required_scopes":[],"timeout_seconds":180}]
```

The provider-level `risk` and scopes are minimums. Per-tool MCP metadata may
only increase risk, add scopes, or shorten the configured timeout. Without the
explicit admin scopes above, administrators can inspect state but cannot
discover the motion tools.

Scopes only make a physical tool discoverable. Before dispatch, the main
runtime also atomically consumes a current `AuthorizationGrant` whose actor is
the authenticated principal, whose action is the native tool name (for example
`robot_navigate_to`), and whose target is
`provider:<workspace MCP provider id>/robot:<AIGC_LITE_ROS2_ROBOT_ID>`.
Expired, exhausted, stale-receipt, or missing grants fail closed without calling
the bridge. The consumed grant identity is written into Tool Step metadata.

The generic scheduled `tool.call` target intentionally runs without elevated
scopes, so it cannot schedule robot motion. A future autonomous physical task
must be a dedicated narrow `TaskRunner` target with its own authorization,
robot-state preconditions, lease, and recovery policy; do not grant motion
scope to the generic scheduler identity.

To demonstrate failure closure without hardware, start the simulator once with
`AIGC_LITE_ROS2_SIM_OUTCOME=failure` and once with `indeterminate`. Both produce
queryable structured artifacts through the normal Run/Step ledger; neither is
reported as success.

## Run against Nav2

Install and source the ROS 2 environment that supplies `rclpy`, `nav2_msgs`,
`geometry_msgs`, `action_msgs`, and `tf2_ros`, then install this Python package
into that environment:

```bash
source /opt/ros/<distro>/setup.bash
source <your-workspace>/install/setup.bash  # when using an overlay
pip install -e extensions/ros2-bridge

export AIGC_LITE_ROS2_BACKEND=nav2
export AIGC_LITE_ROS2_ENVIRONMENT=simulation  # use hardware only for a real robot
export AIGC_LITE_ROS2_ROBOT_ID=robot-1
export AIGC_LITE_ROS2_MAP_ID=warehouse-v1
export AIGC_LITE_ROS2_GLOBAL_FRAME=map
export AIGC_LITE_ROS2_BASE_FRAME=base_link
export AIGC_LITE_ROS2_NAV2_ACTION=navigate_to_pose
export AIGC_LITE_ROS2_API_KEY=replace-with-a-long-random-secret
python -m aigc_lite_ros2
```

`Nav2Backend` uses a dedicated ROS executor thread, sends a
`NavigateToPose` Action goal, obtains bounded progress feedback, maps terminal
Action status into the receipt, reads pose through TF2, and sends Action cancel
on timeout or caller cancellation. ROS imports are lazy, so the core package
and simulator remain usable on machines without ROS.

The current repository CI verifies the simulator, MCP projection, policy
elevation, cancellation, idempotency, and receipt contracts. It does not claim
live Nav2, Gazebo, DDS, or hardware validation; those require a sourced ROS
graph and become a separate integration job.

## Minimum credible autonomy acceptance loop (not yet completed)

A Nav2 provider call alone is not robotics validation. The next integration
milestone is a fixed simulation scenario with a frozen map, initial pose,
goal tolerance, robot/software versions, and a task success predicate. Run a
baseline and inject at least goal rejection, delayed feedback, transport loss,
cancel timeout, and localization degradation. For each trial, capture the ROS
goal ID, feedback/pose timeline, timeout and cancellation decisions, stop
confirmation (or explicit `indeterminate`), and matching aigc-lite
Run/Step/ActionReceipt IDs. A runner should replay the scenario under the same
seed/config and produce a machine-readable report with success rate, failure
taxonomy, latency distribution, and unresolved-stop count. The report must
distinguish simulator assertions from a real Nav2/Gazebo graph and never treat
an unconfirmed stop as success or permission for another motion.

This remains an acceptance target, not a claim that the current simulator or
unit tests measure physical stop distance or hardware safety. Device-side
e-stop and protective interlocks remain outside this bridge.

## Safety boundary

MCP cancellation is a best-effort Action cancellation path. It is not a safety
controller and is not an emergency stop. A real deployment must keep e-stop,
protective stop, collision avoidance, velocity/acceleration limits, controller
watchdogs, and hardware interlocks outside the Agent/MCP data path. Bind the
bridge to loopback by default. Nav2 and non-loopback modes refuse startup
without an API key; when crossing hosts, also add TLS or mutually authenticated
transport, network segmentation, and explicit host/origin allowlists.
