"""
server.py -- a dependency-free web UI for the bridge.

Uses only the Python standard library (http.server).  Serves:

  GET  /              the single-page UI (web/index.html)
  GET  /api/state     full snapshot: app info, status, mapping, recent log
  GET  /api/ports     available real MIDI ports (if mido is installed)
  GET  /api/stream    Server-Sent Events: live dispatch + status feed
  POST /api/inject    {page,slot,role,value} drive a control (virtual E16)

The live feed lets the UI mirror the mixer control surface in real time and,
in dry-run mode, acts as an on-screen E16 for testing without hardware.
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge import Bridge
from .midi_io import list_ports

_WEB_DIR = Path(__file__).parent / "web"


class _Handler(BaseHTTPRequestHandler):
    bridge: Bridge = None           # set by make_server
    server_version = "OXI-CQ-Bridge"

    # Quiet logging: one tidy line, no stderr spam.
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    # -- helpers -------------------------------------------------------------
    def _send_json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, ctype: str = "text/html") -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes --------------------------------------------------------------
    def do_GET(self) -> None:          # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_text((_WEB_DIR / "index.html").read_text("utf-8"))
        elif path == "/api/state":
            self._send_json(self.bridge.snapshot())
        elif path == "/api/ports":
            self._send_json(list_ports())
        elif path == "/api/stream":
            self._stream()
        else:
            self._send_json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:         # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/inject":
            self._send_json({"error": "not found", "path": path}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            record = self.bridge.inject(
                int(data["page"]), int(data["slot"]),
                str(data.get("role", "turn")), int(data.get("value", 0)))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        if record is None:
            self._send_json({"error": "no such control"}, 404)
        else:
            self._send_json({"ok": True, "record": record})

    # -- SSE -----------------------------------------------------------------
    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.bridge.subscribe()
        try:
            # prime with the current status so a new tab shows live state
            self._sse_write({"type": "status", "status": self.bridge.status()})
            while True:
                try:
                    msg = q.get(timeout=15.0)
                    self._sse_write(msg)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.bridge.unsubscribe(q)

    def _sse_write(self, obj) -> None:
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
        self.wfile.flush()


def make_server(bridge: Bridge, host: str = "127.0.0.1", port: int = 8753
                ) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"bridge": bridge})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve_in_thread(bridge: Bridge, host: str, port: int
                    ) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = make_server(bridge, host, port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
