"""Loopback operator API with exact approvals and bounded HTTP admission."""
import asyncio
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlsplit

from .ledger import CallNotFound, InvalidCall, identifier
from .observations import Observations
from .privacy import PrivacyStore


class AccessDenied(Exception):
    pass


def server(ledger, transfers, supervisor, tokens, *, host="127.0.0.1", port=18170,
           ui_dir=None, request_timeout=15, max_connections=16, clock=time.time):
    if host not in {"127.0.0.1", "localhost"}:
        raise InvalidCall("The reference operator server binds to loopback")
    if not tokens:
        raise InvalidCall("At least one operator credential is required")
    for token, principal in tokens.items():
        if not isinstance(token, str) or not token.isascii() or len(token) < 32:
            raise InvalidCall("Operator credentials require at least 32 ASCII characters")
        if principal.get("role") not in {"admin", "operator", "viewer"}:
            raise InvalidCall("Invalid operator role")
        identifier(principal.get("name"), "operator name")
        identifier(principal.get("owner"), "owner")
    privacy, observations = PrivacyStore(ledger), Observations(ledger)

    def public_drain_status():
        status = ledger.drain_status(now=clock())
        # The worker's termination queue spans owners and is not API metadata.
        return {key: value for key, value in status.items() if key != "needs_termination"}

    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True
        def __init__(self, address, handler):
            self.slots = threading.BoundedSemaphore(max_connections)
            super().__init__(address, handler)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                try:
                    request.settimeout(0.1)
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(request_timeout / 2)
            self.deadline = threading.Timer(request_timeout, self.expire)
            self.deadline.daemon = True
            self.deadline.start()

        def expire(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self):
            self.deadline.cancel()
            try:
                super().finish()
            except OSError:
                pass

        def log_message(self, *args):
            pass

        def send(self, status, value, kind="application/json"):
            body = json.dumps(value).encode() if kind == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def principal(self):
            values = self.headers.get_all("Authorization", [])
            candidate = values[0][7:] if len(values) == 1 and values[0].startswith("Bearer ") else ""
            if candidate.isascii():
                for token, principal in tokens.items():
                    if hmac.compare_digest(candidate, token):
                        return principal
            return None

        def request(self):
            try:
                authorities = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                hosts = self.headers.get_all("Host", [])
                if len(hosts) != 1 or hosts[0] not in authorities:
                    raise AccessDenied("Invalid request authority")
                origins = self.headers.get_all("Origin", [])
                if len(origins) > 1 or origins and origins[0] not in {"http://" + value for value in authorities}:
                    raise AccessDenied("Cross-origin requests are not accepted")
                path = urlsplit(self.path).path
                if self.command == "GET" and path == "/health":
                    return self.send(200, {"status": "ok"})
                if self.command == "GET" and ui_dir and not path.startswith("/api/"):
                    relative = "index.html" if path == "/" else path.lstrip("/")
                    base = Path(ui_dir).resolve()
                    file = (base / relative).resolve()
                    if not file.is_relative_to(base) or not file.is_file() or file.suffix not in {".html", ".js", ".css", ".svg"}:
                        raise CallNotFound("Page unavailable")
                    kind = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[file.suffix]
                    return self.send(200, file.read_bytes(), kind)
                principal = self.principal()
                if principal is None:
                    return self.send(401, {"error": "unauthorized", "message": "An operator access key is required"})
                owner, role = principal["owner"], principal["role"]
                payload = {}
                if self.command == "POST":
                    if role == "viewer":
                        raise AccessDenied("This role can view calls only")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or self.headers.get("Transfer-Encoding") or not lengths[0].isdigit():
                        raise InvalidCall("One explicit body length is required")
                    length = int(lengths[0])
                    if not 1 <= length <= 65536:
                        return self.send(413, {"error": "body_limit", "message": "Request body is too large"})
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        return self.send(415, {"error": "json_required", "message": "Send a JSON object"})
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise InvalidCall("Incomplete request body")
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise InvalidCall("A JSON object is required")
                parts = path.strip("/").split("/")
                if self.command == "GET":
                    if path == "/api/session":
                        return self.send(200, principal)
                    if path == "/api/calls":
                        return self.send(200, {"calls": ledger.list_calls(owner), "drain": public_drain_status()})
                    if path == "/api/targets":
                        return self.send(200, {"targets": transfers.targets})
                    if len(parts) == 4 and parts[:2] == ["api", "calls"] and parts[3] == "recording":
                        if role == "viewer":
                            raise AccessDenied("Audio access requires an operator")
                        return self.send(200, privacy.recording(parts[2], owner), "audio/wav")
                    if len(parts) == 3 and parts[:2] == ["api", "calls"]:
                        call_id = parts[2]
                        call = ledger.get(call_id, owner)
                        with ledger.connection() as db:
                            operations = [dict(row) for row in db.execute("SELECT * FROM transfers WHERE call_id=? AND owner=? ORDER BY created_at", (call_id, owner))]
                        return self.send(200, {"call": call, "events": ledger.events(call_id, owner),
                                              "media": observations.health(call_id, owner, now=clock()),
                                              "consent": privacy.settings(call_id, owner),
                                              "transcript": privacy.read(call_id, owner) if role != "viewer" else [],
                                              "transfers": operations})
                if self.command == "POST":
                    if path in {"/api/drain", "/api/resume"}:
                        if role != "admin":
                            raise AccessDenied("Service admission requires an administrator")
                        if path == "/api/drain":
                            seconds = payload.get("seconds")
                            if type(seconds) is not int or not 10 <= seconds <= 300:
                                raise InvalidCall("Choose a drain duration between 10 and 300 seconds")
                            ledger.begin_drain(now=clock(), deadline=clock() + seconds)
                        else:
                            with ledger.transaction() as db:
                                if db.execute("SELECT count(*) FROM calls WHERE status NOT IN ('ended','failed','transferred')").fetchone()[0]:
                                    raise InvalidCall("Wait for active calls to finish before resuming")
                                db.execute("DELETE FROM settings WHERE key='drain_deadline'")
                        return self.send(200, public_drain_status())
                    if len(parts) == 4 and parts[:2] == ["api", "calls"]:
                        call_id, action = parts[2:]
                        ledger.get(call_id, owner)
                        if action == "transfer":
                            return self.send(200, transfers.propose(call_id, owner, payload.get("target"), now=clock()))
                        if action == "consent":
                            return self.send(200, privacy.consent(call_id, owner, transcript=payload.get("transcript"),
                                                                recording=payload.get("recording"), attestation=payload.get("attestation"), now=clock()))
                        if action == "hangup":
                            return self.send(200, asyncio.run(supervisor.hangup(call_id, owner)))
                    if len(parts) == 4 and parts[:2] == ["api", "transfers"]:
                        if parts[3] == "cancel":
                            return self.send(200, transfers.cancel(parts[2], owner, now=clock()))
                        if parts[3] == "approve":
                            transfers.approve(parts[2], owner, payload.get("digest"), principal["name"], now=clock())
                            return self.send(200, asyncio.run(supervisor.dispatch(parts[2], owner)))
                raise CallNotFound("Operation unavailable")
            except AccessDenied as exc:
                self.send(403, {"error": "forbidden", "message": str(exc)})
            except CallNotFound as exc:
                self.send(404, {"error": "not_found", "message": str(exc)})
            except (InvalidCall, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self.send(409, {"error": "invalid_request", "message": str(exc)})
            except TimeoutError:
                self.send(408, {"error": "request_timeout", "message": "Request timed out"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send(500, {"error": "internal_error", "message": "The operation could not be completed"})

        do_GET = request
        do_POST = request

    return BoundedServer((host, port), Handler)
