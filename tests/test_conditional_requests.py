"""GETs are conditional by default (0.4.0): the client remembers each response's ETag and body,
sends If-None-Match next time, and serves a 304 from the remembered body for 0 credits.

Real sockets, like conftest's stubs: the If-None-Match header has to reach the wire and the 304
has to come back through requests, or the test proves nothing about the transport.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from puntersedge import PuntersEdge, PuntersEdgeError

BOARD = [{"race_id": "r1", "venue": "Eagle Farm", "runners": [{"name": "A", "win": 2.4}]}]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _serve(tag='"ntg-abc"'):
    seen = {"headers": [], "methods": []}

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _answer(self):
            seen["headers"].append(dict(self.headers))
            seen["methods"].append(self.command)
            # consume the body, or keep-alive hands the next request a corrupted stream
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.command == "GET" and self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("X-Credits-Cost", "0")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(BOARD if self.command == "GET" else {"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("ETag", tag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer
        do_POST = _answer

    httpd = _Server(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/v1" % httpd.server_address[1], seen, httpd


@pytest.fixture
def server():
    base, seen, httpd = _serve()
    yield base, seen
    httpd.shutdown()


def test_second_identical_get_is_conditional_and_served_from_the_remembered_body(server):
    base, seen = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0)
    first = pe.racing_next_to_go(num_races=5)
    assert first == BOARD and pe.last_not_modified is False
    assert "If-None-Match" not in seen["headers"][0]
    second = pe.racing_next_to_go(num_races=5)
    assert second == BOARD and pe.last_not_modified is True
    assert seen["headers"][1]["If-None-Match"] == '"ntg-abc"'
    # a different query is a different request: no validator is sent for it
    pe.racing_next_to_go(num_races=6)
    assert "If-None-Match" not in seen["headers"][2] and pe.last_not_modified is False


def test_the_remembered_body_is_a_fresh_object_each_time(server):
    base, _ = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0)
    a = pe.racing_next_to_go()
    b = pe.racing_next_to_go()
    assert a == b and a is not b
    b[0]["venue"] = "mutated"
    assert pe.racing_next_to_go()[0]["venue"] == "Eagle Farm"


def test_post_is_never_conditional(server):
    base, seen = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0)
    pe._request("POST", "/racing/clv", json={"bets": []})
    pe._request("POST", "/racing/clv", json={"bets": []})
    assert all("If-None-Match" not in h for h in seen["headers"])


def test_can_be_switched_off(server):
    base, seen = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0, conditional_requests=False)
    pe.racing_next_to_go()
    pe.racing_next_to_go()
    assert all("If-None-Match" not in h for h in seen["headers"])
    assert pe.last_not_modified is False and pe._etags == {}


def test_memory_is_bounded(server):
    base, _ = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0)
    for n in range(40):
        pe.racing_next_to_go(num_races=n + 1)
    assert len(pe._etags) == 32
    # the oldest request was forgotten, the newest kept
    held = {ck[1] for ck in pe._etags}
    assert (("num_races", "40"),) in held and (("num_races", "1"),) not in held


def test_a_304_without_a_remembered_body_is_an_error_not_a_silent_none(server):
    base, _ = server
    pe = PuntersEdge(api_key="pe_test_key", base_url=base, retries=0)
    # a validator this client never recorded (set by hand on the session), so the server
    # answers 304 and there is no remembered body to serve
    pe._session.headers["If-None-Match"] = '"ntg-abc"'
    with pytest.raises(PuntersEdgeError) as e:
        pe.racing_next_to_go()
    assert "304" in str(e.value)
