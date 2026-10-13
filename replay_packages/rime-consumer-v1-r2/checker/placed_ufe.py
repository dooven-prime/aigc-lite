"""Route 3: Placed Forward-UFE (UFE_ADAPTER.md).

State: (u, ell) where u is abstract union-find with ordered union list, ell
maps classes to positions. Apply d: pick atoms by least e-identifier from
classes at kernel ports, append ordered pair, merge classes. Apply p: move
all class placements by 1. Non-kernel collisions during d (e.g., in Carry)
merge classes without recording a union event.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass, field

from utils import block_equal


class UFEState:
    """Abstract union-find with ordered union list."""

    def __init__(self, omega: list[int], ufe_enum: list[int], n: int):
        # Each atom starts in its own class
        self.parent = {a: a for a in omega}
        self.rank = {a: 0 for a in omega}
        self.atom_of = {a: a for a in omega}  # class -> canonical atom
        self.ordered_unions: list[tuple[int, int]] = []  # (e(pick(B_0)), e(pick(B_1)))
        self.omega = list(omega)
        self.ufe_enum = {atom: idx for idx, atom in enumerate(ufe_enum)}
        self.inv_enum = {idx: atom for idx, atom in enumerate(ufe_enum)}
        self.n = n

    def find(self, x: int) -> int:
        # Path compression
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, x: int, y: int):
        """Union the classes of x and y."""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def classes(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for a in self.omega:
            r = self.find(a)
            result.setdefault(r, []).append(a)
        return {tuple(sorted(v)): k for k, v in result.items()}

    def class_of(self, x: int) -> int:
        return self.find(x)

    def pick(self, B: list[int]) -> int:
        """Return the atom in B with the least e-identifier."""
        return min(B, key=lambda a: self.ufe_enum[a])

    def append_ordered_union(self, B_0: list[int], B_1: list[int]):
        x = self.ufe_enum[self.pick(B_0)]
        y = self.ufe_enum[self.pick(B_1)]
        self.ordered_unions.append((x, y))


@dataclass
class PlacedUFEState:
    env: dict
    F: list[int]
    ufe: UFEState | None = None
    ell: dict[int, int] = field(default_factory=dict)  # class_id -> position
    fusion_events: list[dict] = field(default_factory=list)
    origin_event_index: int | None = None
    absorptions: list[dict] = field(default_factory=list)
    step_index: int = 0

    def __post_init__(self):
        omega = self.env["omega"]
        iota = self.env["iota"]
        ufe_enum = self.env["ufe_enumeration"]
        n = self.env["q_size"]
        self.ufe = UFEState(omega, ufe_enum, n)
        # Initial: each atom at its iota position
        for atom, pos in zip(omega, iota, strict=True):
            # Each atom is its own class
            self.ell[atom] = pos  # class_of(atom) == atom initially

    def partition(self) -> dict[int, list[int]]:
        # For each class, find its current position. The class's atoms are at the same position
        # (since p moves all classes uniformly and d only moves atoms from the SAME class together).
        # But actually, after d, the class is at c, but other classes are at d(their positions).
        # So the partition is: position -> atoms at that position
        pos_atoms: dict[int, list[int]] = {}
        for atom in self.env["omega"]:
            cls = self.ufe.find(atom)
            pos = self.ell[cls]
            pos_atoms.setdefault(pos, []).append(atom)
        return {p: sorted(v) for p, v in pos_atoms.items()}

    def carrier(self) -> tuple[int, list[int]] | None:
        if self.origin_event_index is None:
            return None
        # Find the class containing all of F
        for atom in self.F:
            cls = self.ufe.find(atom)
        # All atoms in F should be in the same class (since they were merged at registration)
        f_class_atoms = []
        for atom in self.env["omega"]:
            if self.ufe.find(atom) == cls:
                f_class_atoms.append(atom)
        if not all(a in f_class_atoms for a in self.F):
            return None
        return (self.ell[cls], sorted(f_class_atoms))

    def apply_letter(self, letter: str) -> dict:
        d = self.env["d"]
        collision_image = self.env["collision_image"]
        k_0, k_1 = self.env["kernel_ports"]
        result = {"letter": letter, "fusion_event": None, "absorption_recorded": False}

        if letter == "d":
            old_ell = dict(self.ell)
            # Check kernel collision
            # Find classes at k_0 and k_1
            cls_at_k0 = None
            cls_at_k1 = None
            for atom in self.env["omega"]:
                if self.ell[self.ufe.find(atom)] == k_0:
                    cls_at_k0 = self.ufe.find(atom)
                    break
            for atom in self.env["omega"]:
                if self.ell[self.ufe.find(atom)] == k_1:
                    cls_at_k1 = self.ufe.find(atom)
                    break

            kernel_collision = cls_at_k0 is not None and cls_at_k1 is not None and cls_at_k0 != cls_at_k1

            if kernel_collision:
                # Get B_0 and B_1 (atoms in the classes)
                B_0 = sorted([a for a in self.env["omega"] if self.ufe.find(a) == cls_at_k0])
                B_1 = sorted([a for a in self.env["omega"] if self.ufe.find(a) == cls_at_k1])
                fresh = sorted(set(B_0) | set(B_1))
                ev_idx = len(self.fusion_events)
                self.fusion_events.append({
                    "index": ev_idx,
                    "k_0": k_0, "k_1": k_1,
                    "b_0": B_0, "b_1": B_1,
                    "c": collision_image, "fresh_block": fresh,
                })
                # Append ordered union
                self.ufe.append_ordered_union(B_0, B_1)
                # Merge the classes
                self.ufe.union(B_0[0], B_1[0])
                # Check registration
                if self.origin_event_index is None and block_equal(fresh, self.F):
                    self.origin_event_index = ev_idx
                # Check absorption
                f_in_b0 = all(atom in B_0 for atom in self.F)
                f_in_b1 = all(atom in B_1 for atom in self.F)
                if f_in_b0 or f_in_b1:
                    if f_in_b0:
                        carrier_port, carrier_before = k_0, B_0
                        other_port, other_block = k_1, B_1
                    else:
                        carrier_port, carrier_before = k_1, B_1
                        other_port, other_block = k_0, B_0
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

            # On the declared rank-(n-1) action, the only possible collision
            # is the ordered kernel pair handled above. Re-key placements
            # after union: union-find may choose either former class as root.
            new_ell: dict[int, int] = {}
            for old_root, old_pos in old_ell.items():
                new_root = self.ufe.find(old_root)
                new_pos = d[old_pos]
                if new_root in new_ell and new_ell[new_root] != new_pos:
                    raise ValueError("union parents have inconsistent target placements")
                if new_root not in new_ell and new_pos in new_ell.values():
                    raise ValueError("undeclared non-kernel collision")
                new_ell[new_root] = new_pos
            self.ell = new_ell

        elif letter == "p":
            for cls in list(self.ell.keys()):
                self.ell[cls] = (self.ell[cls] + 1) % self.env["q_size"]

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
            for s in range(self.env["q_size"]):
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
