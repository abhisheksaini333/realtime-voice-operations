import tempfile
import unittest
from pathlib import Path

from voiceops.worker import worker_lock


class WorkerLockTests(unittest.TestCase):
    def test_only_one_worker_can_own_a_ledger_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "call.sqlite"
            with worker_lock(path):
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    with worker_lock(path):
                        self.fail("second worker entered")
            with worker_lock(path):
                pass
