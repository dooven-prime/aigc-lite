"""Print a data-only RIME Event-Anchored Consumer example witness as JSON."""

from __future__ import annotations

import json

from app.adapters.research_import.rime_consumer import RimeConsumerWitnessAdapter


def main() -> None:
    contract = RimeConsumerWitnessAdapter().contract()
    witness = {
        "contract": {key: contract[key] for key in (
            "contract_version", "source_commit", "spec_sha256", "ufe_sha256", "checker_sha256"
        )},
        "environment": {
            "q_size": 7,
            "p": "successor_mod_n",
            "d": [0, 3, 2, 5, 1, 4, 0],
            "omega": list(range(7)),
            "iota": list(range(7)),
            "ufe_enumeration": list(range(7)),
            "kernel_ports": [0, 6],
            "collision_image": 0,
        },
        "prefix": "dpp",
        "selected_block": [0, 6],
        "commands": [{"kind": "Carry", "letter": "d"}],
        "candidate_reports": None,
    }
    print(json.dumps(witness, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
