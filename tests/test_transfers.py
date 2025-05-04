import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, InvalidCall
from voiceops.transfers import TransferService


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        call = self.ledger.admit(owner="team-a", caller="1001", room="one",
                                 participant="sip-1", now=100)
        self.call = self.ledger.transition(call["id"], "team-a", "answered", now=101, version=1)
        self.service = TransferService(self.ledger, targets={"operator": "sip:operator@127.0.0.1:18160"})

    def test_proposal_binds_target_and_call_version(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        self.assertEqual(proposal["target"], "operator")
        self.assertEqual(proposal["call_version"], 2)
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(proposal["expires_at"], 162)

    def test_destination_must_be_configured_and_call_answered(self):
        with self.assertRaisesRegex(InvalidCall, "target"):
            self.service.propose(self.call["id"], "team-a", "sip:attacker@example.org", now=102)
        self.ledger.transition(self.call["id"], "team-a", "ended", now=103, version=2)
        with self.assertRaisesRegex(InvalidCall, "answered"):
            self.service.propose(self.call["id"], "team-a", "operator", now=104)

    def test_second_pending_proposal_is_rejected(self):
        self.service.propose(self.call["id"], "team-a", "operator", now=102)
        with self.assertRaisesRegex(InvalidCall, "pending"):
            self.service.propose(self.call["id"], "team-a", "operator", now=103)

    def test_approval_requires_exact_unexpired_digest(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        with self.assertRaisesRegex(InvalidCall, "digest"):
            self.service.approve(proposal["id"], "team-a", "changed", "supervisor", now=103)
        approved = self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=104)
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["approver"], "supervisor")

    def test_expired_or_changed_call_cannot_be_approved(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        with self.assertRaisesRegex(InvalidCall, "expired"):
            self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=162)
        self.ledger.transition(self.call["id"], "team-a", "ended", now=103, version=2)
        with self.assertRaisesRegex(InvalidCall, "changed"):
            self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=104)
