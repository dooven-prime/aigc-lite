"""Bounded, deterministic Event-Anchored Consumer witness replay.

This is an executable finite-case checker, not a proof of the RIME consumer
theorem or a verified imperative implementation of placed forward-UFE.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ...core.errors import InvalidEvidenceError

PROFILE = "rime.event-anchored-consumer.case.v1"
SOURCE_COMMIT = "8dc2e615c5b011efa498bbd6fc4dec613c637030"
SOURCE_ROOT = "contracts/consumer-replay/v1"
SOURCE_CONTRACT_VERSION = "rime.consumer-replay.v1"
MANIFEST_SHA256 = "0db53c0235a2add4189df1e8fcb5aa63da1443bc7451ab865313e842583b0eca"
# Exact inventory of the public manifest at SOURCE_COMMIT. The manifest does
# not hash itself; its own byte hash is pinned separately above.
SOURCE_FILES = (
    (".gitattributes", "BYTE_MATERIALIZATION_POLICY", 64,
     "6966d0bc0169072d3f2c6341ebfd973e3a9c98c98c3941ca8459f077850f934d"),
    ("README.md", "SCOPE_AND_CASE_DECISIONS", 6480,
     "76525a11d3cd814c84d319af683c715065b7e8056fc5bb2a87f6f5cb1938908c"),
    ("candidate-reports.schema.json", "OPTIONAL_CASE_REPORT_JSON_SHAPE", 2693,
     "90b8c9ca530e59e4321a7ebc11dba0efed7b36f4faec3bbeff28a0339f4a8076"),
    ("CONSUMER_SPEC.md", "INDEPENDENT_LOG_ORACLE", 3880,
     "184a507d1ad54004fec0b195db952ce1a18d949f6264f6c2f07b2a56b77bf0e1"),
    ("FOREST_MODEL.md", "PLACED_FOREST_REPRESENTATION", 3098,
     "7ee153f68238a436d973f254bb16857ba78e6a853a85e127cb5eee9e66535c48"),
    ("UFE_ADAPTER.md", "PLACED_FORWARD_UFE_REPRESENTATION", 4265,
     "bbd47dbaca39c2828f3e9a92fd536adb26730c63be3bf92c9a6def4c374fd35c"),
    ("examples/witness.json", "FINITE_DATA_WITNESS", 408,
     "af7ecdc1c4d4dd38a6236f58684ec8fe7d3e7e3d00baa2a7d381b98859b3115d"),
)
CONTRACT_VERSION = "aigc-lite.rime.consumer-replay.v2"
LIMITATIONS = (
    "Finite, bounded witness only; not an All-N adequacy proof or old B0 closure.",
    "UFE route is an executable decoder of the published equations, not a verified imperative wrapper.",
    "Local checker agreement does not establish independent validation or knowledge admission.",
    "The published commit and manifest bind source bytes; this server does not re-fetch them or verify a release signature per request.",
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def checker_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _block_shape(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(type(atom) is int for atom in value)
        and len(value) == len(set(value))
    )


def _position_shape(value: object) -> bool:
    return type(value) is int and value >= 0


def _placed_shape(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"q", "block"}
        and _position_shape(value["q"])
        and _block_shape(value["block"])
    )


def _origin_shape(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "letter", "ports", "input_blocks", "collision_image", "fresh_block"
    }:
        return False
    ports, blocks = value["ports"], value["input_blocks"]
    return (
        value["letter"] == "d"
        and isinstance(ports, list)
        and len(ports) == 2
        and all(_position_shape(port) for port in ports)
        and ports[0] != ports[1]
        and isinstance(blocks, list)
        and len(blocks) == 2
        and all(_block_shape(block) for block in blocks)
        and _position_shape(value["collision_image"])
        and _block_shape(value["fresh_block"])
    )


def _absorption_shape(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"letter", "carrier_port", "carrier_before", "other_port",
                           "other_block", "collision_image", "carrier_after"}
        and value["letter"] == "d"
        and all(_position_shape(value[key]) for key in (
            "carrier_port", "other_port", "collision_image"
        ))
        and all(_block_shape(value[key]) for key in (
            "carrier_before", "other_block", "carrier_after"
        ))
    )


def _report_shape(value: object) -> bool:
    """Structural JSON contract; semantic consistency is checked by replay."""
    if not isinstance(value, dict) or set(value) != {
        "partition", "selected_block", "origin", "absorptions", "carrier"
    }:
        return False
    partition, absorptions = value["partition"], value["absorptions"]
    return (
        isinstance(partition, list)
        and bool(partition)
        and all(_placed_shape(packet) for packet in partition)
        and _block_shape(value["selected_block"])
        and _origin_shape(value["origin"])
        and isinstance(absorptions, list)
        and all(_absorption_shape(item) for item in absorptions)
        and _placed_shape(value["carrier"])
    )


class RimeConsumerWitnessAdapter:
    importer_id = "rime.event-anchored-consumer"
    version = 2
    display_name = "RIME Event-Anchored Consumer Replay"
    description = "Finite three-route data-witness replay; candidate evidence only."
    source_format = "application/json; aigc-lite.rime.consumer-replay.v2"
    target_surface = "research_claim_candidate"
    preview_endpoint = None
    commit_endpoint = "/api/research/rime-consumer/witnesses"
    importer_contract = "Frozen finite consumer witness; no scripts or qualification authority."

    def identity(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "source_contract_version": SOURCE_CONTRACT_VERSION,
            "source_commit": SOURCE_COMMIT,
            "source_root": SOURCE_ROOT,
            "source_manifest_sha256": MANIFEST_SHA256,
            "source_files": [
                {"path": path, "role": role, "size": size, "sha256": sha256}
                for path, role, size, sha256 in SOURCE_FILES
            ],
            "checker_sha256": checker_sha256(),
        }

    def contract(self) -> dict:
        return {
            **self.identity(),
            "limits": {"q_size": 16, "word_length": 128, "commands": 128, "bytes": 200_000},
            "limitations": list(LIMITATIONS),
        }

    def validate(self, witness: dict) -> dict:
        if not isinstance(witness, dict) or len(canonical(witness).encode()) > 200_000:
            raise InvalidEvidenceError("witness", "Expected a bounded JSON object")
        required = {"contract", "environment", "prefix", "selected_block", "commands"}
        if not required <= set(witness) or set(witness) - required - {"candidate_reports", "prior_case_id"}:
            raise InvalidEvidenceError("witness", "Witness fields must match the frozen v2 data contract")
        if witness["contract"] != self.identity():
            raise InvalidEvidenceError("contract", "Frozen source/checker identity mismatch")
        prior_case_id = witness.get("prior_case_id")
        if prior_case_id is not None and (
            not isinstance(prior_case_id, str) or not 1 <= len(prior_case_id) <= 100
        ):
            raise InvalidEvidenceError("prior_case_id", "Prior Case ID is malformed")
        env = witness["environment"]
        if not isinstance(env, dict) or set(env) != {
            "q_size", "p", "d", "omega", "iota", "ufe_enumeration", "kernel_ports", "collision_image"
        }:
            raise InvalidEvidenceError("environment", "Environment fields do not match v1")
        n = env["q_size"]
        if type(n) is not int or not 2 <= n <= 16 or env["p"] != "successor_mod_n":
            raise InvalidEvidenceError("environment", "Expected 2 <= |Q| <= 16 and successor p")
        d, omega, iota, enumeration = (env[k] for k in ("d", "omega", "iota", "ufe_enumeration"))
        if not all(isinstance(v, list) for v in (d, omega, iota, enumeration)):
            raise InvalidEvidenceError("environment", "d, omega, iota, enumeration must be lists")
        if len(d) != n or any(type(v) is not int or v not in range(n) for v in d):
            raise InvalidEvidenceError("d", "d must map every Q point into Q")
        if not 2 <= len(omega) <= n or any(type(v) is not int for v in omega) or len(set(omega)) != len(omega):
            raise InvalidEvidenceError("omega", "Omega must be a unique bounded atom list")
        if len(iota) != len(omega) or any(type(v) is not int or v not in range(n) for v in iota) or len(set(iota)) != len(iota):
            raise InvalidEvidenceError("iota", "iota must inject Omega into Q")
        if len(enumeration) != len(omega) or any(type(v) is not int for v in enumeration) or set(enumeration) != set(omega):
            raise InvalidEvidenceError("ufe_enumeration", "Enumeration must permute Omega")
        ports = env["kernel_ports"]
        collision = env["collision_image"]
        if (not isinstance(ports, list) or len(ports) != 2 or
            any(type(v) is not int or v not in range(n) for v in ports) or
            ports[0] == ports[1] or
            type(collision) is not int or collision not in range(n) or
            [q for q, value in enumerate(d) if value == collision] != sorted(ports) or
            len(set(d)) != n - 1):
            raise InvalidEvidenceError("d", "d must have exactly one binary ordered kernel")
        prefix = witness["prefix"]
        if not isinstance(prefix, str) or len(prefix) > 128 or set(prefix) - {"p", "d"}:
            raise InvalidEvidenceError("prefix", "Prefix must be a p/d word of length <= 128")
        selected = witness["selected_block"]
        if not isinstance(selected, list) or not selected or any(type(v) is not int for v in selected) or sorted(set(selected)) != selected or not set(selected) <= set(omega):
            raise InvalidEvidenceError("selected_block", "Selected F must be a sorted Omega block")
        commands = witness["commands"]
        if not isinstance(commands, list) or len(commands) > 128 or any(
            not isinstance(c, dict) or set(c) != {"kind", "letter"} or
            not isinstance(c["kind"], str) or not isinstance(c["letter"], str) or
            c["kind"] not in {"Carry", "Absorb"} or c["letter"] not in {"p", "d"}
            for c in commands
        ):
            raise InvalidEvidenceError("commands", "Expected bounded Carry/Absorb p/d data commands")
        reports = witness.get("candidate_reports")
        if reports is not None and (
            not isinstance(reports, list)
            or len(reports) != len(commands) + 1
            or any(not _report_shape(report) for report in reports)
        ):
            raise InvalidEvidenceError(
                "candidate_reports", "Reports do not match the pinned structural JSON contract"
            )
        return witness

    def replay(self, witness: dict) -> dict:
        self.validate(witness)
        env = witness["environment"]
        selected = tuple(witness["selected_block"])
        oracle = _LogRoute(env)
        forest = _ForestRoute(env)
        ufe = _UFERoute(env)
        routes = (oracle, forest, ufe)
        trace: list[dict] = []
        prefix_checks: list[dict] = []

        def fail(code: str, index: int, detail: str) -> dict:
            return {"outcome": "failed", "failure": {"code": code, "index": index, "detail": detail},
                    "trace": trace, "prefix_checks": prefix_checks, "limitations": list(LIMITATIONS)}

        for prefix_index, letter in enumerate(witness["prefix"], 1):
            for route in routes:
                route.push(letter)
            partitions = [
                _partition(oracle.packets, set),
                _partition(forest.packets, forest._atoms),
                _partition(ufe.placement, ufe._class),
            ]
            placements = [
                _placed(oracle.packets, set),
                _placed(forest.packets, forest._atoms),
                _placed(ufe.placement, ufe._class),
            ]
            event_lists = [
                oracle.events,
                [node["event"] for node in forest.nodes if node["event"] is not None],
                ufe._events(),
            ]
            if (partitions[0] != partitions[1] or partitions[0] != partitions[2] or
                placements[0] != placements[1] or placements[0] != placements[2] or
                event_lists[0] != event_lists[1] or event_lists[0] != event_lists[2]):
                return fail("prefix_route_divergence", prefix_index, "Seed prefix routes diverge")
            prefix_checks.append({"prefix_index": prefix_index, "letter": letter,
                                  "partition": partitions[0], "placed_packets": placements[0],
                                  "fusion_count": len(oracle.events)})
        if not oracle.register(selected):
            return fail("registration_domain", 0, "Selected F is not a logged fresh event block")
        if not forest.register(selected) or not ufe.register(selected):
            return fail("registration_divergence", 0, "Routes disagree on event registration")

        def compare(index: int, command: dict | None) -> dict | None:
            reports = [route.report() for route in routes]
            placements = [
                _placed(oracle.packets, set),
                _placed(forest.packets, forest._atoms),
                _placed(ufe.placement, ufe._class),
            ]
            if (reports[0] != reports[1] or reports[0] != reports[2] or
                placements[0] != placements[1] or placements[0] != placements[2]):
                return fail("route_divergence", index, "Log, forest and UFE reports differ")
            candidate = witness.get("candidate_reports")
            if candidate is not None and candidate[index] != reports[0]:
                return fail("candidate_mismatch", index, "Candidate report differs from replay")
            trace.append({"index": index, "command": command, "report": reports[0],
                          "placed_packets": placements[0],
                          "forest_latest": forest.latest_block(),
                          "ufe_union_history": ufe.union_history()})
            return None

        mismatch = compare(0, None)
        if mismatch:
            return mismatch
        for index, command in enumerate(witness["commands"], 1):
            guards = [route.guard(command["letter"]) for route in routes]
            if len(set(guards)) != 1:
                return fail("guard_divergence", index, "Routes disagree on command domain")
            if guards[0] != command["kind"]:
                return fail("command_domain", index, "Command is not enabled for registered F")
            for route in routes:
                route.push(command["letter"])
            mismatch = compare(index, command)
            if mismatch:
                return mismatch
        return {"outcome": "passed", "failure": None, "trace": trace,
                "prefix_checks": prefix_checks,
                "limitations": list(LIMITATIONS)}


def _partition(packets: dict[int, object], atoms) -> list[list[int]]:
    return sorted([sorted(atoms(value)) for value in packets.values()])


def _placed(packets: dict[int, object], atoms) -> list[dict]:
    return [{"q": q, "block": sorted(atoms(packets[q]))} for q in sorted(packets)]


def _carrier(packets: dict[int, object], selected: set[int], atoms) -> dict:
    return next(
        {"q": q, "block": sorted(atoms(packet))}
        for q, packet in sorted(packets.items())
        if selected <= atoms(packet)
    )


def _guard(packets: dict[int, object], carrier: set[int], letter: str, ports: tuple[int, int], atoms) -> str:
    if letter == "p":
        return "Carry"
    occupied = [port for port in ports if port in packets]
    if len(occupied) == 2 and any(carrier <= atoms(packets[port]) for port in occupied):
        return "Absorb"
    return "Carry"


def _event_record(ports: tuple[int, int], packets, atoms, c: int) -> dict:
    left, right = (sorted(atoms(packets[port])) for port in ports)
    return {"letter": "d", "ports": list(ports), "input_blocks": [left, right],
            "collision_image": c, "fresh_block": sorted(set(left) | set(right))}


class _LogRoute:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.ports = tuple(env["kernel_ports"])
        self.packets = {q: {atom} for atom, q in zip(env["omega"], env["iota"], strict=True)}
        self.events: list[dict] = []
        self.selected: set[int] | None = None
        self.origin: dict | None = None
        self.absorptions: list[dict] = []

    def push(self, letter: str) -> None:
        old = self.packets
        if letter == "d" and all(port in old for port in self.ports):
            event = _event_record(self.ports, old, set, self.env["collision_image"])
            self.events.append(event)
            if self.selected is not None:
                blocks = [old[port] for port in self.ports]
                for i in (0, 1):
                    if self.selected <= blocks[i]:
                        self.absorptions.append(_absorption(event, i))
                        break
        mapping = (lambda q: (q + 1) % self.env["q_size"]) if letter == "p" else self.env["d"].__getitem__
        next_packets: dict[int, set[int]] = {}
        for q, block in old.items():
            next_packets.setdefault(mapping(q), set()).update(block)
        self.packets = next_packets

    def register(self, selected: tuple[int, ...]) -> bool:
        event = next((e for e in self.events if e["fresh_block"] == list(selected)), None)
        if event is None:
            return False
        self.selected = set(selected)
        self.origin = event
        self.absorptions = []
        # Registration is after prefix: recover later absorptions already in prefix.
        seen = False
        for later in self.events:
            if later is event:
                seen = True
            elif seen:
                for i, block in enumerate(later["input_blocks"]):
                    if self.selected <= set(block):
                        self.absorptions.append(_absorption(later, i))
                        break
        return True

    def guard(self, letter: str) -> str:
        return _guard(self.packets, self.selected, letter, self.ports, set)

    def report(self) -> dict:
        return {"partition": _placed(self.packets, set), "selected_block": sorted(self.selected),
                "origin": self.origin, "absorptions": self.absorptions,
                "carrier": _carrier(self.packets, self.selected, set)}


def _absorption(event: dict, i: int) -> dict:
    return {"letter": "d", "carrier_port": event["ports"][i],
            "carrier_before": event["input_blocks"][i], "other_port": event["ports"][1-i],
            "other_block": event["input_blocks"][1-i], "collision_image": event["collision_image"],
            "carrier_after": event["fresh_block"]}


class _ForestRoute:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.ports = tuple(env["kernel_ports"])
        self.nodes: list[dict] = [{"atoms": {atom}, "children": None, "parent": None, "event": None}
                                  for atom in env["omega"]]
        self.packets = {q: i for i, q in enumerate(env["iota"])}
        self.latest: int | None = None
        self.selected_node: int | None = None

    def _atoms(self, node: int) -> set[int]:
        return self.nodes[node]["atoms"]

    def push(self, letter: str) -> None:
        old = self.packets
        mapping = (lambda q: (q + 1) % self.env["q_size"]) if letter == "p" else self.env["d"].__getitem__
        next_packets = {mapping(q): node for q, node in old.items()}
        if letter == "d" and all(port in old for port in self.ports):
            left, right = (old[p] for p in self.ports)
            event = _event_record(self.ports, old, self._atoms, self.env["collision_image"])
            node = len(self.nodes)
            self.nodes.append({"atoms": self._atoms(left) | self._atoms(right),
                               "children": (left, right), "parent": None, "event": event})
            self.nodes[left]["parent"] = node
            self.nodes[right]["parent"] = node
            next_packets[self.env["collision_image"]] = node
            self.latest = node
        self.packets = next_packets

    def register(self, selected: tuple[int, ...]) -> bool:
        self.selected_node = next((i for i, node in enumerate(self.nodes)
                                   if node["children"] is not None and node["atoms"] == set(selected)), None)
        return self.selected_node is not None

    def guard(self, letter: str) -> str:
        return _guard(self.packets, self._atoms(self.selected_node), letter, self.ports, self._atoms)

    def latest_block(self) -> list[int] | None:
        return sorted(self._atoms(self.latest)) if self.latest is not None else None

    def report(self) -> dict:
        selected = self.selected_node
        ancestors = []
        current = selected
        while self.nodes[current]["parent"] is not None:
            parent = self.nodes[current]["parent"]
            left, right = self.nodes[parent]["children"]
            ancestors.append(_absorption(self.nodes[parent]["event"], 0 if current == left else 1))
            current = parent
        return {"partition": _placed(self.packets, self._atoms),
                "selected_block": sorted(self._atoms(selected)),
                "origin": self.nodes[selected]["event"], "absorptions": ancestors,
                "carrier": _carrier(self.packets, self._atoms(selected), self._atoms)}


class _UFERoute:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.ports = tuple(env["kernel_ports"])
        self.enumeration = list(env["ufe_enumeration"])
        self.parent = {atom: atom for atom in self.enumeration}
        self.placement = {q: atom for atom, q in zip(env["omega"], env["iota"], strict=True)}
        # Only ordered union operands are retained; report/event snapshots are
        # reconstructed from the history, not copied into this representation.
        self.history: list[list[int]] = []
        self.selected_index: int | None = None

    def _root(self, atom: int) -> int:
        while self.parent[atom] != atom:
            atom = self.parent[atom]
        return atom

    def _class(self, atom: int) -> set[int]:
        root = self._root(atom)
        return {a for a in self.enumeration if self._root(a) == root}

    def push(self, letter: str) -> None:
        old = self.placement
        mapping = (lambda q: (q + 1) % self.env["q_size"]) if letter == "p" else self.env["d"].__getitem__
        next_placement = {mapping(q): atom for q, atom in old.items()}
        if letter == "d" and all(port in old for port in self.ports):
            blocks = [self._class(old[port]) for port in self.ports]
            picks = [next(a for a in self.enumeration if a in block) for block in blocks]
            self.history.append(picks)
            self.parent[self._root(picks[1])] = self._root(picks[0])
            next_placement[self.env["collision_image"]] = picks[0]
        self.placement = next_placement

    def register(self, selected: tuple[int, ...]) -> bool:
        self.selected_index = next((i for i, event in enumerate(self._events())
                                    if event["fresh_block"] == list(selected)), None)
        return self.selected_index is not None

    def guard(self, letter: str) -> str:
        selected = set(self._events()[self.selected_index]["fresh_block"])
        return _guard(self.placement, selected, letter, self.ports, self._class)

    def union_history(self) -> list[list[int]]:
        return [list(pair) for pair in self.history]

    def _events(self) -> list[dict]:
        parent = {atom: atom for atom in self.enumeration}

        def root(atom: int) -> int:
            while parent[atom] != atom:
                atom = parent[atom]
            return atom

        events = []
        for left, right in self.history:
            classes = [sorted(a for a in self.enumeration if root(a) == root(atom))
                       for atom in (left, right)]
            events.append({"letter": "d", "ports": list(self.ports),
                           "input_blocks": classes,
                           "collision_image": self.env["collision_image"],
                           "fresh_block": sorted(set(classes[0]) | set(classes[1]))})
            parent[root(right)] = root(left)
        return events

    def report(self) -> dict:
        events = self._events()
        origin = events[self.selected_index]
        selected = set(origin["fresh_block"])
        absorptions = []
        for event in events[self.selected_index+1:]:
            for i, block in enumerate(event["input_blocks"]):
                if selected <= set(block):
                    absorptions.append(_absorption(event, i))
                    break
        return {"partition": _placed(self.placement, self._class),
                "selected_block": sorted(selected), "origin": origin,
                "absorptions": absorptions,
                "carrier": _carrier(self.placement, selected, self._class)}
