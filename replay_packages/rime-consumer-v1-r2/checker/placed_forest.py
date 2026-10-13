"""Route 2: Placed Fusion-Forest (FOREST_MODEL.md).

State: dict {position: tree_root}. A tree has leaves (atoms) and internal
nodes (kernel fusion events). Kernel-port collisions during d create internal
nodes with children at event-time positions k_0, k_1, letter d, target c.
Non-kernel collisions just merge leaf sets into a single root.

Projection (the placed partition) is: each root's leaf set.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass, field

from utils import block_equal


def make_leaf(atom: int) -> dict:
    return {"type": "leaf", "atom": atom}


def make_internal(letter: str, k_0: int, k_1: int, c: int, left: dict, right: dict) -> dict:
    return {"type": "internal", "letter": letter, "k_0": k_0, "k_1": k_1, "c": c, "left": left, "right": right}


def leaf_set(node: dict) -> list[int]:
    if node["type"] == "leaf":
        return [node["atom"]]
    return sorted(set(leaf_set(node["left"])) | set(leaf_set(node["right"])))


def find_v_F(node: dict, F: list[int]) -> dict | None:
    """Return the (unique) internal node v_F with leaf set == F, or None."""
    if node["type"] == "leaf":
        return None
    if block_equal(leaf_set(node), F):
        return node
    left_v = find_v_F(node["left"], F)
    if left_v is not None:
        return left_v
    return find_v_F(node["right"], F)


def ancestors_strict(node: dict, target: dict) -> list[dict]:
    """Return strict ancestors of target inside node, inner-to-outer."""
    result = []
    if node is target or node["type"] == "leaf":
        return result
    def walk(n):
        if n["type"] == "leaf":
            return False
        if n["left"] is target or n["right"] is target:
            return True
        return walk(n["left"]) or walk(n["right"])
    def collect(n):
        if n is target or n["type"] == "leaf":
            return
        if walk(n["left"]) or walk(n["right"]):
            result.append(n)
            return
        collect(n["left"])
        collect(n["right"])
    collect(node)
    return result


@dataclass
class PlacedForestState:
    env: dict
    F: list[int]
    state: dict[int, dict] = field(default_factory=dict)  # pos -> root
    fusion_events: list[dict] = field(default_factory=list)
    origin_event_index: int | None = None
    absorptions: list[dict] = field(default_factory=list)
    step_index: int = 0
    v_F_cache: dict | None = None

    def __post_init__(self):
        omega = self.env["omega"]
        iota = self.env["iota"]
        self.state = {}
        for atom, pos in zip(omega, iota, strict=True):
            self.state[pos] = make_leaf(atom)

    def partition(self) -> dict[int, list[int]]:
        return {q: leaf_set(r) for q, r in self.state.items()}

    def carrier(self) -> tuple[int, list[int]] | None:
        if self.origin_event_index is None:
            return None
        for q, root in self.state.items():
            if all(atom in leaf_set(root) for atom in self.F):
                return (q, leaf_set(root))
        return None

    def apply_letter(self, letter: str) -> dict:
        d = self.env["d"]
        collision_image = self.env["collision_image"]
        k_0, k_1 = self.env["kernel_ports"]
        result = {"letter": letter, "fusion_event": None, "absorption_recorded": False}

        if letter == "d":
            # Check kernel collision
            b_0 = self.state.get(k_0)
            b_1 = self.state.get(k_1)
            kernel_collision = b_0 is not None and b_1 is not None

            if kernel_collision:
                # Record fusion event with pre-move data
                fresh = sorted(set(leaf_set(b_0)) | set(leaf_set(b_1)))
                ev_idx = len(self.fusion_events)
                self.fusion_events.append({
                    "index": ev_idx,
                    "k_0": k_0, "k_1": k_1,
                    "b_0": leaf_set(b_0), "b_1": leaf_set(b_1),
                    "c": collision_image, "fresh_block": fresh,
                })
                # Create new internal node with children at event-time positions
                # k_0 child = b_0 (the root at k_0), k_1 child = b_1
                # Remove the two original roots; add the new internal node at c
                new_node = make_internal("d", k_0, k_1, collision_image, b_0, b_1)
                # Check registration
                if self.origin_event_index is None and block_equal(fresh, self.F):
                    self.origin_event_index = ev_idx
                # Check absorption: does pre-move root contain F?
                f_in_b0 = all(atom in leaf_set(b_0) for atom in self.F)
                f_in_b1 = all(atom in leaf_set(b_1) for atom in self.F)
                if f_in_b0 or f_in_b1:
                    if f_in_b0:
                        carrier_port, carrier_before = k_0, leaf_set(b_0)
                        other_port, other_block = k_1, leaf_set(b_1)
                    else:
                        carrier_port, carrier_before = k_1, leaf_set(b_1)
                        other_port, other_block = k_0, leaf_set(b_0)
                    self.absorptions.append({
                        "event_index": ev_idx,
                        "carrier_port": carrier_port,
                        "carrier_before": carrier_before,
                        "other_port": other_port,
                        "other_block": other_block,
                        "c": collision_image,
                        "carrier_after": sorted(set(carrier_before) | set(other_block)),
                    })
                    result["absorption_recorded"] = True
                # Now move all roots: kernel roots are removed (replaced by new node at c)
                new_state: dict[int, dict] = {}
                for q, root in self.state.items():
                    if q in (k_0, k_1):
                        continue  # removed; will be replaced by new_node
                    new_q = d[q]
                    if new_q in new_state:
                        # merge leaf sets, no new internal node
                        merged = make_internal("d", k_0, k_1, collision_image,
                                               new_state[new_q], root)
                        # but this is a "fake" internal node without origin event
                        # The spec says no other collision is possible in raw d (with kernel d),
                        # but in Carry(d) other collisions can occur. Use a simpler merge:
                        # replace the two roots with a single root whose leaf set is union
                        # The internal structure here is just for tracking; the output is the leaf set.
                        # Use a synthetic internal node to track the merge:
                        new_state[new_q] = merged
                    else:
                        new_state[new_q] = root
                # Insert the new kernel node at collision_image if not already there
                if collision_image in new_state:
                    # merge with existing root at collision_image
                    new_state[collision_image] = make_internal(
                        "d", k_0, k_1, collision_image,
                        new_node, new_state[collision_image])
                else:
                    new_state[collision_image] = new_node
                self.state = new_state
                result["fusion_event"] = self.fusion_events[-1]
            else:
                # No kernel collision; just move all roots
                new_state = {}
                for q, root in self.state.items():
                    new_q = d[q]
                    if new_q in new_state:
                        # Non-kernel collision: merge leaf sets (no new internal node)
                        # Use the existing root and merge the leaves
                        existing = new_state[new_q]
                        if existing["type"] == "leaf" and root["type"] == "leaf":
                            new_state[new_q] = make_internal("d", k_0, k_1, collision_image,
                                                              existing, root)
                        else:
                            new_state[new_q] = make_internal("d", k_0, k_1, collision_image,
                                                              existing, root)
                    else:
                        new_state[new_q] = root
                self.state = new_state

        elif letter == "p":
            new_state = {}
            for q, root in self.state.items():
                new_q = (q + 1) % self.env["q_size"]
                if new_q in new_state:
                    existing = new_state[new_q]
                    new_state[new_q] = make_internal("d", k_0, k_1, self.env["collision_image"],
                                                     existing, root)
                else:
                    new_state[new_q] = root
            self.state = new_state

        # Invalidate v_F cache
        self.v_F_cache = None
        self.step_index += 1
        return result

    def apply_command(self, cmd: dict) -> dict:
        kind = cmd["kind"]
        letter = cmd["letter"]
        result = {"command": cmd, "applied": False, "reason": None}
        if kind == "Carry":
            self.apply_letter(letter)
            result["applied"] = True
        elif kind == "Absorb":
            if letter != "d":
                result["reason"] = "Absorb(p) never enabled"
                return result
            car = self.carrier()
            if car is None:
                result["reason"] = "F not registered, Absorb undefined"
                return result
            q_carrier, _ = car
            d = self.env["d"]
            g = set()
            for s in self.state.keys():
                if d[s] == d[q_carrier]:
                    g.add(s)
            if len(g) != 2 or q_carrier not in g:
                result["reason"] = f"Absorb guard fails: G = {sorted(g)}"
                return result
            self.apply_letter(letter)
            result["applied"] = True
        return result

    def to_summary(self) -> dict:
        car = self.carrier()
        return {
            "step": self.step_index,
            "partition": {str(k): list(v) for k, v in sorted(self.partition().items())},
            "origin_event_index": self.origin_event_index,
            "absorptions": list(self.absorptions),
            "carrier": {"pos": car[0], "block": car[1]} if car else None,
            "fusion_event_count": len(self.fusion_events),
        }
