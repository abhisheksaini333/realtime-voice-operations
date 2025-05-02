"""Exact operator proposals and durable transfer operation records."""
import hashlib
import json
import re
import uuid

from .ledger import InvalidCall, identifier, timestamp


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
