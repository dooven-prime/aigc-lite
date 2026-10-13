"""Small, separately implemented finite oracle for one consumer report property.

This module intentionally imports no r2 checker, transition, or report code.
It models occupied positions as a direct finite partition and checks only the
registered event origin, selected chronological absorptions, and carrier.
Agreement is not a proof of either implementation, organizational independence,
or the general consumer theorem.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class RejectedWitness(ValueError):
    """The finite source or requested continuation violates this contract."""


def _environment(witness: dict[str, Any]) -> tuple[int, list[int], dict[int, frozenset[int]], tuple[int, int]]:
    env = witness.get("environment")
    if not isinstance(env, dict):
        raise RejectedWitness("missing environment")
    n, d = env.get("q_size"), env.get("d")
    if not isinstance(n, int) or not 2 <= n <= 16:
        raise RejectedWitness("invalid finite Q size")
    if env.get("p") != "successor_mod_n" or not isinstance(d, list) or len(d) != n:
        raise RejectedWitness("invalid action")
    if any(not isinstance(y, int) or y < 0 or y >= n for y in d):
        raise RejectedWitness("invalid d image")
    fibres: dict[int, list[int]] = defaultdict(list)
    for position, image in enumerate(d):
        fibres[image].append(position)
    double = [tuple(source) for source in fibres.values() if len(source) == 2]
    if len(double) != 1 or any(len(source) > 2 for source in fibres.values()):
        raise RejectedWitness("d must have exactly one double fibre")
    ports = env.get("kernel_ports")
    if not isinstance(ports, list) or len(ports) != 2 or set(ports) != set(double[0]):
        raise RejectedWitness("ordered kernel ports do not identify the double fibre")
    if env.get("collision_image") != d[ports[0]]:
        raise RejectedWitness("collision image mismatch")
    atoms, positions = env.get("omega"), env.get("iota")
    if (not isinstance(atoms, list) or not isinstance(positions, list)
            or not 2 <= len(atoms) <= n or len(atoms) != len(positions)
            or len(set(atoms)) != len(atoms) or len(set(positions)) != len(positions)
            or any(not isinstance(x, int) for x in atoms + positions)
            or any(q < 0 or q >= n for q in positions)):
        raise RejectedWitness("invalid singleton seed")
    partition = {position: frozenset((atom,)) for atom, position in zip(atoms, positions, strict=True)}
    return n, d, partition, (ports[0], ports[1])


def replay(witness: dict[str, Any]) -> dict[str, Any]:
    """Return raw finite states and first refusal; never issue a qualification."""
    states: list[dict[str, Any]] = []
    try:
        n, d, partition, ports = _environment(witness)
        chosen = witness.get("selected_block")
        if (not isinstance(chosen, list) or not chosen or len(set(chosen)) != len(chosen)
                or any(atom not in witness["environment"]["omega"] for atom in chosen)):
            raise RejectedWitness("invalid selected block")
        selected = frozenset(chosen)
        word = witness.get("prefix")
        commands = witness.get("commands")
        if not isinstance(word, str) or any(letter not in "pd" for letter in word):
            raise RejectedWitness("invalid prefix letter")
        if (not isinstance(commands, list) or any(
            not isinstance(command, dict)
            or command.get("kind") not in ("Carry", "Absorb")
            or command.get("letter") not in ("p", "d") for command in commands
        )):
            raise RejectedWitness("invalid command")
    except (KeyError, TypeError, RejectedWitness) as exc:
        return {"verdict": "INVALID_REQUEST", "reason": str(exc), "states": states}

    origin: dict[str, Any] | None = None
    absorptions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def snapshot(label: str) -> None:
        carriers = [(q, block) for q, block in partition.items() if selected <= block]
        carrier = carriers[0] if len(carriers) == 1 and origin is not None else None
        states.append({
            "label": label,
            "partition": [{"q": q, "block": sorted(block)} for q, block in sorted(partition.items())],
            "origin": origin,
            "absorptions": list(absorptions),
            "carrier": None if carrier is None else {"q": carrier[0], "block": sorted(carrier[1])},
            "fusion_events": list(events),
        })

    def image(letter: str, q: int) -> int:
        return (q + 1) % n if letter == "p" else d[q]

    def advance(letter: str) -> None:
        nonlocal partition, origin
        before = partition
        left, right = ports
        if letter == "d" and left in before and right in before:
            merged = before[left] | before[right]
            event = {
                "ports": [left, right],
                "input_blocks": [sorted(before[left]), sorted(before[right])],
                "collision_image": d[left],
                "fresh_block": sorted(merged),
            }
            events.append(event)
            if origin is None and merged == selected:
                origin = event
            elif origin is not None:
                if selected <= before[left]:
                    q, other = left, right
                elif selected <= before[right]:
                    q, other = right, left
                else:
                    q = other = -1
                if q >= 0:
                    absorptions.append({
                        "carrier_port": q, "carrier_before": sorted(before[q]),
                        "other_port": other, "other_block": sorted(before[other]),
                        "collision_image": d[q], "carrier_after": sorted(merged),
                    })
        moved: dict[int, frozenset[int]] = {}
        for q, block in before.items():
            destination = image(letter, q)
            moved[destination] = moved.get(destination, frozenset()) | block
        partition = moved

    snapshot("initial")
    for letter in word:
        advance(letter)
        snapshot(f"after {letter}")

    for command in commands:
        if origin is None:
            return {"verdict": "FAIL", "reason": "selected event not registered", "states": states}
        carrier = states[-1]["carrier"]
        assert carrier is not None
        letter, kind = command["letter"], command["kind"]
        group = {q for q in partition if image(letter, q) == image(letter, carrier["q"])}
        if (kind == "Carry" and group != {carrier["q"]}) or (
            kind == "Absorb" and (letter == "p" or len(group) != 2)
        ):
            return {"verdict": "FAIL", "reason": f"{kind} guard fails", "states": states}
        advance(letter)
        snapshot(f"after {kind}({letter})")

    if origin is None:
        return {"verdict": "FAIL", "reason": "selected event not registered", "states": states}
    reports = witness.get("candidate_reports")
    if reports is not None:
        if not isinstance(reports, list) or len(reports) != len(commands) + 1:
            return {"verdict": "INVALID_REQUEST", "reason": "invalid report count", "states": states}
        for index, report in enumerate(reports):
            expected = states[len(word) + index]
            if not isinstance(report, dict):
                return {"verdict": "FAIL", "reason": f"report {index} is not an object", "states": states}
            for field in ("partition", "origin", "absorptions", "carrier"):
                if field == "origin":
                    value = {"letter": "d", **expected["origin"]}
                elif field == "absorptions":
                    value = [{"letter": "d", **item} for item in expected[field]]
                else:
                    value = expected[field]
                if report.get(field) != value:
                    return {"verdict": "FAIL", "reason": f"report {index} {field} mismatch", "states": states}
            if report.get("selected_block") != sorted(selected):
                return {"verdict": "FAIL", "reason": f"report {index} selected_block mismatch", "states": states}
    return {"verdict": "PASS", "reason": None, "states": states}
