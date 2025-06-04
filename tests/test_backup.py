import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger
from voiceops.storage import backup_database, restore_database


class BackupTests(unittest.TestCase):
    def test_wal_snapshot_restores_calls_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = CallLedger(root / "live.sqlite")
            call = ledger.admit(owner="one", caller="1001", room="room", participant="sip", now=100)
            with ledger.connection() as live:
                live.execute("PRAGMA wal_autocheckpoint=0")
                manifest = backup_database(ledger.path, root / "snapshot.sqlite")
                self.assertEqual(manifest["integrity"], "ok")
            restored = restore_database(root / "snapshot.sqlite", root / "restored.sqlite")
            self.assertEqual(CallLedger(restored).get(call["id"], "one"), ledger.get(call["id"], "one"))
            with self.assertRaises(FileExistsError):
                restore_database(root / "snapshot.sqlite", ledger.path)

    def test_corrupt_or_replaced_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = CallLedger(root / "live.sqlite")
            backup_database(ledger.path, root / "snapshot.sqlite")
            with (root / "snapshot.sqlite").open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaises(ValueError):
                restore_database(root / "snapshot.sqlite", root / "restored.sqlite")
            self.assertFalse((root / "restored.sqlite").exists())
