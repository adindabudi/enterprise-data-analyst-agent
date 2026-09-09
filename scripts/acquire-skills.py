from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from document_skills_acquisition import UnsafeSkillContentError, acquire_pack
from document_skills_contract import TermsNotAcceptedError

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire the exact reviewed document skill trees.")
    parser.add_argument("--lock", type=Path, default=ROOT / "skills.lock.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "services" / "worker" / "document-skills")
    parser.add_argument("--pack", choices=("all", "documents", "web"), default="all")
    arguments = parser.parse_args()
    try:
        lock = acquire_pack(arguments.lock, arguments.destination, arguments.pack)
    except (OSError, subprocess.CalledProcessError, TermsNotAcceptedError, UnsafeSkillContentError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: acquired verified {arguments.pack} skill pack at {arguments.destination} from {lock.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
