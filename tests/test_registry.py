import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from voiceops.ledger import CallLedger, InvalidCall
from voiceops.registry import CallRegistry


class RegistryTests(unittest.TestCase):
    def test_same_caller_new_dialog_gets_new_call_and_late_disconnect_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = CallLedger(Path(directory) / "calls.sqlite", max_active_calls=1)
            registry = CallRegistry(ledger, "local-team", "room")
            first = SimpleNamespace(identity="sip_1001", sid="PA_one", attributes={
                "sip.callID": "SCL_one", "sip.callIDFull": "first-dialog", "sip.phoneNumber": "1001"})
            second = SimpleNamespace(identity="sip_1001", sid="PA_two", attributes={
                "sip.callID": "SCL_two", "sip.callIDFull": "second-dialog", "sip.phoneNumber": "1001"})
            old_id = registry.admit(first, now=100)
            self.assertEqual(registry.admit(first, now=101), old_id)
            registry.disconnect(first, now=102)
            new_id = registry.admit(second, now=103)
            self.assertNotEqual(old_id, new_id)
            self.assertFalse(registry.matches(first))
            self.assertTrue(registry.matches(second))
            registry.disconnect(first, now=104)
            self.assertEqual(registry.calls[first.identity], new_id)
            self.assertEqual(ledger.get(old_id, "local-team")["status"], "ended")
            self.assertEqual(ledger.get(new_id, "local-team")["status"], "admitted")

    def test_a_terminal_dialog_is_not_resurrected_by_another_track_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = CallLedger(Path(directory) / "calls.sqlite")
            registry = CallRegistry(ledger, "owner", "room")
            participant = SimpleNamespace(identity="sip_1", sid="PA_one", attributes={"sip.callID": "SCL_one", "sip.phoneNumber": "1001"})
            call_id = registry.admit(participant, now=100)
            ledger.transition(call_id, "owner", "ended", version=1, now=101)
            with self.assertRaises(InvalidCall):
                registry.admit(participant, now=102)

    def test_rejected_disconnect_preserves_session_for_a_valid_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = CallLedger(Path(directory) / "calls.sqlite", max_active_calls=1)
            registry = CallRegistry(ledger, "owner", "room")
            participant = SimpleNamespace(identity="sip_1", sid="PA_one", attributes={"sip.callID": "SCL_one", "sip.phoneNumber": "1001"})
            call_id = registry.admit(participant, now=100)
            with self.assertRaises(InvalidCall):
                registry.disconnect(participant, now=99)
            self.assertTrue(registry.matches(participant))
            self.assertEqual(registry.calls[participant.identity], call_id)
            self.assertEqual(ledger.get(call_id, "owner")["status"], "admitted")
            self.assertEqual(registry.disconnect(participant, now=101), call_id)
            self.assertFalse(registry.matches(participant))
            self.assertEqual(ledger.get(call_id, "owner")["status"], "ended")
