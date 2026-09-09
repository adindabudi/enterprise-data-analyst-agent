from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

EXPECTED = {
    "eda_worker/agent/prompts/claude-opus-4-8-v1.md": (
        "9e4408e257c61ba7f299ca0512545915f37703f9fb40aeb3416a57b9c372ecb4"
    ),
    "eda_worker/agent/prompts/gpt-5.6-terra-v1.md": (
        "a5cb43d04e9f5237f6e620c12af484e79bdc0c36abbfd7dcd4450928137497df"
    ),
}
PROMPT_PACKAGE = "eda_worker/agent/prompts/"


def main() -> None:
    wheel_dir = Path(sys.argv[1])
    wheels = sorted(wheel_dir.glob("eda_worker-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one eda-worker wheel, found {len(wheels)}")
    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        required = set(EXPECTED) | {f"{PROMPT_PACKAGE}__init__.py"}
        prompt_resources = {name for name in names if name.startswith(PROMPT_PACKAGE)}
        if prompt_resources != required:
            raise SystemExit(
                f"unexpected worker prompt resources: expected {sorted(required)}, found {sorted(prompt_resources)}"
            )
        for archive_path, expected_sha256 in EXPECTED.items():
            actual_sha256 = sha256(archive.read(archive_path)).hexdigest()
            if actual_sha256 != expected_sha256:
                raise SystemExit(f"prompt hash mismatch: {archive_path}")
    print("PASS: worker wheel contains exact prompt assets")


if __name__ == "__main__":
    main()
