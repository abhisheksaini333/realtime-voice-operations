"""Correlated media and DTMF observations from the trusted call worker."""

from .ledger import InvalidCall, timestamp


class Observations:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS media (
                call_id TEXT PRIMARY KEY REFERENCES calls(id),
                received_samples INTEGER NOT NULL DEFAULT 0,
                sent_samples INTEGER NOT NULL DEFAULT 0,
                last_received REAL, last_sent REAL, observed_at REAL NOT NULL
            )"""
            )

    def media(self, call_id, owner, *, received, sent, now):
        now = timestamp(now)
        if any(
            type(value) is not int or not 0 <= value <= 480000
            for value in (received, sent)
        ):
            raise InvalidCall("Invalid audio sample counts")
        with self.ledger.transaction() as db:
            call = self.ledger._owned(db, call_id, owner)
            previous = db.execute(
                "SELECT observed_at FROM media WHERE call_id=?", (call_id,)
            ).fetchone()
            if now < (previous[0] if previous else call["created_at"]):
                raise InvalidCall("Media observation is stale")
            db.execute(
                """INSERT INTO media VALUES(?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET
                received_samples=media.received_samples+excluded.received_samples,
                sent_samples=media.sent_samples+excluded.sent_samples,
                last_received=coalesce(excluded.last_received,media.last_received),
                last_sent=coalesce(excluded.last_sent,media.last_sent),observed_at=excluded.observed_at""",
                (
                    call_id,
                    received,
                    sent,
                    now if received else None,
                    now if sent else None,
                    now,
                ),
            )

    def health(self, call_id, owner, *, now, timeout=10, outbound_policy="continuous"):
        now = timestamp(now)
        if outbound_policy not in {"continuous", "intermittent"}:
            raise InvalidCall("Invalid outbound media policy")
        if not 1 <= timeout <= 300:
            raise InvalidCall("Invalid media timeout")
        with self.ledger.connection() as db:
            call = self.ledger._owned(db, call_id, owner)
            row = db.execute(
                "SELECT * FROM media WHERE call_id=?", (call_id,)
            ).fetchone()
            result = (
                dict(row)
                if row
                else {
                    "received_samples": 0,
                    "sent_samples": 0,
                    "last_received": None,
                    "last_sent": None,
                }
            )
        result["outbound_policy"] = outbound_policy
        monitored = (
            ("last_received", "last_sent")
            if outbound_policy == "continuous"
            else ("last_received",)
        )
        result["stale"] = any(
            now - (result[key] if result[key] is not None else call["created_at"])
            >= timeout
            for key in monitored
        )
        return result

    def dtmf(self, call_id, owner, participant, digit, *, now):
        now = timestamp(now)
        if (
            not isinstance(digit, str)
            or len(digit) != 1
            or digit not in "0123456789*#ABCD"
        ):
            raise InvalidCall("Expected one DTMF digit")
        with self.ledger.transaction() as db:
            call = self.ledger._owned(db, call_id, owner)
            if participant != call["participant"]:
                raise InvalidCall("DTMF participant does not match call")
            self.ledger._event(
                db,
                call_id,
                "dtmf_received",
                now,
                {"digit": digit, "participant": participant},
            )
