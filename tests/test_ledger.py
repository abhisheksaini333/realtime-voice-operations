import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, InvalidCall, CallNotFound


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "calls.sqlite"
        self.ledger = CallLedger(self.path)

    def admit(self, **changes):
        values = dict(owner="team-a", caller="1001", room="intake-1",
                      participant="sip-1", now=100.0)
        values.update(changes)
        return self.ledger.admit(**values)

    def test_admission_persists_observed_identifiers_and_event(self):
        call = self.admit()
        restored = CallLedger(self.path).get(call["id"], "team-a")
        self.assertEqual(restored["status"], "admitted")
        self.assertEqual(restored["caller"], "1001")
        self.assertEqual(restored["participant"], "sip-1")
        self.assertEqual(restored["room"], "intake-1")
        self.assertEqual(self.ledger.events(call["id"], "team-a")[0]["kind"], "admitted")

    def test_invalid_admission_is_not_persisted(self):
        for field, value in [("caller", "1001\r\nX: injected"), ("owner", ""),
                             ("room", ""), ("participant", ""), ("now", float("nan"))]:
            with self.subTest(field=field), self.assertRaises(InvalidCall):
                self.admit(**{field: value})
        self.assertEqual(self.ledger.list_calls("team-a"), [])

    def test_active_call_capacity_is_shared_between_ledger_instances(self):
        limited = CallLedger(Path(self.directory.name) / "limited.sqlite", max_active_calls=1)
        limited.admit(owner="team-a", caller="1001", room="one", participant="one", now=1)
        other = CallLedger(limited.path)
        with self.assertRaisesRegex(InvalidCall, "capacity"):
            other.admit(owner="team-b", caller="1002", room="two", participant="two", now=2)
        self.assertEqual(len(other.list_calls("team-b")), 0)

    def test_duplicate_room_participant_does_not_consume_another_slot(self):
        self.admit()
        with self.assertRaisesRegex(InvalidCall, "already"):
            self.admit()
        self.assertEqual(len(self.ledger.list_calls("team-a")), 1)

    def test_call_lifecycle_is_versioned_and_terminal(self):
        call = self.admit()
        for state in ("ringing", "answered", "ended"):
            call = self.ledger.transition(call["id"], "team-a", state,
                                          now=call["updated_at"] + 1, version=call["version"])
        self.assertEqual(call["status"], "ended")
        self.assertEqual(call["version"], 4)
        with self.assertRaisesRegex(InvalidCall, "transition"):
            self.ledger.transition(call["id"], "team-a", "answered", now=105, version=4)

    def test_stale_update_and_cross_owner_access_do_not_change_call(self):
        call = self.admit()
        with self.assertRaisesRegex(InvalidCall, "version"):
            self.ledger.transition(call["id"], "team-a", "answered", now=101, version=0)
        with self.assertRaises(CallNotFound):
            self.ledger.transition(call["id"], "team-b", "answered", now=101, version=1)
        self.assertEqual(self.ledger.get(call["id"], "team-a")["version"], 1)

    def test_drain_refuses_admission_while_existing_call_can_finish(self):
        call = self.admit()
        self.ledger.begin_drain(now=101, deadline=110)
        with self.assertRaisesRegex(InvalidCall, "draining"):
            self.admit(room="another", participant="another", now=102)
        self.assertFalse(self.ledger.drain_status(now=102)["complete"])
        self.ledger.transition(call["id"], "team-a", "ended", now=103, version=1)
        self.assertTrue(self.ledger.drain_status(now=104)["complete"])

    def test_expired_drain_requests_termination_without_inventing_hangup(self):
        call = self.admit()
        self.ledger.begin_drain(now=101, deadline=110)
        status = CallLedger(self.path).drain_status(now=111)
        self.assertEqual(status["needs_termination"], [call["id"]])
        self.assertEqual(self.ledger.get(call["id"], "team-a")["status"], "admitted")


if __name__ == "__main__":
    unittest.main()
