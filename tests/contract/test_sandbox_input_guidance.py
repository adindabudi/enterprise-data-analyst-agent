"""What the agent is told about its inputs has to match where the sandbox puts them.

The guidance in the task-state context is the only thing telling a generated
script where to look. The sandbox names an imported file `file_<random>` with the
suffix of the name the worker supplied, so no script can construct that path; it
has to list the directory. If either side drifts, the script opens a path that
does not exist and the failure looks like a modelling mistake instead of a
contract break.
"""

from __future__ import annotations

import inspect

from eda_sandbox.files import FileIndex, _safe_suffix
from eda_sandbox.settings import IMPORTS, WORKSPACE
from eda_worker.context.task_state import _query_result_guidance

GUIDANCE = _query_result_guidance({"queryResults": [{"displayName": "query-result-1.json"}]}, True)


def test_the_agent_is_pointed_at_the_directory_the_sandbox_actually_imports_into() -> None:
    assert IMPORTS.parent == WORKSPACE
    assert IMPORTS.name in GUIDANCE


def test_the_agent_is_told_to_list_rather_than_guess_a_generated_name() -> None:
    # import_stream builds the name from a random token, so no caller can predict it.
    source = inspect.getsource(FileIndex.import_stream)
    assert "secrets.token_urlsafe" in source
    assert "list that directory" in GUIDANCE
    assert "<artifactId>" not in GUIDANCE


def test_the_worker_supplies_a_suffix_the_sandbox_keeps() -> None:
    # capabilities imports each artifact as "<artifactId>.bin", and only the suffix survives.
    assert _safe_suffix("artifact-abc.bin") == ".bin"
