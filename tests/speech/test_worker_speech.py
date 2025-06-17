import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from voiceops.ledger import CallLedger
from voiceops.privacy import PrivacyStore
from voiceops.registry import CallRegistry
from voiceops import speech


class SpeechWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = CallLedger(Path(self.tmp.name) / "calls.sqlite")
        self.privacy = PrivacyStore(self.ledger)
        self.registry = CallRegistry(self.ledger, "team", "room")
        self.participant = SimpleNamespace(
            identity="sip-one",
            sid="PA_1",
            attributes={"sip.callID": "SCL_1", "sip.phoneNumber": "1001"},
        )
        self.call_id = self.registry.admit(self.participant, now=100)

    def test_transcript_hooks_require_consent_and_current_participant_session(self):
        self.assertTrue(
            hasattr(speech, "SpeechHooks"), "worker consent integration is missing"
        )
        hook = speech.SpeechHooks(
            self.ledger,
            self.privacy,
            self.registry,
            self.participant,
            self.call_id,
            "team",
            clock=lambda: 102,
        )
        hook.transcript("caller", "I need help")
        self.assertEqual(self.privacy.read(self.call_id, "team"), [])
        self.privacy.consent(
            self.call_id,
            "team",
            transcript=True,
            recording=False,
            attestation="Synthetic test consent",
            now=101,
        )
        hook.transcript("caller", "I need help")
        self.assertEqual(len(self.privacy.read(self.call_id, "team")), 1)
        self.registry.sessions[self.participant.identity] = "PA_replacement"
        hook.transcript("agent", "Old session reply")
        self.assertEqual(len(self.privacy.read(self.call_id, "team")), 1)

    def test_observations_drop_transcript_text_and_never_count_generated_audio_as_sent(
        self,
    ):
        self.assertTrue(
            hasattr(speech, "SpeechHooks"), "worker metadata integration is missing"
        )
        hook = speech.SpeechHooks(
            self.ledger,
            self.privacy,
            self.registry,
            self.participant,
            self.call_id,
            "team",
            clock=lambda: 102,
        )
        hook.observe(
            "tts", {"latency_ms": 12, "audio_ms": 1000, "text": "private data"}
        )
        row = self.ledger.events(self.call_id, "team")[-1]
        self.assertEqual(row["kind"], "speech_tts")
        self.assertNotIn("private data", json.dumps(row))
        self.assertEqual(row["data"], {"latency_ms": 12, "audio_ms": 1000})

    def test_configuration_rejects_unknown_keys_and_oversized_files(self):
        self.assertTrue(
            hasattr(speech, "load_config"), "worker speech config is missing"
        )
        path = Path(self.tmp.name) / "speech.json"
        path.write_text('{"untrusted":"value"}')
        with self.assertRaises(ValueError):
            speech.load_config(path)
        path.write_text(" " * 4097)
        with self.assertRaises(ValueError):
            speech.load_config(path)
