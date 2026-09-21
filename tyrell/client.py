import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def request(directory, action, **params):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(90)
        sock.connect(str(Path(directory) / "service.sock"))
        sock.sendall((json.dumps({"action": action, **params}) + "\n").encode())
        with sock.makefile("rb") as reader:
            line = reader.readline(32 * 1024 * 1024)
    if not line:
        raise RuntimeError("Dashboard service disconnected")
    response = json.loads(line)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def ensure_service(directory, codex):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if len(os.fsencode(str(directory / "service.sock"))) >= 104:
        raise RuntimeError("Dashboard state path is too long for a Unix socket; choose a shorter --state-dir")
    try:
        request(directory, "snapshot")
        return
    except (OSError, RuntimeError):
        pass
    package_root = Path(__file__).resolve().parents[1]
    log_path = directory / "service.log"
    with log_path.open("a") as log:
        os.chmod(log_path, 0o600)
        subprocess.Popen([sys.executable, "-B", "-m", "tyrell", "--state-dir", str(directory), "--codex", codex, "service"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True, cwd=str(package_root))
    for _ in range(60):
        time.sleep(0.1)
        try:
            request(directory, "snapshot")
            return
        except (OSError, RuntimeError):
            pass
    raise RuntimeError("Dashboard service did not start. See " + str(log_path))
