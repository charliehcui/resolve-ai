"""Local HTTP fault injection; forwards real requests to the real simulator."""
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import httpx


def serve_proxy(port: int, upstream: str) -> None:
    state = {"mode": None, "path": None}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/__eval__/arm":
                with lock:
                    state.update(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"armed":true}')
                return
            with lock:
                mode = state["mode"] if self.path == state["path"] else None
                if mode:
                    state["mode"] = None
            if mode != "drop_before_accept":
                headers = {name: value for name, value in self.headers.items() if name.lower() not in {"host", "content-length", "connection"}}
                try:
                    response = httpx.request(self.command, upstream + self.path, content=body, headers=headers, timeout=30)
                except httpx.RequestError:
                    self.send_error(502)
                    return
            if mode in {"drop_after_accept", "drop_before_accept"}:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
