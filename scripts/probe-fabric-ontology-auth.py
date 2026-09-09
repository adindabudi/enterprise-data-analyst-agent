from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

from fabric_ontology_contract import read_json_object, validate_auth_contract, write_json_atomically

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / ".artifacts" / "private" / "fabric-ontology" / "production-auth-probe.json"
DEFAULT_OUTPUT = ROOT / ".artifacts" / "fabric-ontology-auth-contract.json"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a production-BFF ontology auth proof without fallback.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference-cli-scope", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    output = arguments.output
    try:
        if arguments.reference_cli_scope:
            raise ValueError("reference CLI scope is diagnostic and cannot publish an auth contract")
        report = arguments.report
        if stat.S_IMODE(report.stat().st_mode) != 0o600:
            raise ValueError("production BFF auth proof must have mode 0600")
        contract = validate_auth_contract(read_json_object(report))
        write_json_atomically(output, contract)
    except (OSError, ValueError) as error:
        output.unlink(missing_ok=True)
        message = "PRODUCTION_AUTH_CONTRACT_UNPROVEN" if not arguments.reference_cli_scope else str(error)
        print(f"FAIL: {message}", file=sys.stderr)
        return 1
    print(f"PASS: ontology production auth contract {contract['runSha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
