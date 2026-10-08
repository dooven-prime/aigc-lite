"""Read a local case-003 Run and its verification receipt without changing state."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("run_id")
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10) as db:
        row = db.execute(
            "SELECT id, tenant_id, status, error_code, created_at, completed_at "
            "FROM agent_runs WHERE id = ?", (args.run_id,),
        ).fetchone()
        if row is None:
            raise SystemExit("Run not found")
        artifacts = db.execute(
            "SELECT id, content_text, metadata FROM artifacts WHERE run_id = ?",
            (args.run_id,),
        ).fetchall()
        attempts = db.execute(
            "SELECT id, outcome, kind FROM research_verification_attempts WHERE run_id = ?",
            (args.run_id,),
        ).fetchall()
    receipts = [
        (artifact_id, json.loads(content))
        for artifact_id, content, metadata in artifacts
        if json.loads(metadata).get("role") == "project_verification_receipt"
    ]
    result = {
        "run_id": row[0],
        "workspace_id": row[1],
        "status": row[2],
        "error_code": row[3],
        "created_at": row[4],
        "completed_at": row[5],
        "verification_attempts": [
            {"id": item[0], "outcome": item[1], "kind": item[2]} for item in attempts
        ],
        "receipts": [
            {
                "artifact_id": artifact_id,
                "contract_version": receipt.get("contract_version"),
                "dependencies": receipt.get("dependencies"),
                "kernel": receipt.get("kernel"),
                "comparator": receipt.get("comparator"),
                "limitations": receipt.get("limitations"),
            }
            for artifact_id, receipt in receipts
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
