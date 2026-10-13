"""Route 1: Log Oracle (CONSUMER_SPEC.md).

Replay the word letter-by-letter. Record fusion events when both kernel
positions are occupied. For each d step with both kernel ports occupied,
record (b_0, b_1, F_e = b_0 ∪ b_1, c). Apply d or p to the placed partition.
After registration (F_e == F), track origin and absorptions.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass, field

from utils import block_equal, block_union


@dataclass
class FusionEvent:
    index: int
    k_0: int
    k_1: int
    b_0: list[int]   # block at k_0 before move
    b_1: list[int]   # block at k_1 before move
    c: int           # collision_image
    fresh_block: list[int]   # b_0 ∪ b_1

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "k_0": self.k_0,
            "k_1": self.k_1,
            "b_0": list(self.b_0),
            "b_1": list(self.b_1),
            "c": self.c,
            "fresh_block": list(self.fresh_block),
        }


@dataclass
class Absorption:
    event_index: int
    carrier_port: int
    carrier_before: list[int]
    other_port: int
    other_block: list[int]
    c: int
    carrier_after: list[int]

    def to_dict(self) -> dict:
        return {
            "event_index": self.event_index,
            "carrier_port": self.carrier_port,
            "carrier_before": list(self.carrier_before),
            "other_port": self.other_port,
            "other_block": list(self.other_block),
            "c": self.c,
            "carrier_after": list(self.carrier_after),
        }


@dataclass
class LogOracleState:
    env: dict
    F: list[int]
    partition: dict[int, list[int]] = field(default_factory=dict)
    fusion_events: list[FusionEvent] = field(default_factory=list)
    origin_event_index: int | None = None
    absorptions: list[Absorption] = field(default_factory=list)
    step_index: int = 0

    def __post_init__(self):
        # Initial: each atom at its iota position
        omega = self.env["omega"]
        iota = self.env["iota"]
        self.partition = {}
        for atom, pos in zip(omega, iota, strict=True):
            self.partition.setdefault(pos, []).append(atom)
        # sort blocks
        for q in self.partition:
            self.partition[q] = sorted(self.partition[q])

    def carrier(self) -> tuple[int, list[int]] | None:
        """Return (position, block) of the packet containing F, if registered."""
        if self.origin_event_index is None:
            return None
        for q, blk in self.partition.items():
            if all(atom in blk for atom in self.F):
                return (q, sorted(blk))
        return None

    def apply_letter(self, letter: str) -> dict:
        """Returns event info dict for this letter (fusion event or empty)."""
        d = self.env["d"]
        collision_image = self.env["collision_image"]
        k_0, k_1 = self.env["kernel_ports"]
        result = {"letter": letter, "fusion_event": None, "absorption_recorded": False}

        if letter == "d":
            # Check if both kernel positions are occupied (pre-move)
            pre_partition = {k: list(v) for k, v in self.partition.items()}
            b_0 = pre_partition.get(k_0)
            b_1 = pre_partition.get(k_1)
            if b_0 and b_1:
                fresh = block_union(b_0, b_1)
                ev = FusionEvent(
                    index=len(self.fusion_events),
                    k_0=k_0, k_1=k_1,
                    b_0=sorted(b_0), b_1=sorted(b_1),
                    c=collision_image, fresh_block=fresh,
                )
                self.fusion_events.append(ev)
                result["fusion_event"] = ev.to_dict()
                # Registration check
                if self.origin_event_index is None and block_equal(fresh, self.F):
                    self.origin_event_index = ev.index
                # Absorption check: does carrier (pre-move) contain F?
                f_in_b0 = all(atom in b_0 for atom in self.F)
                f_in_b1 = all(atom in b_1 for atom in self.F)
                if f_in_b0 or f_in_b1:
                    # The carrier is the side containing F (or its pre-merge ancestor)
                    if f_in_b0:
                        carrier_port, carrier_before = k_0, sorted(b_0)
                        other_port, other_block = k_1, sorted(b_1)
                    else:
                        carrier_port, carrier_before = k_1, sorted(b_1)
                        other_port, other_block = k_0, sorted(b_0)
                    # We record: other_block (the partner packet) and its port; the carrier_after
                    # is carrier_before ∪ other_block (the merged fresh_block is the post-fusion block,
                    # but here "carrier_after" is the carrier's block AFTER the fusion event, which
                    # is fresh_block IF the carrier was the side containing F; otherwise it stays).
                    # Per CONSUMER_SPEC: the carrier's block grows to fresh_block only on Absorb.
                    # For natural fusion events, the carrier's block becomes fresh_block.
                    # For Carry(d), no absorption is recorded.
                    # We only record absorption here if the carrier grows (i.e., this is a natural
                    # fusion of the carrier, or it's an Absorb call).
                    # The CALL distinction is handled in apply_command; here we record the fusion.
                    self.absorptions.append(Absorption(
                        event_index=ev.index,
                        carrier_port=carrier_port,
                        carrier_before=carrier_before,
                        other_port=other_port,
                        other_block=other_block,
                        c=collision_image,
                        carrier_after=fresh,  # carrier after natural fusion
                    ))
                    result["absorption_recorded"] = True
            # Apply d: for each occupied (q, block), move to d[q]. Merge if collide.
            new_partition: dict[int, list[int]] = {}
            for q, blk in self.partition.items():
                if q >= len(d):
                    new_q = q
                else:
                    new_q = d[q]
                if new_q in new_partition:
                    new_partition[new_q] = sorted(set(new_partition[new_q]) | set(blk))
                else:
                    new_partition[new_q] = sorted(blk)
            self.partition = new_partition

        elif letter == "p":
            new_partition: dict[int, list[int]] = {}
            for q, blk in self.partition.items():
                new_q = (q + 1) % self.env["q_size"]
                if new_q in new_partition:
                    new_partition[new_q] = sorted(set(new_partition[new_q]) | set(blk))
                else:
                    new_partition[new_q] = sorted(blk)
            self.partition = new_partition

        self.step_index += 1
        return result

    def apply_command(self, cmd: dict) -> dict:
        """Apply a Carry or Absorb command."""
        kind = cmd["kind"]
        letter = cmd["letter"]
        result = {"command": cmd, "applied": False, "reason": None}

        if kind == "Carry":
            # Same as applying the letter; no special absorption recording
            self.apply_letter(letter)
            result["applied"] = True
        elif kind == "Absorb":
            # Absorb only valid for d
            if letter != "d":
                result["reason"] = "Absorb(p) never enabled"
                return result
            car = self.carrier()
            if car is None:
                result["reason"] = "F not registered, Absorb undefined"
                return result
            q_carrier, _ = car
            d = self.env["d"]
            # Compute G_d(P, q_carrier)
            g = set()
            for s in self.partition.keys():
                if s < len(d) and d[s] == d[q_carrier]:
                    g.add(s)
            if g != {q_carrier, } and not (len(g) == 2 and q_carrier in g):
                # Absorb enabled iff G = {q, s} for some s != q
                if len(g) != 2 or q_carrier not in g:
                    result["reason"] = f"Absorb guard fails: G_d(P,{q_carrier}) = {sorted(g)}"
                    return result
            if len(g) != 2:
                result["reason"] = f"Absorb requires |G|=2, got {len(g)}"
                return result
            # Apply d (this records fusion event + absorption)
            self.apply_letter(letter)
            result["applied"] = True

        return result

    def to_summary(self) -> dict:
        car = self.carrier()
        return {
            "step": self.step_index,
            "partition": {str(k): list(v) for k, v in sorted(self.partition.items())},
            "origin_event_index": self.origin_event_index,
            "absorptions": [a.to_dict() for a in self.absorptions],
            "carrier": {"pos": car[0], "block": car[1]} if car else None,
            "fusion_event_count": len(self.fusion_events),
        }
