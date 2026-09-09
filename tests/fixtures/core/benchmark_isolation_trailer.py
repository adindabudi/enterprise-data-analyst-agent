import hashlib
import json
import socket
import subprocess
from pathlib import Path

INPUTS = Path("inputs")
render_ms = int(globals().get("render_ms", 0))
input_hashes = sorted(hashlib.sha256(path.read_bytes()).hexdigest() for path in INPUTS.iterdir() if path.is_file())
network_success = False
try:
    connection = socket.create_connection(("1.1.1.1", 53), timeout=1)
    connection.close()
    network_success = True
except OSError:
    pass
child = subprocess.Popen(
    ["/usr/local/bin/python", "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(
    json.dumps(
        {
            "inputHashes": input_hashes,
            "networkSuccess": network_success,
            "childPid": child.pid,
            "renderMs": render_ms,
        },
        separators=(",", ":"),
    )
)
