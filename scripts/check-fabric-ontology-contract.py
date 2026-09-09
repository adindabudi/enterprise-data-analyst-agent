from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fabric_ontology_contract import build_provider_contract, read_json_object, write_json_atomically

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_CONTRACT = ROOT / ".artifacts" / "fabric-ontology-auth-contract.json"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate local Fabric ontology artifacts and build a hash-only provider contract."
    )
    parser.add_argument("--auth-contract", type=Path, default=DEFAULT_AUTH_CONTRACT)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--inspection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        contract = build_provider_contract(
            auth_contract=read_json_object(arguments.auth_contract),
            catalog=read_json_object(arguments.catalog),
            inspection=read_json_object(arguments.inspection),
        )
        write_json_atomically(arguments.output, contract)
    except ValueError as error:
        arguments.output.unlink(missing_ok=True)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: prepared Fabric ontology provider contract {contract['authContractSha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
