import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, InvalidCall


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


if __name__ == "__main__":
    unittest.main()
