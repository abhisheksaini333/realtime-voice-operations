import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, CallNotFound, InvalidCall
from voiceops.privacy import PrivacyStore


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        self.call = self.ledger.admit(owner="team-a", caller="1001", room="one", participant="sip-1", now=100)
        self.store = PrivacyStore(self.ledger)

    def test_transcripts_are_off_until_explicit_consent_and_owner_scoped(self):
        with self.assertRaisesRegex(InvalidCall, "consent"):
            self.store.append(self.call["id"], "team-a", "caller", "Please help", now=101)
        self.store.consent(self.call["id"], "team-a", transcript=True, recording=False,
                           attestation="spoken agreement observed by operator", now=102)
        self.store.append(self.call["id"], "team-a", "caller", "Please help", now=103)
        self.assertEqual(self.store.read(self.call["id"], "team-a")[0]["text"], "Please help")
        with self.assertRaises(CallNotFound):
            self.store.read(self.call["id"], "team-b")

    def test_revocation_deletes_transcript_and_blocks_further_retention(self):
        self.store.consent(self.call["id"], "team-a", transcript=True, recording=False,
                           attestation="owned fixture consent", now=101)
        self.store.append(self.call["id"], "team-a", "agent", "How can I help?", now=102)
        self.store.consent(self.call["id"], "team-a", transcript=False, recording=False,
                           attestation="caller revoked retention", now=103)
        self.assertEqual(self.store.read(self.call["id"], "team-a"), [])
        self.assertNotIn("How can", str(self.ledger.events(self.call["id"], "team-a")))
        with self.assertRaises(InvalidCall):
            self.store.append(self.call["id"], "team-a", "agent", "anything", now=104)

    def test_retention_expiry_and_input_bounds(self):
        self.store.consent(self.call["id"], "team-a", transcript=True, recording=False,
                           attestation="owned fixture consent", now=101)
        self.store.append(self.call["id"], "team-a", "caller", "hello", now=102)
        self.assertEqual(self.store.purge(before=103), 1)
        for bad in ("", "x" * 4001, "bad\x00text"):
            with self.assertRaises(InvalidCall):
                self.store.append(self.call["id"], "team-a", "caller", bad, now=104)
