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
# success | failure | indeterminate | goal_rejected | feedback_stall
# transport_loss | localization_loss | cancel_unconfirmed
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

## Deterministic simulator acceptance loop

The optional package ships a fixed, bounded nine-trial navigation scenario.
It covers baseline arrival, goal rejection, navigation failure, sparse
feedback, transport loss, localization loss, confirmed and unconfirmed cancel,
and action timeout. The scenario freezes map identity, initial/goal poses,
arrival tolerance, seed, injected fault, expected receipt, and safety
assertions. The seed is reserved for future stochastic backends; v1's
simulator does not use randomness. This evaluation does not require ROS 2:

```bash
aigc-lite-ros2-eval run --output navigation-report.json
aigc-lite-ros2-eval replay \
  --report navigation-report.json --output navigation-replay.json
```

`run --scenario path/to/scenario.json` selects a different versioned scenario;
without it, the installed wheel's `navigation_v1.json` is used.

The runner refuses to overwrite either output and exits nonzero on a failed
trial contract or replay mismatch. The report contains a portable evaluation
Run ID, ordered Step traces, action receipts, before/after observations,
feedback, stop confirmation, failure taxonomy, success/contract rates, latency
distribution, package/backend versions, and SHA-256 scenario/report/replay
digests. The replay compares deterministic behavior and runtime version, not
UUIDs or wall-clock latency. Digests are integrity checks, not signatures or
proof of the report author's identity. Keep these
generated JSON files as runtime evidence; do not commit them.

The version fields do not prove the exact source tree or installed dependency
bytes; that requires a locked environment and independently retained build
provenance.

In this fault matrix only the baseline and sparse-feedback trials are expected
to reach the goal: task success is 2/9, while all nine safety/behavior
contracts should pass. A failed physical task is not a failed evaluation when
the injected fault is handled as specified.

An `indeterminate` stop keeps the simulator motion slot occupied. A second
goal must fail with `robot_busy` until a simulator-only operator control
confirms stop; that control is deliberately absent from MCP. In the normal
outbound MCP path, aigc-lite's existing Tool Catalog records tool invocations
as Run/Step entries. The standalone evaluator's portable Run/Step trace is not
automatically imported into the central workspace ledger.

This is **deterministic backend acceptance**, not a Nav2/Gazebo or real-robot
acceptance claim. The separate live-graph path below observes real Nav2 Action
and TF behavior. Hardware e-stop and protective interlocks remain outside this
bridge.

## Narrow live Nav2/Gazebo graph acceptance

`aigc-lite-nav2-graph-eval` is a separate, operator-run Jazzy/TurtleBot3
integration path. It targets the packaged `tb3_sandbox` map and a fixed
`(-2.0, -0.5)` start; it is **not** a generic ROS graph crawler or a hardware
test. Use a fresh ROS domain that contains only the simulation launched for
this evaluation. The evaluator requires `LOCALHOST` discovery and no
`ROS_STATIC_PEERS`, in addition to a non-default domain; those settings narrow
discovery but do not prove that no hardware is attached locally. In WSL Ubuntu,
open two terminals:

```bash
# Terminal 1: start a fresh, headless graph.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=87  # choose an unused non-default domain
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
ros2 launch nav2_bringup tb3_simulation_launch.py use_rviz:=False headless:=True
```

```bash
# Terminal 2: run promptly after launch, before Nav2 bringup times out on TF.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=87
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export AIGC_LITE_ROS2_ENVIRONMENT=simulation
cd /mnt/e/Documents/PycharmProjects/aigc-lite
export PYTHONPATH="$PWD/extensions/ros2-bridge/src:$PYTHONPATH"
python3 -m aigc_lite_ros2.nav2_graph_eval --domain-id 87 \
  --output /tmp/nav2-preflight.json
python3 -m aigc_lite_ros2.nav2_graph_eval --domain-id 87 \
  --execute-motion --output /tmp/nav2-evaluation.json
```

Preflight observes an advancing `/clock`, the Gazebo bridge, `use_sim_time`
on AMCL, navigator and robot-state publisher, active AMCL, then publishes the
fixed initial pose. It requires `map→base_link` TF and an active
`NavigateToPose` Action server before any goal. If Nav2 already aborted
bringup before the initial pose arrived, restart the graph; a late initial pose
does not retroactively make that run valid. The `--execute-motion` flag is
required for the three motion trials: short arrival, explicit cancellation,
and a one-second action timeout. A failed receipt or unconfirmed stop blocks
later goals. Cancel and timeout additionally require two TF poses one second
apart with no more than 0.25 m drift and no active action.

The non-overwriting JSON report contains actual ROS goal IDs, action receipts,
ordered Step traces, TF/clock/lifecycle observations, success/failure counts,
and hashes of the map, Nav2 parameters, world, robot description, and bridge
source files. Runtime reports belong outside Git. ROS Action cancellation and
TF drift in simulation are evidence of this graph's behavior, not proof of
hardware stopping distance, physical safety, or graph isolation; the operator
must ensure no robot shares that ROS domain. The graph evaluator does not
automatically import its report into aigc-lite's central Run ledger.

## Safety boundary

MCP cancellation is a best-effort Action cancellation path. It is not a safety
controller and is not an emergency stop. A real deployment must keep e-stop,
protective stop, collision avoidance, velocity/acceleration limits, controller
watchdogs, and hardware interlocks outside the Agent/MCP data path. Bind the
bridge to loopback by default. Nav2 and non-loopback modes refuse startup
without an API key; when crossing hosts, also add TLS or mutually authenticated
transport, network segmentation, and explicit host/origin allowlists.
