"""Bounded external actions with durable intent and observed reconciliation."""
import asyncio
import time

from .ledger import InvalidCall


class Supervisor:
    def __init__(self, ledger, transfers, gateway, *, clock=time.time, timeout=8):
        self.ledger, self.transfers, self.gateway = ledger, transfers, gateway
        self.clock, self.timeout = clock, timeout

    def _event(self, call_id, kind, data):
        with self.ledger.transaction() as db:
            self.ledger._event(db, call_id, kind, self.clock(), data)

    def _session(self, call):
        for event in reversed(self.ledger.events(call["id"], call["owner"])):
            if event["kind"] == "sip_dialog_observed":
                return {"expected_sid": event["data"]["participant_sid"]}
        return {}  # Pure ledger fixtures do not contain a SIP observation.

    async def dispatch(self, transfer_id, owner):
        operation = self.transfers.claim(transfer_id, owner, now=self.clock())
        call = self.ledger.get(operation["call_id"], owner)
        try:
            await asyncio.wait_for(self.gateway.transfer(call["room"], call["participant"],
                                                         operation["target_uri"], **self._session(call)), self.timeout)
        except Exception:
            return self.transfers.mark_unknown(transfer_id, owner, "lost_response", now=self.clock())
        self._event(call["id"], "transfer_api_acknowledged", {"transfer_id": transfer_id})
        return operation  # API acknowledgement is not answered-call or media evidence.

    async def reconcile(self, owner):
        report = {"ended": [], "active": [], "unavailable": []}
        for call in self.ledger.list_calls(owner):
            if call["status"] in {"ended", "failed", "transferred"}:
                continue
            try:
                active = await asyncio.wait_for(self.gateway.active(call["room"], call["participant"], **self._session(call)), self.timeout)
                if active:
                    report["active"].append(call["id"])
                else:
                    self.ledger.transition(call["id"], owner, "ended", now=self.clock(), version=call["version"])
                    report["ended"].append(call["id"])
            except Exception:
                report["unavailable"].append(call["id"])
        return report

    async def hangup(self, call_id, owner):
        call = self.ledger.get(call_id, owner)
        if call["status"] in {"ended", "failed", "transferred"}:
            raise InvalidCall("Call is already terminal")
        self._event(call_id, "hangup_requested", {})
        await asyncio.wait_for(self.gateway.hangup(call["room"], call["participant"], **self._session(call)), self.timeout)
        if await asyncio.wait_for(self.gateway.active(call["room"], call["participant"], **self._session(call)), self.timeout):
            raise InvalidCall("Hangup is not yet confirmed")
        current = self.ledger.get(call_id, owner)
        if current["status"] not in {"ended", "failed", "transferred"}:
            return self.ledger.transition(call_id, owner, "ended", now=self.clock(), version=current["version"])
        return current

    async def enforce_drain(self, owner):
        status = self.ledger.drain_status(now=self.clock())
        report = {"ended": [], "unavailable": []}
        owned = {call["id"] for call in self.ledger.list_calls(owner)}
        for call_id in status["needs_termination"]:
            if call_id not in owned:
                continue
            try:
                await self.hangup(call_id, owner)
                report["ended"].append(call_id)
            except Exception:
                report["unavailable"].append(call_id)
        return report
