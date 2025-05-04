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
        if target not in self.targets:
            raise InvalidCall("Unknown transfer target")
        with self.ledger.transaction() as db:
            call = self.ledger._owned(db, call_id, owner)
            if call["status"] != "answered":
                raise InvalidCall("Call must be answered before transfer")
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
