"""Offline replay CLI; all trust pins must come from outside the candidate file."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib.resources import files
from pathlib import Path

from .replay import ContractError, canonical_bytes, digest, replay

MAX_INPUT_BYTES = 4_000_000


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON key: {key[:80]}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContractError(f"Non-finite JSON constant: {value}")


def _read_input(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ContractError("Input exceeds size limit")
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractError("Invalid input JSON") from exc
    if not isinstance(data, dict) or set(data) not in (
        {"goal", "ledger", "checkpoints"},
        {"goal", "ledger", "checkpoints", "compaction_view"},
    ):
        raise ContractError("Unexpected replay input fields")
    checkpoints = data["checkpoints"]
    if not isinstance(checkpoints, dict) or set(checkpoints) != {
        "previous_sequence", "current_sequence", "previous_at", "current_at",
    }:
        raise ContractError("Malformed checkpoints")
    return data


def _plain_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checker_source_sha256() -> str:
    """Bind every Python source file that can affect the CLI replay path."""
    source_dir = Path(__file__).parent
    members = sorted(source_dir.glob("*.py"), key=lambda item: item.name)
    manifest = b"aigc-lite.long-horizon-recovery.v0.checker\n"
    for path in members:
        manifest += path.name.encode("utf-8") + b"\0" + hashlib.sha256(
            path.read_bytes()
        ).digest()
    return hashlib.sha256(manifest).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--goal-sha256", required=True)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--checkpoints-sha256", required=True)
    parser.add_argument("--checker-sha256", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--expected-result-sha256")
    args = parser.parse_args(argv)
    try:
        contract_path = Path(str(files(__package__).joinpath("contract_v0.json")))
        if _checker_source_sha256() != args.checker_sha256:
            raise ContractError("Checker source digest mismatch")
        if _plain_sha256(contract_path) != args.contract_sha256:
            raise ContractError("Contract source digest mismatch")
        data = _read_input(args.input)
        if digest(data["checkpoints"]) != args.checkpoints_sha256:
            raise ContractError("Pinned checkpoints digest mismatch")
        output = replay(
            data["goal"], data["ledger"],
            expected_goal_sha256=args.goal_sha256,
            expected_ledger_sha256=args.ledger_sha256,
            expected_checkpoints_sha256=args.checkpoints_sha256,
            compaction_view=data.get("compaction_view"),
            **data["checkpoints"],
        )
        if args.expected_result_sha256 and digest(output) != args.expected_result_sha256:
            raise ContractError("Result digest mismatch")
        sys.stdout.buffer.write(canonical_bytes(output) + b"\n")
        return 0
    except (ContractError, OSError) as exc:
        print(f"Long-horizon replay rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
