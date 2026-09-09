from __future__ import annotations

import base64
import hashlib

from eda_worker.sandbox_contract import OUTPUTS, WORKSPACE
from eda_worker.tools.contracts import BuildWebArtifactOperation, ExecuteSandboxOperation, SandboxRuntime


def build_web_execution(operation: BuildWebArtifactOperation) -> ExecuteSandboxOperation:
    app_payload = base64.b64encode(operation.app_source.encode("utf-8")).decode("ascii")
    styles_payload = base64.b64encode(operation.styles.encode("utf-8")).decode("ascii")
    build_id = hashlib.sha256(
        "\0".join((operation.title, operation.display_name, operation.app_source, operation.styles)).encode("utf-8")
    ).hexdigest()[:16]
    app_path = f"{WORKSPACE}/.eda-web-{build_id}.tsx"
    styles_path = f"{WORKSPACE}/.eda-web-{build_id}.css"
    output_path = f"{OUTPUTS}/{operation.display_name}"
    source = (
        "import base64\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"app_path = Path({app_path!r})\n"
        f"styles_path = Path({styles_path!r})\n"
        f"app_path.write_bytes(base64.b64decode({app_payload!r}, validate=True))\n"
        f"styles_path.write_bytes(base64.b64decode({styles_payload!r}, validate=True))\n"
        "subprocess.run([\n"
        "    sys.executable, '-m', 'eda_sandbox.cli', 'bundle-web',\n"
        "    '--app', str(app_path), '--styles', str(styles_path),\n"
        f"    '--title', {operation.title!r}, '--output', {output_path!r},\n"
        "], check=True)\n"
    )
    return ExecuteSandboxOperation(
        runtime=SandboxRuntime.PYTHON,
        source=source,
        expected_outputs=(operation.display_name,),
        timeout_seconds=300,
    )
