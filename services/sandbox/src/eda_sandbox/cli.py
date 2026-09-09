from __future__ import annotations

import argparse
import json
import secrets
from hashlib import sha256
from pathlib import Path

from .contracts import FileRecord, ValidationProfile, ValidationResult, ValidationStatus
from .validation import validate_path
from .web_artifact import bundle_web_artifact


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m eda_sandbox.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("--file", required=True, type=Path)
    validate.add_argument("--profile", required=True)
    validate.add_argument("--output", required=True, type=Path)
    bundle_web = subcommands.add_parser("bundle-web")
    bundle_web.add_argument("--app", required=True, type=Path)
    bundle_web.add_argument("--styles", required=True, type=Path)
    bundle_web.add_argument("--title", required=True)
    bundle_web.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    if arguments.command == "bundle-web":
        bundle_web_artifact(
            app_source=arguments.app.read_text(encoding="utf-8"),
            styles=arguments.styles.read_text(encoding="utf-8"),
            title=arguments.title,
            output=arguments.output,
        )
        print(json.dumps({"status": "passed", "output": str(arguments.output)}))
        return

    profile = ValidationProfile(arguments.profile)
    try:
        validate_path(profile, arguments.file)
        status = ValidationStatus.PASSED
        error = None
    except (OSError, ValueError) as validation_error:
        status = ValidationStatus.FAILED
        error = str(validation_error)
    report_payload = json.dumps(
        {"file": arguments.file.name, "profile": profile.value, "status": status.value, "error": error},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(report_payload)
    result = ValidationResult(
        validation_id=f"validation_{secrets.token_hex(8)}",
        file_id=f"file_{sha256(arguments.file.read_bytes()).hexdigest()[:32]}",
        profile=profile,
        status=status,
        report=FileRecord(
            file_id=f"file_{sha256(report_payload).hexdigest()[:32]}",
            category="validation",
            display_name=arguments.output.name,
            size_bytes=len(report_payload),
            sha256=sha256(report_payload).hexdigest(),
        ),
    )
    print(json.dumps({"status": result.status.value, "report": str(arguments.output)}))
    if result.status.value != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
