"""Local HTTP stubs, so the secrecy tests exercise the real requests stack.

Deliberately real sockets rather than mocks: three of the leaks these tests pin (the
redirect, the retained PreparedRequest, the raw request bytes in urllib3 frame locals)
live inside requests/urllib3 and are invisible to a mocked transport.
"""
from __future__ import annotations

import http.server
import socketserver
import threading

import pytest


class _Server(socketserver.ThreadingTCPServer):
    # THREADING, not plain TCPServer. With HTTP/1.1 keep-alive the handler holds its
    # connection open waiting for the next request on it, so a single-threaded server
    # blocks forever on the second test request from a fresh connection.
    daemon_threads = True
    allow_reuse_address = True


def _serve(handler_factory):
    seen = {"paths": [], "headers": [], "n": 0}
    handler = handler_factory(seen)
    httpd = _Server(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/v1" % port, seen, httpd


def _base(seen, respond):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _do(self):
            seen["paths"].append(self.path)
            seen["headers"].append(dict(self.headers))
            seen["n"] += 1
            respond(self)

        do_GET = _do
        do_POST = _do

        def log_message(self, *a):
            pass

    return H


def _json(handler, code, body: bytes):
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.fixture
def httpserver_401():
    url, seen, httpd = _serve(
        lambda s: _base(s, lambda h: _json(h, 401, b'{"detail":"Invalid API key"}'))
    )
    yield url, seen
    httpd.shutdown()


@pytest.fixture
def httpserver_echo():
    """Echoes the presented key back in the error body — the WAF/proxy scenario."""

    def respond(h):
        key = h.headers.get("X-API-Key", "")
        _json(h, 401, ('{"detail":"Invalid API key: %s"}' % key).encode())

    url, seen, httpd = _serve(lambda s: _base(s, respond))
    yield url, seen
    httpd.shutdown()


@pytest.fixture
def httpserver_rotate():
    url, seen, httpd = _serve(
        lambda s: _base(s, lambda h: _json(h, 200, b'{"api_key":"pe_live_ROTATED_NEW"}'))
    )
    yield url, seen
    httpd.shutdown()


@pytest.fixture
def httpserver_500_counter():
    url, seen, httpd = _serve(
        lambda s: _base(s, lambda h: _json(h, 500, b'{"detail":"boom"}'))
    )
    yield url, seen
    httpd.shutdown()


@pytest.fixture
def httpserver_redirect():
    """302s to a DIFFERENT host. `seen` records whether the target was ever reached."""
    target_hits = []

    class Sink(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            target_hits.append(dict(self.headers))
            _json(self, 200, b'{"ok":true}')

        def log_message(self, *a):
            pass

    sink = _Server(("127.0.0.1", 0), Sink)
    sink_port = sink.server_address[1]
    threading.Thread(target=sink.serve_forever, daemon=True).start()

    def respond(h):
        h.send_response(302)
        # localhost vs 127.0.0.1 — a different host by requests' reckoning
        h.send_header("Location", "http://localhost:%d/v1/sink" % sink_port)
        h.send_header("Content-Length", "0")
        h.end_headers()

    url, seen, httpd = _serve(lambda s: _base(s, respond))
    yield url, target_hits
    httpd.shutdown()
    sink.shutdown()
