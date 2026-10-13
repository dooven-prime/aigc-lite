"""Shared utilities for the 3-route RIME consumer replay checker.

Standard library only. No external dependencies. No network. No database.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# ---------- block algebra ----------

def block_union(a: list[int], b: list[int]) -> list[int]:
    return sorted(set(a) | set(b))


def block_intersection(a: list[int], b: list[int]) -> list[int]:
    return sorted(set(a) & set(b))


def block_contains(block: list[int], atom: int) -> bool:
    return atom in block


def block_equal(a: list[int], b: list[int]) -> bool:
    return sorted(a) == sorted(b)


# ---------- partition canonical form ----------

def canonical_partition(partition: dict[int, list[int]]) -> list[tuple[int, tuple[int, ...]]]:
    """Drop empty positions; sort by position; emit blocks as sorted tuples."""
    out = []
    for q in sorted(partition.keys()):
        blk = sorted(partition[q])
        if not blk:
            continue
        out.append((q, tuple(blk)))
    return out


def partitions_agree(a: dict[int, list[int]], b: dict[int, list[int]]) -> bool:
    return canonical_partition(a) == canonical_partition(b)


def diff_partitions(a: dict[int, list[int]], b: dict[int, list[int]]) -> str | None:
    ca = canonical_partition(a)
    cb = canonical_partition(b)
    if ca == cb:
        return None
    a_set = dict(ca)
    b_set = dict(cb)
    keys = sorted(set(a_set) | set(b_set))
    diffs = []
    for k in keys:
        if a_set.get(k) != b_set.get(k):
            diffs.append(f"pos {k}: A={a_set.get(k)} B={b_set.get(k)}")
            if len(diffs) >= 3:
                break
    return "; ".join(diffs)


# ---------- environment helpers ----------

VALID_LETTERS = {"p", "d"}
VALID_COMMANDS = {"Carry", "Absorb"}


def check_environment(env: dict) -> tuple[bool, str | None]:
    """Returns (ok, error_message)."""
    if not isinstance(env, dict):
        return False, "environment must be object"
    required = ["q_size", "p", "d", "omega", "iota", "ufe_enumeration", "kernel_ports", "collision_image"]
    for k in required:
        if k not in env:
            return False, f"missing environment field: {k}"
    if type(env["q_size"]) is not int or env["q_size"] < 2 or env["q_size"] > 16:
        return False, f"q_size {env['q_size']} out of bounds [2, 16]"
    n = env["q_size"]
    if env["p"] != "successor_mod_n":
        return False, f"unsupported p variant: {env['p']}"
    if not isinstance(env["d"], list) or len(env["d"]) != n:
        return False, "d must be list of length q_size"
    d = env["d"]
    if any(type(x) is not int or not 0 <= x < n for x in d):
        return False, "d targets must be positions in Q"
    if len(set(d)) != n - 1:
        return False, "d must have rank q_size-1"
    if not isinstance(env["omega"], list) or not 2 <= len(env["omega"]) <= n:
        return False, "omega size must be in [2, q_size]"
    omega = env["omega"]
    if any(type(x) is not int for x in omega) or len(set(omega)) != len(omega):
        return False, "omega must contain distinct integer atoms"
    iota = env["iota"]
    if not isinstance(iota, list) or len(iota) != len(omega):
        return False, "iota length mismatch with omega"
    if any(type(x) is not int or not 0 <= x < n for x in iota) or len(set(iota)) != len(iota):
        return False, "iota must inject omega into Q"
    enumeration = env["ufe_enumeration"]
    if (not isinstance(enumeration, list) or len(enumeration) != len(omega)
            or any(type(x) is not int for x in enumeration)
            or set(enumeration) != set(omega)):
        return False, "ufe_enumeration must be a permutation of omega"
    if not isinstance(env["kernel_ports"], list) or len(env["kernel_ports"]) != 2:
        return False, "kernel_ports must be a list of two distinct positions"
    ports = env["kernel_ports"]
    if any(type(x) is not int or not 0 <= x < n for x in ports) or ports[0] == ports[1]:
        return False, "kernel_ports must be distinct positions in Q"
    if d[ports[0]] != d[ports[1]] or any(d[i] == d[ports[0]] for i in range(n) if i not in ports):
        return False, "kernel_ports must be d's sole collision pair"
    if type(env["collision_image"]) is not int or env["collision_image"] != d[ports[0]]:
        return False, "collision_image must equal d at both kernel ports"
    return True, None


def check_witness_shape(w: dict) -> tuple[bool, str | None]:
    if not isinstance(w, dict):
        return False, "witness must be object"
    if "environment" not in w or "prefix" not in w or "selected_block" not in w or "commands" not in w:
        return False, "missing witness field"
    env_ok, env_err = check_environment(w["environment"])
    if not env_ok:
        return False, f"environment: {env_err}"
    if not isinstance(w["prefix"], str) or len(w["prefix"]) > 128:
        return False, "prefix must be string"
    for c in w["prefix"]:
        if c not in VALID_LETTERS:
            return False, f"invalid letter in prefix: {c!r}"
    if not isinstance(w["commands"], list) or len(w["commands"]) > 128:
        return False, "commands must be list"
    for cmd in w["commands"]:
        if not isinstance(cmd, dict) or cmd.get("kind") not in VALID_COMMANDS:
            return False, f"invalid command kind: {cmd}"
        if cmd.get("letter") not in VALID_LETTERS:
            return False, f"invalid command letter: {cmd}"
    selected = w["selected_block"]
    if not _block_shape(selected) or not set(selected) <= set(w["environment"]["omega"]):
        return False, "selected_block must be a sorted nonempty subset of omega"
    reports = w.get("candidate_reports")
    if reports is not None:
        if not isinstance(reports, list) or len(reports) != 1 + len(w["commands"]):
            return False, "candidate_reports must have 1 + len(commands) entries"
        for i, report in enumerate(reports):
            if not _report_shape(report):
                return False, f"candidate_reports[{i}] has invalid report shape"
    return True, None


def _block_shape(block: object) -> bool:
    return (isinstance(block, list) and bool(block)
            and all(type(x) is int for x in block)
            and block == sorted(set(block)))


def _packet_shape(packet: object) -> bool:
    return (isinstance(packet, dict) and set(packet) == {"q", "block"}
            and type(packet["q"]) is int and packet["q"] >= 0
            and _block_shape(packet["block"]))


def _report_shape(report: object) -> bool:
    if not isinstance(report, dict) or set(report) != {
        "partition", "selected_block", "origin", "absorptions", "carrier"
    }:
        return False
    partition = report["partition"]
    if not isinstance(partition, list) or not partition or not all(_packet_shape(x) for x in partition):
        return False
    if [x["q"] for x in partition] != sorted({x["q"] for x in partition}):
        return False
    if not _block_shape(report["selected_block"]) or not _packet_shape(report["carrier"]):
        return False
    origin = report["origin"]
    if not isinstance(origin, dict) or set(origin) != {
        "letter", "ports", "input_blocks", "collision_image", "fresh_block"
    } or origin["letter"] != "d":
        return False
    ports = origin["ports"]
    if (not isinstance(ports, list) or len(ports) != 2
            or any(type(x) is not int or x < 0 for x in ports) or ports[0] == ports[1]):
        return False
    blocks = origin["input_blocks"]
    if (not isinstance(blocks, list) or len(blocks) != 2
            or not all(_block_shape(x) for x in blocks)
            or type(origin["collision_image"]) is not int or origin["collision_image"] < 0
            or not _block_shape(origin["fresh_block"])):
        return False
    absorptions = report["absorptions"]
    if not isinstance(absorptions, list):
        return False
    for item in absorptions:
        if not isinstance(item, dict) or set(item) != {
            "letter", "carrier_port", "carrier_before", "other_port", "other_block",
            "collision_image", "carrier_after"
        } or item["letter"] != "d":
            return False
        if any(type(item[k]) is not int or item[k] < 0 for k in (
            "carrier_port", "other_port", "collision_image"
        )) or not all(_block_shape(item[k]) for k in (
            "carrier_before", "other_block", "carrier_after"
        )):
            return False
    return True


def command_guard(env: dict, partition: dict, carrier: dict | None, cmd: dict) -> str | None:
    if carrier is None:
        return "F not registered, command undefined"
    letter = cmd["letter"]
    if cmd["kind"] == "Absorb" and letter == "p":
        return "Absorb(p) never enabled"
    q = carrier["pos"]
    image = (lambda x: (x + 1) % env["q_size"]) if letter == "p" else (lambda x: env["d"][x])
    group = {int(s) for s in partition if image(int(s)) == image(q)}
    if cmd["kind"] == "Carry" and group != {q}:
        return f"Carry guard fails: G = {sorted(group)}"
    if cmd["kind"] == "Absorb" and (len(group) != 2 or q not in group):
        return f"Absorb guard fails: G = {sorted(group)}"
    return None


# ---------- SHA ----------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
