import asyncio
import tempfile
import unittest
from pathlib import Path

from voiceops.ledger import CallLedger, InvalidCall
from voiceops.transfers import TransferService
from voiceops.supervisor import Supervisor


class Gateway:
    def __init__(self):
        self.present = True
        self.transfers = []
        self.hangups = 0
        self.fail = False

    async def active(self, room, participant):
        if self.fail:
            raise ConnectionError("offline")
        return self.present

    async def hangup(self, room, participant):
        self.hangups += 1
        self.present = False

    async def transfer(self, room, participant, uri):
        self.transfers.append(uri)
        if self.fail:
            raise TimeoutError("response lost")


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = CallLedger(Path(self.directory.name) / "calls.sqlite")
        row = self.ledger.admit(owner="team-a", caller="1001", room="one", participant="sip-1", now=100)
        self.call = self.ledger.transition(row["id"], "team-a", "answered", now=101, version=1)
        self.transfers = TransferService(self.ledger, targets={"operator": "sip:operator@127.0.0.1:18160"})
        self.gateway = Gateway()
        self.supervisor = Supervisor(self.ledger, self.transfers, self.gateway, clock=lambda: 110)

    async def test_api_ack_is_pending_until_independent_media_reconciliation(self):
        proposal = self.transfers.propose(self.call["id"], "team-a", "operator", now=102)
        self.transfers.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=103)
        await self.supervisor.dispatch(proposal["id"], "team-a")
        self.assertEqual(len(self.gateway.transfers), 1)
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "transferring")
        with self.assertRaises(InvalidCall):
            await self.supervisor.dispatch(proposal["id"], "team-a")

    async def test_lost_transfer_response_is_not_retried(self):
        proposal = self.transfers.propose(self.call["id"], "team-a", "operator", now=102)
        self.transfers.approve(proposal["id"], "team-a", proposal["digest"], "supervisor", now=103)
        self.gateway.fail = True
        result = await self.supervisor.dispatch(proposal["id"], "team-a")
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertEqual(len(self.gateway.transfers), 1)

    async def test_restart_reconciles_absent_participant_without_recreating_dialog(self):
        self.gateway.present = False
        report = await self.supervisor.reconcile("team-a")
        self.assertEqual(report["ended"], [self.call["id"]])
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "ended")
        self.assertEqual(self.gateway.transfers, [])

    async def test_network_failure_does_not_claim_the_call_ended(self):
        self.gateway.fail = True
        report = await self.supervisor.reconcile("team-a")
        self.assertEqual(report["unavailable"], [self.call["id"]])
        self.assertEqual(self.ledger.get(self.call["id"], "team-a")["status"], "answered")

    async def test_drain_waits_before_deadline_then_observes_actual_hangup(self):
        self.ledger.begin_drain(now=102, deadline=109)
        self.supervisor.clock = lambda: 105
        self.assertEqual((await self.supervisor.enforce_drain("team-a"))["ended"], [])
        self.assertEqual(self.gateway.hangups, 0)
        self.supervisor.clock = lambda: 110
        result = await self.supervisor.enforce_drain("team-a")
        self.assertEqual(result["ended"], [self.call["id"]])
        self.assertEqual(self.gateway.hangups, 1)
