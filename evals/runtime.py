"""Evaluation-only service lifecycle and local HTTP fault injection."""
import argparse
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

import httpx


def serve_proxy(port: int, upstream: str) -> None:
    state = {"mode": None, "path": None}
    lock = Lock()
    events = []

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
                    if state['mode'] is None and state['path'] is None:
                        events.clear()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"armed":true}')
                return
            if self.path == '/__eval__/events':
                with lock:
                    payload = json.dumps(events).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            with lock:
                mode = state["mode"] if self.path == state["path"] else None
                if mode and mode != "read_unavailable":
                    state["mode"] = None
                event = None
                if self.path.startswith('/repairs/'):
                    event = {'method': self.command, 'path': self.path, 'forwarded': mode != 'drop_before_accept', 'payload': json.loads(body) if body else {}}
                    events.append(event)
            if mode == "read_unavailable":
                payload = b'{"detail":"Evaluation injected read service unavailable"}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if mode != "drop_before_accept":
                headers = {name: value for name, value in self.headers.items() if name.lower() not in {"host", "content-length", "connection"}}
                try:
                    response = httpx.request(self.command, upstream + self.path, content=body, headers=headers, timeout=30)
                except httpx.RequestError:
                    self.send_error(502)
                    return
                if event is not None:
                    with lock:
                        event['http_status'] = response.status_code
            if mode in {"drop_after_accept", "drop_before_accept"}:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if mode == 'timeout_after_accept':
                time.sleep(6)
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def configure_tokens() -> None:
    from simulator.services import common

    common.SERVICE_TOKEN_FILE = Path(os.environ["EVAL_SERVICE_TOKEN_FILE"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["merchant", "platform", "warehouse", "worker", "proxy"], required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--upstream")
    args = parser.parse_args()
    configure_tokens()
    if args.service == "worker":
        from simulator.services.worker import recover_interrupted_tasks, run_forever

        if os.getenv('EVAL_WORKER_READY_FILE'):
            recover_interrupted_tasks()
            Path(os.environ['EVAL_WORKER_READY_FILE']).write_text('startup recovery completed', encoding='utf-8')
        run_forever(0.05)
    elif args.service == "proxy":
        serve_proxy(args.port, args.upstream)
    else:
        import uvicorn

        uvicorn.run(f"simulator.services.{args.service}:app", host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
