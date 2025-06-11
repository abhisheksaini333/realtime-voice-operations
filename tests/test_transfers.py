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

    def test_dispatch_claim_is_durable_and_cannot_be_replayed(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        with self.assertRaisesRegex(InvalidCall, "approved"):
            self.service.claim(proposal["id"], "team-a", now=103)
        self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=104)
        operation = self.service.claim(proposal["id"], "team-a", now=105)
        self.assertEqual(operation["state"], "dispatched")
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "transferring")
        restarted = TransferService(CallLedger(self.ledger.path), targets=self.service.targets)
        with self.assertRaises(InvalidCall):
            restarted.claim(proposal["id"], "team-a", now=106)

    def test_destination_configuration_change_invalidates_dispatch(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=104)
        self.service.targets["operator"] = "sip:other@127.0.0.1:18160"
        with self.assertRaisesRegex(InvalidCall, "destination changed"):
            self.service.claim(proposal["id"], "team-a", now=105)

    def dispatched(self):
        proposal = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        self.service.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=103)
        return self.service.claim(proposal["id"], "team-a", now=104)

    def test_lost_response_is_quarantined_and_never_automatically_replayed(self):
        operation = self.dispatched()
        uncertain = self.service.mark_unknown(operation["id"], "team-a", "lost_response", now=105)
        self.assertEqual(uncertain["state"], "outcome_unknown")
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "transfer_unknown")
        with self.assertRaises(InvalidCall):
            self.service.claim(operation["id"], "team-a", now=106)

    def test_worker_recovery_marks_only_overdue_dispatched_operations(self):
        operation = self.dispatched()
        self.assertEqual(self.service.recover_dispatched(now=110, older_than=90), [])
        recovered = self.service.recover_dispatched(now=210, older_than=200)
        self.assertEqual(recovered, [operation["id"]])
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "transfer_unknown")

    def test_recent_interrupted_dispatch_becomes_uncertain_on_later_poll(self):
        import json
        operation = self.dispatched()
        self.assertEqual(self.service.recover_dispatched(now=110, older_than=100), [])
        self.assertEqual(self.service.recover_dispatched(now=115, older_than=105, reason="timeout"), [operation["id"]])
        with self.ledger.connection() as db:
            row = dict(db.execute("SELECT * FROM transfers WHERE id=?", (operation["id"],)).fetchone())
        self.assertEqual(json.loads(row["evidence"])["reason"], "timeout")
        self.assertEqual(self.service.recover_dispatched(now=120, older_than=110, reason="timeout"), [])
        with self.assertRaises(InvalidCall):
            self.service.claim(operation["id"], "team-a", now=121)

    def test_success_requires_second_dialog_and_both_audio_directions(self):
        operation = self.dispatched()
        for evidence in ({"api_ok": True}, {"dialog_id": "second", "answered": True,
                                            "audio_sent": 160, "audio_received": 0}):
            with self.assertRaises(InvalidCall):
                self.service.reconcile(operation["id"], "team-a", "connected", evidence, now=105)
        result = self.service.reconcile(operation["id"], "team-a", "connected",
                                        {"dialog_id": "second", "answered": True,
                                         "audio_sent": 160, "audio_received": 160}, now=106)
        self.assertEqual(result["state"], "connected")
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "transferred")

    def test_rejected_transfer_restores_call_only_with_current_media_evidence(self):
        operation = self.dispatched()
        self.service.mark_unknown(operation["id"], "team-a", "timeout", now=105)
        result = self.service.reconcile(operation["id"], "team-a", "rejected",
                                        {"original_active": True, "audio_sent": 160,
                                         "audio_received": 160}, now=106)
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "answered")

    def test_reconciliation_does_not_resurrect_a_terminated_call(self):
        operation = self.dispatched()
        call = self.ledger.get(self.call["id"], "team-a")
        self.ledger.transition(call["id"], "team-a", "ended", version=call["version"], now=105)
        self.service.reconcile(operation["id"], "team-a", "unanswered",
                               {"original_active": False, "audio_sent": 0, "audio_received": 0}, now=106)
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "ended")

    def test_expired_proposal_does_not_permanently_block_future_transfers(self):
        old = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        fresh = self.service.propose(self.call["id"], "team-a", "operator", now=163)
        self.assertNotEqual(old["id"], fresh["id"])
        with self.ledger.connection() as db:
            self.assertEqual(db.execute("SELECT state FROM transfers WHERE id=?", (old["id"],)).fetchone()[0], "expired")

    def test_undispatched_approval_can_be_canceled_but_dispatched_operation_cannot(self):
        old = self.service.propose(self.call["id"], "team-a", "operator", now=102)
        self.service.cancel(old["id"], "team-a", now=103)
        operation = self.dispatched()
        with self.assertRaises(InvalidCall):
            self.service.cancel(operation["id"], "team-a", now=105)

    def test_non_string_target_is_a_validation_error(self):
        with self.assertRaises(InvalidCall):
            self.service.propose(self.call["id"], "team-a", {}, now=102)
