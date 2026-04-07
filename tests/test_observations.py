import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, InvalidCall
from voiceops.observations import Observations


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        self.call = self.ledger.admit(
            owner="team-a", caller="1001", room="one", participant="sip-1", now=100
        )
        self.observations = Observations(self.ledger)

    def test_media_counters_are_monotonic_and_detect_one_way_audio(self):
        self.observations.media(
            self.call["id"], "team-a", received=160, sent=0, now=101
        )
        first = self.observations.health(self.call["id"], "team-a", now=112, timeout=10)
        self.assertTrue(first["stale"])
        self.observations.media(
            self.call["id"], "team-a", received=160, sent=160, now=113
        )
        healthy = self.observations.health(
            self.call["id"], "team-a", now=114, timeout=10
        )
        self.assertFalse(healthy["stale"])
        self.assertEqual(healthy["received_samples"], 320)
        self.assertEqual(healthy["sent_samples"], 160)
        with self.assertRaises(InvalidCall):
            self.observations.media(
                self.call["id"], "team-a", received=-1, sent=0, now=115
            )

    def test_dtmf_is_bound_to_the_actual_participant(self):
        with self.assertRaises(InvalidCall):
            self.observations.dtmf(self.call["id"], "team-a", "other", "5", now=101)
        self.observations.dtmf(self.call["id"], "team-a", "sip-1", "5", now=101)
        self.assertEqual(
            self.ledger.events(self.call["id"], "team-a")[-1]["data"],
            {"digit": "5", "participant": "sip-1"},
        )
        with self.assertRaises(InvalidCall):
            self.observations.dtmf(self.call["id"], "team-a", "sip-1", "1234", now=102)

    def test_stale_media_observation_does_not_move_health_backwards(self):
        self.observations.media(
            self.call["id"], "team-a", received=160, sent=160, now=105
        )
        with self.assertRaises(InvalidCall):
            self.observations.media(
                self.call["id"], "team-a", received=160, sent=160, now=104
            )

    def test_speech_outbound_silence_uses_explicit_intermittent_policy(self):
        self.observations.media(
            self.call["id"], "team-a", received=160, sent=0, now=120
        )
        self.assertTrue(
            self.observations.health(self.call["id"], "team-a", now=121)["stale"]
        )
        speech = self.observations.health(
            self.call["id"], "team-a", now=121, outbound_policy="intermittent"
        )
        self.assertFalse(speech["stale"])
        self.assertEqual(speech["sent_samples"], 0)
        self.assertEqual(speech["outbound_policy"], "intermittent")
        self.assertTrue(
            self.observations.health(
                self.call["id"], "team-a", now=131, outbound_policy="intermittent"
            )["stale"]
        )
        with self.assertRaises(InvalidCall):
            self.observations.health(
                self.call["id"], "team-a", now=121, outbound_policy="disabled"
            )

    def test_health_rejects_invalid_timeouts_and_clocks_before_observations(self):
        for timeout in (True, "10", None, float("inf"), float("nan"), 0):
            with self.subTest(timeout=timeout), self.assertRaises(InvalidCall):
                self.observations.health(self.call["id"], "team-a", now=101, timeout=timeout)
        with self.assertRaises(InvalidCall):
            self.observations.health(self.call["id"], "team-a", now=99)
        self.observations.media(self.call["id"], "team-a", received=160, sent=160, now=110)
        with self.assertRaises(InvalidCall):
            self.observations.health(self.call["id"], "team-a", now=109)
        self.assertFalse(self.observations.health(self.call["id"], "team-a", now=110, timeout=1.5)["stale"])

    def test_stale_dtmf_cannot_reorder_call_history(self):
        self.ledger.transition(self.call["id"], "team-a", "answered", now=110, version=1)
        before = self.ledger.events(self.call["id"], "team-a")
        with self.assertRaises(InvalidCall):
            self.observations.dtmf(self.call["id"], "team-a", "sip-1", "5", now=109)
        self.assertEqual(self.ledger.events(self.call["id"], "team-a"), before)
        self.observations.dtmf(self.call["id"], "team-a", "sip-1", "5", now=120)
        before = self.ledger.events(self.call["id"], "team-a")
        with self.assertRaises(InvalidCall):
            self.observations.dtmf(self.call["id"], "team-a", "sip-1", "6", now=119)
        self.assertEqual(self.ledger.events(self.call["id"], "team-a"), before)
        self.observations.dtmf(self.call["id"], "team-a", "sip-1", "7", now=120)
        self.assertEqual(self.ledger.events(self.call["id"], "team-a")[-1]["data"]["digit"], "7")
