"""Replay the pinned Fibre Criterion candidate without granting qualification.

The lock digest must be supplied from outside the candidate directory. This
program is an operator-side diagnostic, not an OS sandbox or a trusted signer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

CASE_ID = "fibre-criterion-v1"
DECLARATIONS = (
    "factors_iff_fibre_constant",
    "factor_unique",
    "hostile_pair_blocks_factorization",
    "mul_nonzero_iff_basis_survivor",
    "same_boolean_support",
    "different_composition",
    "support_does_not_determine_composition",
)
BOUND_FILES = frozenset(
    {".gitattributes", "lean-toolchain", "FibreCriterion.lean", "AxiomAudit.lean"}
)
AXIOM_LINE = re.compile(
    r"^'(?P<name>AgainstSilentPromotion\.[A-Za-z0-9_]+)' "
    r"(?:(?:depends on axioms: \[(?P<axioms>[^\]]*)\])|"
    r"(?:does not depend on any axioms))$"
)
VERSION_LINE = re.compile(
    r"Lean \(version (?P<version>[^,]+), [^,]+, commit "
    r"(?P<commit>[0-9a-f]{40}), Release\)"
)
MAX_OUTPUT_BYTES = 1_000_000


class CaseReplayError(ValueError):
    """The fixed input or replay result violates this case's contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_bounded(path: Path, limit: int = MAX_OUTPUT_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise CaseReplayError(f"missing, linked, or oversized input: {path.name}")
    return path.read_bytes()


def check_package(case_dir: Path, expected_lock_sha256: str) -> tuple[dict, dict[str, bytes]]:
    """Authenticate the lock against an out-of-band digest, then its files."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_lock_sha256):
        raise CaseReplayError("expected lock digest must be lowercase SHA-256")
    lock_bytes = _read_bounded(case_dir / "case-lock.json", 100_000)
    if _sha256(lock_bytes) != expected_lock_sha256:
        raise CaseReplayError("case lock differs from the out-of-band digest")
    try:
        lock = json.loads(lock_bytes)
    except (UnicodeError, ValueError) as exc:
        raise CaseReplayError("invalid case lock") from exc
    if (
        lock.get("schema") != "rime.formal-case-lock.v1"
        or lock.get("case_id") != CASE_ID
        or lock.get("status") != "CANDIDATE_NOT_RELEASED"
    ):
        raise CaseReplayError("wrong formal case lock")
    toolchain = lock.get("toolchain") or {}
    if toolchain.get("imports") != ["Init"] or not re.fullmatch(
        r"[0-9a-f]{40}", str(toolchain.get("lean_commit", ""))
    ):
        raise CaseReplayError("unexpected toolchain or import closure")
    expected_names = tuple(f"AgainstSilentPromotion.{name}" for name in DECLARATIONS)
    entries = lock.get("expected_theorem_axioms")
    if not isinstance(entries, list) or tuple(item.get("name") for item in entries) != expected_names:
        raise CaseReplayError("declaration inventory is not the frozen seven-theorem case")
    allowed = set(lock.get("allowed_standard_axioms") or [])
    if allowed != {"Classical.choice", "Quot.sound", "propext"}:
        raise CaseReplayError("unexpected allowed axiom policy")
    if any(not isinstance(item.get("axioms"), list) or set(item["axioms"]) - allowed for item in entries):
        raise CaseReplayError("invalid expected axiom list")
    files = lock.get("bound_files")
    if (
        not isinstance(files, list)
        or len(files) != len(BOUND_FILES)
        or {item.get("path") for item in files} != BOUND_FILES
    ):
        raise CaseReplayError("unexpected bound file inventory")
    raw_files: dict[str, bytes] = {}
    for item in files:
        name = item["path"]
        data = _read_bounded(case_dir / name)
        if len(data) != item.get("size") or _sha256(data) != item.get("sha256"):
            raise CaseReplayError(f"bound file changed: {name}")
        raw_files[name] = data
    # Both executable Lean inputs are exact-byte pinned; refuse adjacent Lean
    # modules that could change module resolution without entering the lock.
    if {path.name for path in case_dir.glob("*.lean")} != {
        "FibreCriterion.lean", "AxiomAudit.lean"
    }:
        raise CaseReplayError("unexpected Lean source beside the locked inputs")
    return lock, raw_files


def parse_axiom_audit(output: str, expected: list[dict]) -> dict[str, list[str]]:
    names = {item["name"] for item in expected}
    observed: dict[str, list[str]] = {}
    for line in output.splitlines():
        match = AXIOM_LINE.fullmatch(line.strip())
        if match is None:
            continue
        name = match.group("name")
        if name not in names or name in observed:
            raise CaseReplayError("unexpected or duplicate axiom report")
        observed[name] = sorted(
            part.strip() for part in (match.group("axioms") or "").split(",") if part.strip()
        )
    if set(observed) != names:
        raise CaseReplayError("axiom audit did not report every frozen declaration")
    for item in expected:
        if observed[item["name"]] != sorted(item["axioms"]):
            raise CaseReplayError(f"axiom set differs for {item['name']}")
    return observed


def _run(command: list[str], cwd: Path, env: dict[str, str], timeout: int) -> dict:
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise CaseReplayError("Lean replay exceeded its wall-clock limit") from exc
    if len(completed.stdout) > MAX_OUTPUT_BYTES or len(completed.stderr) > MAX_OUTPUT_BYTES:
        raise CaseReplayError("Lean output exceeded the receipt limit")
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout.decode("utf-8", errors="replace"),
        "stderr": completed.stderr.decode("utf-8", errors="replace"),
        "stdout_sha256": _sha256(completed.stdout),
        "stderr_sha256": _sha256(completed.stderr),
    }


def replay(
    case_dir: Path, lean_executable: Path, expected_lock_sha256: str, timeout: int = 120
) -> dict:
    lock, files = check_package(case_dir, expected_lock_sha256)
    if timeout < 1 or timeout > 600:
        raise CaseReplayError("timeout must be between 1 and 600 seconds")
    lean_executable = lean_executable.resolve(strict=True)
    if not lean_executable.is_file():
        raise CaseReplayError("Lean executable is not a file")
    with tempfile.TemporaryDirectory(prefix="fibre-criterion-") as name:
        scratch = Path(name)
        for source in ("FibreCriterion.lean", "AxiomAudit.lean"):
            (scratch / source).write_bytes(files[source])
        env = {
            "HOME": name,
            "USERPROFILE": name,
            "LEAN_PATH": name,
            "PATH": str(lean_executable.parent),
            "TEMP": name,
            "TMP": name,
        }
        if os.name == "nt":
            env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
        version = _run([str(lean_executable), "--version"], scratch, env, timeout)
        match = VERSION_LINE.search(version["stdout"])
        toolchain = lock["toolchain"]
        if version["exit_code"] or match is None or (
            match.group("version") != toolchain["lean_version"]
            or match.group("commit") != toolchain["lean_commit"]
        ):
            raise CaseReplayError("Lean release version or commit differs from the lock")
        compile_result = _run(
            [str(lean_executable), "--trust=0", "-o", "FibreCriterion.olean", "FibreCriterion.lean"],
            scratch,
            env,
            timeout,
        )
        if compile_result["exit_code"] != 0 or not (scratch / "FibreCriterion.olean").is_file():
            raise CaseReplayError("pinned Lean source did not compile")
        audit_result = _run(
            [str(lean_executable), "--trust=0", "AxiomAudit.lean"], scratch, env, timeout
        )
        if audit_result["exit_code"] != 0:
            raise CaseReplayError("axiom audit did not compile")
        observed = parse_axiom_audit(
            audit_result["stdout"], lock["expected_theorem_axioms"]
        )
    result = {
        "schema": "aigc-lite.formal-case-local-replay.v1",
        "case_id": CASE_ID,
        "status": "LOCAL_KERNEL_REPLAY_PASSED",
        "checked_at": datetime.now(UTC).isoformat(),
        "source_status": lock.get("status"),
        "case_lock_sha256": expected_lock_sha256,
        "source_sha256": _sha256(files["FibreCriterion.lean"]),
        "axiom_audit_sha256": _sha256(files["AxiomAudit.lean"]),
        "lean_binary_sha256": _sha256(_read_bounded(lean_executable, 200_000_000)),
        "lean_version": version["stdout"].strip(),
        "host": platform.platform(),
        "declarations": observed,
        "compile_stdout_sha256": compile_result["stdout_sha256"],
        "compile_stderr_sha256": compile_result["stderr_sha256"],
        "audit_stdout_sha256": audit_result["stdout_sha256"],
        "audit_stderr_sha256": audit_result["stderr_sha256"],
        "limitations": [
            "Same-source local Lean replay; not independently authenticated toolchain or OS isolation.",
            "No external source commit, formal-to-paper semantic review, organizational independence, or Case 003 result.",
            "No QualificationReceipt, CurrentUseBinding, or execution authorization is issued.",
        ],
    }
    result["receipt_sha256"] = _sha256(_canonical(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--lean", type=Path, required=True)
    parser.add_argument("--expected-lock-sha256", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    try:
        result = replay(args.case_dir, args.lean, args.expected_lock_sha256, args.timeout)
    except (CaseReplayError, OSError) as exc:
        print(json.dumps({"case_id": CASE_ID, "status": "NOT_VERIFIED", "error": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
