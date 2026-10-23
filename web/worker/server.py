"""Serial local RPC server. No TCP listener, no Docker socket."""
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import signal

CLI = ["bash", "/app/gokaskad.sh", "--machine"]


def cli(command, payload=None):
    result = subprocess.run(CLI + [command], input=json.dumps(payload), text=True, capture_output=True, timeout=120)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {"error": "Worker CLI failed"}
    return (200 if result.returncode == 0 else 400 if result.returncode == 2 else 503), data


class Handler(http.server.BaseHTTPRequestHandler):
    def address_string(self):
        return "unix-client"

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384 or self.path != "/rpc":
                raise ValueError("Invalid RPC request")
            data = json.loads(self.rfile.read(size))
            if data.get("command") not in ("list", "put", "delete", "firewall"):
                raise ValueError("Invalid RPC command")
            status, result = cli(data["command"], data.get("payload"))
        except (ValueError, KeyError, TypeError):
            status, result = 400, {"error": "Invalid request"}
        except Exception as exc:
            print(str(exc), file=sys.stderr, flush=True)
            status, result = 503, {"error": "Worker unavailable"}
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UnixHTTPServer(http.server.HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        self.socket.bind(self.server_address)
        self.server_name, self.server_port = "localhost", 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    status, result = cli("reconcile")
    if status != 200:
        sys.exit("Cannot restore managed firewall rules: " + str(result))
    path = Path("/run/gokaskad/worker.sock")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(path.parent, 0, 10001)
    os.chmod(path.parent, 0o750)
    path.unlink(missing_ok=True)
    server = UnixHTTPServer(str(path), Handler)
    os.chown(path, 0, 10001)
    os.chmod(path, 0o660)
    print("Worker ready on Unix socket", flush=True)
    server.serve_forever()
