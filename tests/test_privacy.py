import tempfile
import unittest
import io
import wave
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

    def test_recording_requires_separate_consent_and_revocation_removes_audio(self):
        pcm = b"\x10\x00" * 160
        with self.assertRaises(InvalidCall):
            self.store.record(self.call["id"], "team-a", pcm, sample_rate=8000, now=101)
        self.store.consent(self.call["id"], "team-a", transcript=False, recording=True,
                           attestation="owned caller agreed to audio retention", now=102)
        self.store.record(self.call["id"], "team-a", pcm, sample_rate=8000, now=103)
        audio = self.store.recording(self.call["id"], "team-a")
        with wave.open(io.BytesIO(audio)) as reader:
            self.assertEqual((reader.getframerate(), reader.getnframes()), (8000, 160))
            self.assertEqual(reader.readframes(160), pcm)
        with self.assertRaises(CallNotFound):
            self.store.recording(self.call["id"], "team-b")
        self.store.consent(self.call["id"], "team-a", transcript=False, recording=False,
                           attestation="caller withdrew audio retention", now=104)
        with self.assertRaises(CallNotFound):
            self.store.recording(self.call["id"], "team-a")

    def test_recording_duration_is_bounded(self):
        self.store.consent(self.call["id"], "team-a", transcript=False, recording=True,
                           attestation="owned fixture consent", now=101)
        with self.assertRaises(InvalidCall):
            self.store.record(self.call["id"], "team-a", b"\0" * 960002, sample_rate=8000, now=102)
