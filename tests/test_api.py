import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from voiceops.api import server
from voiceops.ledger import CallLedger
from voiceops.transfers import TransferService
from voiceops.supervisor import Supervisor


class Gateway:
    async def transfer(self, room, participant, uri):
        pass


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        call = self.ledger.admit(owner="team-a", caller="1001", room="one", participant="sip-1", now=100)
        self.call = self.ledger.transition(call["id"], "team-a", "answered", version=1, now=101)
        self.transfers = TransferService(self.ledger, targets={"operator": "sip:operator@127.0.0.1:18160"})
        self.tokens = {
            "a" * 40: {"name": "admin", "owner": "team-a", "role": "admin"},
            "b" * 40: {"name": "other", "owner": "team-b", "role": "operator"},
            "v" * 40: {"name": "viewer", "owner": "team-a", "role": "viewer"},
        }
        self.http = server(self.ledger, self.transfers, Supervisor(self.ledger, self.transfers, Gateway(), clock=lambda: 110),
                           self.tokens, port=0, request_timeout=2, max_connections=2, clock=lambda: 110)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def request(self, method, path, body=None, token="a" * 40, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=3)
        request_headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(method, path, None if body is None else json.dumps(body), headers=request_headers)
        response = connection.getresponse()
        value = response.read()
        connection.close()
        return response.status, json.loads(value)

    def test_authentication_ownership_and_read_only_role(self):
        self.assertEqual(self.request("GET", "/api/calls", token="wrong")[0], 401)
        self.assertEqual(self.request("GET", "/api/calls/" + self.call["id"], token="b" * 40)[0], 404)
        self.assertEqual(self.request("POST", "/api/drain", {"seconds": 10}, token="b" * 40)[0], 403)
        self.assertEqual(self.request("POST", "/api/calls/" + self.call["id"] + "/transfer", {"target": "operator"}, token="v" * 40)[0], 403)

    def test_approval_binds_exact_proposal_and_cannot_replay(self):
        status, proposal = self.request("POST", "/api/calls/" + self.call["id"] + "/transfer", {"target": "operator"})
        self.assertEqual(status, 200)
        path = "/api/transfers/" + proposal["id"] + "/approve"
        self.assertEqual(self.request("POST", path, {"digest": "wrong"})[0], 409)
        status, result = self.request("POST", path, {"digest": proposal["digest"]})
        self.assertEqual((status, result["state"]), (200, "dispatched"))
        self.assertEqual(self.request("POST", path, {"digest": proposal["digest"]})[0], 409)

    def test_forged_authority_and_cross_origin_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "attacker.test", "Origin": "http://attacker.test"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Origin": "https://attacker.test"})[0], 403)

    def test_incomplete_body_has_a_bounded_socket_deadline(self):
        with socket.create_connection(("127.0.0.1", self.http.server_port), timeout=2) as client:
            client.sendall((f"POST /api/drain HTTP/1.1\r\nHost: 127.0.0.1:{self.http.server_port}\r\n"
                            f"Authorization: Bearer {'a'*40}\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{{").encode())
            self.assertIn(b"408", client.recv(4096))

    def test_call_listing_does_not_expose_other_owners_drain_ids(self):
        other = self.ledger.admit(owner="team-b", caller="1002", room="two", participant="sip-2", now=101)
        self.ledger.begin_drain(now=102, deadline=105)
        for token in ("a" * 40, "v" * 40):
            status, payload = self.request("GET", "/api/calls", token=token)
            self.assertEqual(status, 200)
            self.assertNotIn(other["id"], json.dumps(payload))
            self.assertNotIn("needs_termination", payload["drain"])
