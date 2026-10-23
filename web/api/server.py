import http.client
import http.server
import json
import socket
import signal
import sys
from urllib.parse import urlsplit


class WorkerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=125)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/run/gokaskad/worker.sock")


def rpc(command, payload=None):
    connection = WorkerConnection()
    try:
        connection.request("POST", "/rpc", json.dumps({"command": command, "payload": payload}),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        try:
            if self.command == "GET" and self.path in ("/api/health", "/api/rules"):
                status, data = rpc("list")
                self.reply(status, {"ready": status == 200} if self.path == "/api/health" else data)
                return
            if self.command not in ("PUT", "DELETE") or not self.path.startswith("/api/rules/"):
                self.reply(404, {"error": "Not found"})
                return
            # Basic auth cookies/credentials are browser ambient: require same-origin JSON requests.
            origin = self.headers.get("Origin")
            if (self.headers.get("X-Gokaskad") != "1" or
                    (origin and urlsplit(origin).netloc != self.headers.get("Host"))):
                self.reply(403, {"error": "Same-origin request required"})
                return
            ident = self.path.removeprefix("/api/rules/")
            import uuid
            ident = str(uuid.UUID(ident))
            payload = {"id": ident}
            if self.command == "PUT":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON body required")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                payload["id"] = ident
            status, data = rpc("put" if self.command == "PUT" else "delete", payload)
            self.reply(status, data)
        except (ValueError, TypeError, KeyError) as exc:
            self.reply(400, {"error": str(exc)})
        except (OSError, http.client.HTTPException):
            self.reply(503, {"error": "Worker unavailable; retry after checking its health"})

    do_GET = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
