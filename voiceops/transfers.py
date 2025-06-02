"""Exact operator proposals and durable transfer operation records."""
import hashlib
import hmac
import json
import re
import uuid

from .ledger import CallNotFound, InvalidCall, identifier, timestamp


class TransferService:
    def __init__(self, ledger, *, targets):
        self.ledger = ledger
        self.targets = dict(targets)
        for name, uri in self.targets.items():
            identifier(name, "target name")
            if not isinstance(uri, str) or not re.fullmatch(
                r"sip:[A-Za-z0-9+._-]{1,64}@(127\.0\.0\.1|localhost):[0-9]{1,5}", uri
            ) or not 1 <= int(uri.rsplit(":", 1)[1]) <= 65535:
                raise InvalidCall("Transfer target must be a configured local SIP endpoint")
        with ledger.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS transfers (
                    id TEXT PRIMARY KEY, call_id TEXT NOT NULL REFERENCES calls(id),
                    owner TEXT NOT NULL, target TEXT NOT NULL, target_uri TEXT NOT NULL,
                    call_version INTEGER NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    approver TEXT, dispatched_at REAL, completed_at REAL, evidence TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_transfer ON transfers(call_id)
                  WHERE state IN ('proposed','approved','dispatched','outcome_unknown');
            """)

    def propose(self, call_id, owner, target, *, now):
        now = timestamp(now)
        if not isinstance(target, str) or target not in self.targets:
            raise InvalidCall("Unknown transfer target")
        with self.ledger.transaction() as db:
            call = self.ledger._owned(db, call_id, owner)
            if call["status"] != "answered":
                raise InvalidCall("Call must be answered before transfer")
            expired = db.execute("SELECT id FROM transfers WHERE call_id=? AND state IN "
                                 "('proposed','approved') AND expires_at<=?", (call_id, now)).fetchall()
            for operation in expired:
                db.execute("UPDATE transfers SET state='expired',completed_at=? WHERE id=?", (now, operation[0]))
                self.ledger._event(db, call_id, "transfer_expired", now, {"transfer_id": operation[0]})
            if db.execute("SELECT 1 FROM transfers WHERE call_id=? AND state IN "
                          "('proposed','approved','dispatched','outcome_unknown')", (call_id,)).fetchone():
                raise InvalidCall("Call already has a pending transfer")
            payload = {"call_id": call_id, "version": call["version"],
                       "target": target, "target_uri": self.targets[target]}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            transfer_id = uuid.uuid4().hex
            db.execute("""INSERT INTO transfers
                (id,call_id,owner,target,target_uri,call_version,digest,state,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,'proposed',?,?)""",
                       (transfer_id, call_id, owner, target, self.targets[target],
                        call["version"], digest, now, now + 60))
            self.ledger._event(db, call_id, "transfer_proposed", now,
                               {"transfer_id": transfer_id, "target": target, "digest": digest})
            return dict(db.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone())

    @staticmethod
    def _owned(db, transfer_id, owner):
        row = db.execute("SELECT * FROM transfers WHERE id=? AND owner=?", (transfer_id, owner)).fetchone()
        if row is None:
            raise CallNotFound("Transfer unavailable")
        return dict(row)

    def _assert_current(self, db, proposal, now):
        if now >= proposal["expires_at"]:
            raise InvalidCall("Transfer approval expired")
        if now < proposal["created_at"]:
            raise InvalidCall("Approval predates proposal")
        call = self.ledger._owned(db, proposal["call_id"], proposal["owner"])
        if call["status"] != "answered" or call["version"] != proposal["call_version"]:
            raise InvalidCall("Call changed after proposal")
        if self.targets.get(proposal["target"]) != proposal["target_uri"]:
            raise InvalidCall("Transfer destination changed after proposal")
        return call

    def approve(self, transfer_id, owner, digest, approver, *, now):
        now = timestamp(now)
        identifier(approver, "approver")
        with self.ledger.transaction() as db:
            proposal = self._owned(db, transfer_id, owner)
            if not isinstance(digest, str) or not hmac.compare_digest(digest, proposal["digest"]):
                raise InvalidCall("Transfer digest does not match")
            self._assert_current(db, proposal, now)
            if proposal["state"] != "proposed":
                raise InvalidCall("Transfer is not awaiting approval")
            db.execute("UPDATE transfers SET state='approved',approver=? WHERE id=?", (approver, transfer_id))
            self.ledger._event(db, proposal["call_id"], "transfer_approved", now,
                               {"transfer_id": transfer_id, "approver": approver, "digest": digest})
            return self._owned(db, transfer_id, owner)

    def cancel(self, transfer_id, owner, *, now):
        now = timestamp(now)
        with self.ledger.transaction() as db:
            proposal = self._owned(db, transfer_id, owner)
            if proposal["state"] not in {"proposed", "approved"} or now < proposal["created_at"]:
                raise InvalidCall("Only an undispatched proposal can be canceled")
            db.execute("UPDATE transfers SET state='canceled',completed_at=? WHERE id=?", (now, transfer_id))
            self.ledger._event(db, proposal["call_id"], "transfer_canceled", now, {"transfer_id": transfer_id})
            return self._owned(db, transfer_id, owner)

    def claim(self, transfer_id, owner, *, now):
        """Record dispatch before any external request; a claim is never replayable."""
        now = timestamp(now)
        with self.ledger.transaction() as db:
            proposal = self._owned(db, transfer_id, owner)
            if proposal["state"] != "approved":
                raise InvalidCall("Transfer must be approved and not previously dispatched")
            self._assert_current(db, proposal, now)
            db.execute("UPDATE transfers SET state='dispatched',dispatched_at=? WHERE id=?", (now, transfer_id))
            db.execute("UPDATE calls SET status='transferring',updated_at=?,version=version+1 WHERE id=?",
                       (now, proposal["call_id"]))
            self.ledger._event(db, proposal["call_id"], "transfer_dispatched", now,
                               {"transfer_id": transfer_id, "target": proposal["target"]})
            return self._owned(db, transfer_id, owner)

    def _unknown(self, db, proposal, reason, now):
        if proposal["state"] != "dispatched":
            raise InvalidCall("Only a dispatched transfer can have an unknown outcome")
        if now < proposal["dispatched_at"]:
            raise InvalidCall("Observation predates dispatch")
        db.execute("UPDATE transfers SET state='outcome_unknown',evidence=? WHERE id=?",
                   (json.dumps({"reason": reason}), proposal["id"]))
        db.execute("UPDATE calls SET status='transfer_unknown',updated_at=?,version=version+1 "
                   "WHERE id=? AND status='transferring'", (now, proposal["call_id"]))
        self.ledger._event(db, proposal["call_id"], "transfer_outcome_unknown", now,
                           {"transfer_id": proposal["id"], "reason": reason})
        return self._owned(db, proposal["id"], proposal["owner"])

    def mark_unknown(self, transfer_id, owner, reason, *, now):
        now = timestamp(now)
        if reason not in {"timeout", "lost_response", "worker_restart"}:
            raise InvalidCall("Unknown uncertainty reason")
        with self.ledger.transaction() as db:
            return self._unknown(db, self._owned(db, transfer_id, owner), reason, now)

    def recover_dispatched(self, *, now, older_than):
        now, older_than = timestamp(now), timestamp(older_than)
        if older_than > now:
            raise InvalidCall("Recovery cutoff cannot be in the future")
        with self.ledger.transaction() as db:
            pending = db.execute("SELECT * FROM transfers WHERE state='dispatched' "
                                 "AND dispatched_at<=? ORDER BY dispatched_at,id", (older_than,)).fetchall()
            for proposal in pending:
                self._unknown(db, dict(proposal), "worker_restart", now)
            return [proposal["id"] for proposal in pending]

    def reconcile(self, transfer_id, owner, outcome, evidence, *, now):
        """Apply observations from the trusted media worker, never browser claims."""
        now = timestamp(now)
        if outcome not in {"connected", "rejected", "unanswered"} or not isinstance(evidence, dict):
            raise InvalidCall("Invalid transfer observation")
        media = all(type(evidence.get(key)) is int and evidence[key] > 0
                    for key in ("audio_sent", "audio_received"))
        if outcome == "connected":
            identifier(evidence.get("dialog_id"), "second dialog")
            if evidence.get("answered") is not True or not media:
                raise InvalidCall("Transfer success requires answered second dialog and bidirectional audio")
        elif type(evidence.get("original_active")) is not bool:
            raise InvalidCall("Original call observation is required")
        elif evidence["original_active"] and not media:
            raise InvalidCall("Restoring the original call requires current bidirectional audio")
        with self.ledger.transaction() as db:
            proposal = self._owned(db, transfer_id, owner)
            if proposal["state"] not in {"dispatched", "outcome_unknown"}:
                raise InvalidCall("Transfer is not awaiting reconciliation")
            call = self.ledger._owned(db, proposal["call_id"], owner)
            if now < max(proposal["dispatched_at"], call["updated_at"]):
                raise InvalidCall("Observation predates latest call state")
            db.execute("UPDATE transfers SET state=?,completed_at=?,evidence=? WHERE id=?",
                       (outcome, now, json.dumps(evidence), transfer_id))
            if call["status"] in {"transferring", "transfer_unknown"}:
                state = "transferred" if outcome == "connected" else (
                    "answered" if evidence["original_active"] else "ended")
                db.execute("UPDATE calls SET status=?,updated_at=?,version=version+1 WHERE id=?",
                           (state, now, call["id"]))
            self.ledger._event(db, call["id"], "transfer_reconciled", now,
                               {"transfer_id": transfer_id, "outcome": outcome, **evidence})
            return self._owned(db, transfer_id, owner)
