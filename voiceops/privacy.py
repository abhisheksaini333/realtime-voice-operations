"""Explicit consent and bounded, owner-scoped transcript retention."""
from .ledger import InvalidCall, timestamp


class PrivacyStore:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS consent (
                    call_id TEXT PRIMARY KEY REFERENCES calls(id),
                    transcript INTEGER NOT NULL, recording INTEGER NOT NULL,
                    attestation TEXT NOT NULL, observed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transcripts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id TEXT NOT NULL REFERENCES calls(id),
                    speaker TEXT NOT NULL, text TEXT NOT NULL, observed_at REAL NOT NULL
                );
            """)

    def consent(self, call_id, owner, *, transcript, recording, attestation, now):
        now = timestamp(now)
        if type(transcript) is not bool or type(recording) is not bool:
            raise InvalidCall("Consent flags must be explicit booleans")
        if not isinstance(attestation, str) or not 8 <= len(attestation.strip()) <= 256:
            raise InvalidCall("A bounded consent attestation is required")
        with self.ledger.transaction() as db:
            self.ledger._owned(db, call_id, owner)
            prior = db.execute("SELECT observed_at FROM consent WHERE call_id=?", (call_id,)).fetchone()
            if prior and now < prior[0]:
                raise InvalidCall("Consent observation is stale")
            db.execute("INSERT INTO consent VALUES(?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET "
                       "transcript=excluded.transcript,recording=excluded.recording,"
                       "attestation=excluded.attestation,observed_at=excluded.observed_at",
                       (call_id, transcript, recording, attestation, now))
            if not transcript:
                db.execute("DELETE FROM transcripts WHERE call_id=?", (call_id,))
            self.ledger._event(db, call_id, "consent_changed", now,
                               {"transcript": transcript, "recording": recording})
        return self.settings(call_id, owner)

    def settings(self, call_id, owner):
        with self.ledger.connection() as db:
            self.ledger._owned(db, call_id, owner)
            row = db.execute("SELECT * FROM consent WHERE call_id=?", (call_id,)).fetchone()
            return dict(row) if row else {"transcript": False, "recording": False}

    def append(self, call_id, owner, speaker, text, *, now):
        now = timestamp(now)
        if speaker not in {"caller", "agent"} or not isinstance(text, str) or not 1 <= len(text) <= 4000:
            raise InvalidCall("Invalid transcript entry")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise InvalidCall("Invalid transcript characters")
        with self.ledger.transaction() as db:
            self.ledger._owned(db, call_id, owner)
            consent = db.execute("SELECT * FROM consent WHERE call_id=?", (call_id,)).fetchone()
            if not consent or not consent["transcript"] or now < consent["observed_at"]:
                raise InvalidCall("Transcript retention requires current consent")
            count = db.execute("SELECT count(*) FROM transcripts WHERE call_id=?", (call_id,)).fetchone()[0]
            if count >= 500:
                raise InvalidCall("Transcript entry limit reached")
            db.execute("INSERT INTO transcripts(call_id,speaker,text,observed_at) VALUES(?,?,?,?)",
                       (call_id, speaker, text, now))

    def read(self, call_id, owner):
        with self.ledger.connection() as db:
            self.ledger._owned(db, call_id, owner)
            return [dict(row) for row in db.execute(
                "SELECT speaker,text,observed_at FROM transcripts WHERE call_id=? ORDER BY sequence", (call_id,))]

    def purge(self, *, before):
        with self.ledger.transaction() as db:
            return db.execute("DELETE FROM transcripts WHERE observed_at<?", (timestamp(before),)).rowcount
