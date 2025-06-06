"""Bind reusable SIP identities to individual RTC participant sessions."""
from .ledger import InvalidCall


class CallRegistry:
    def __init__(self, ledger, owner, room):
        self.ledger, self.owner, self.room = ledger, owner, room
        self.calls, self.sessions = {}, {}

    def matches(self, participant):
        """Ignore late media/control callbacks from a replaced RTC session."""
        return self.sessions.get(participant.identity) == participant.sid

    def admit(self, participant, *, now):
        if "sip.callID" not in participant.attributes:
            return None
        identity, session = participant.identity, participant.sid
        if identity in self.calls:
            current = self.ledger.get(self.calls[identity], self.owner)
            if self.sessions[identity] == session:
                if current["status"] in {"ended", "failed", "transferred"}:
                    raise InvalidCall("A terminated dialog cannot be readmitted")
                return current["id"]
            if current["status"] not in {"ended", "failed", "transferred"}:
                self.ledger.transition(current["id"], self.owner, "ended", now=now, version=current["version"])
            self.calls.pop(identity)
            self.sessions.pop(identity)
        call = self.ledger.admit(owner=self.owner, caller=participant.attributes.get("sip.phoneNumber", ""),
                                 room=self.room, participant=identity, now=now)
        self.calls[identity], self.sessions[identity] = call["id"], session
        with self.ledger.transaction() as db:
            self.ledger._event(db, call["id"], "sip_dialog_observed", now,
                              {"sip_call_id": participant.attributes.get("sip.callIDFull"),
                               "bridge_call_id": participant.attributes["sip.callID"],
                               "participant_sid": session})
        return call["id"]

    def disconnect(self, participant, *, now):
        identity = participant.identity
        if self.sessions.get(identity) != participant.sid:
            return None
        call_id = self.calls.pop(identity)
        self.sessions.pop(identity)
        call = self.ledger.get(call_id, self.owner)
        if call["status"] not in {"ended", "failed", "transferred"}:
            self.ledger.transition(call_id, self.owner, "ended", now=now, version=call["version"])
        return call_id
