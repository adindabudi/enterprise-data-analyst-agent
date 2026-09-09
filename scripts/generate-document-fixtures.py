from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eda_artifacts.documents import generate_document_corpus, validate_generated_document


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the maintained synthetic document corpus from source.")
    parser.add_argument("--output", type=Path, default=Path(".artifacts/document-fixtures"))
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for document in generate_document_corpus():
        validate_generated_document(document)
        destination = arguments.output / document.display_name
        destination.write_bytes(document.content)
        records.append(
            {
                "kind": document.kind,
                "displayName": document.display_name,
                "sha256": hashlib.sha256(document.content).hexdigest(),
                "sizeBytes": len(document.content),
            }
        )
    (arguments.output / "manifest.json").write_text(
        json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
