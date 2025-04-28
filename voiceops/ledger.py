"""Transactional records of observed calls and operator actions."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid


class InvalidCall(ValueError):
    """The supplied call data violates the application contract."""


class CallNotFound(LookupError):
    """No call is visible to the requested owner."""


def identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise InvalidCall(f"Invalid {field}")
    return value


def timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise InvalidCall("Invalid timestamp")
    return float(value)


class CallLedger:
    TRANSITIONS = {
        "admitted": {"ringing", "answered", "ended", "failed"},
        "ringing": {"answered", "ended", "failed"},
        "answered": {"transferring", "ended", "failed"},
        "transferring": {"answered", "transfer_unknown", "transferred", "ended", "failed"},
        "transfer_unknown": {"answered", "transferred", "ended", "failed"},
        "ended": set(), "failed": set(), "transferred": set(),
    }

    def __init__(self, path, *, max_active_calls=4):
        if type(max_active_calls) is not int or not 1 <= max_active_calls <= 100:
            raise InvalidCall("Invalid active call capacity")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, caller TEXT NOT NULL,
                    room TEXT NOT NULL, participant TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id TEXT NOT NULL REFERENCES calls(id),
                    kind TEXT NOT NULL, observed_at REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_call ON events(call_id, sequence);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS live_participant ON calls(room,participant)
                  WHERE status NOT IN ('ended','failed','transferred');
            """)
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('capacity',?)", (str(max_active_calls),))
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _owned(db, call_id, owner):
        row = db.execute("SELECT * FROM calls WHERE id=? AND owner=?", (call_id, owner)).fetchone()
        if row is None:
            raise CallNotFound("Call unavailable")
        return dict(row)

    @staticmethod
    def _event(db, call_id, kind, now, data):
        db.execute("INSERT INTO events(call_id,kind,observed_at,data) VALUES(?,?,?,?)",
                   (call_id, kind, now, json.dumps(data, separators=(",", ":"))))

    def admit(self, *, owner, caller, room, participant, now):
        for key, value in (("owner", owner), ("room", room), ("participant", participant)):
            identifier(value, key)
        if not isinstance(caller, str) or not re.fullmatch(r"\+?[0-9]{1,20}", caller):
            raise InvalidCall("Invalid caller")
        now = timestamp(now)
        call_id = uuid.uuid4().hex
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='drain_deadline'").fetchone():
                raise InvalidCall("Service is draining")
            if db.execute("""SELECT 1 FROM calls WHERE room=? AND participant=?
                             AND status NOT IN ('ended','failed','transferred')""", (room, participant)).fetchone():
                raise InvalidCall("Participant already admitted")
            active = db.execute("SELECT count(*) FROM calls WHERE status NOT IN ('ended','failed','transferred')").fetchone()[0]
            capacity = int(db.execute("SELECT value FROM settings WHERE key='capacity'").fetchone()[0])
            if active >= capacity:
                raise InvalidCall("Active call capacity reached")
            db.execute("""INSERT INTO calls(id,owner,caller,room,participant,status,created_at,updated_at)
                          VALUES(?,?,?,?,?,'admitted',?,?)""",
                       (call_id, owner, caller, room, participant, now, now))
            self._event(db, call_id, "admitted", now, {"room": room, "participant": participant})
            return self._owned(db, call_id, owner)

    def get(self, call_id, owner):
        with self.connection() as db:
            return self._owned(db, call_id, owner)

    def begin_drain(self, *, now, deadline):
        now, deadline = timestamp(now), timestamp(deadline)
        if not now < deadline <= now + 3600:
            raise InvalidCall("Drain deadline must be within one hour")
        with self.transaction() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('drain_deadline',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(deadline),))

    def drain_status(self, *, now):
        now = timestamp(now)
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='drain_deadline'").fetchone()
            active = [r[0] for r in db.execute("SELECT id FROM calls WHERE status NOT IN "
                                             "('ended','failed','transferred') ORDER BY id")]
        deadline = float(row[0]) if row else None
        return {"draining": row is not None, "deadline": deadline,
                "active_calls": len(active), "complete": row is not None and not active,
                "needs_termination": active if deadline is not None and now >= deadline else []}

    def transition(self, call_id, owner, state, *, now, version):
        now = timestamp(now)
        with self.transaction() as db:
            call = self._owned(db, call_id, owner)
            if type(version) is not int or call["version"] != version:
                raise InvalidCall("Call version changed")
            if state not in self.TRANSITIONS[call["status"]]:
                raise InvalidCall("Invalid call transition")
            if now < call["updated_at"]:
                raise InvalidCall("Observation predates latest call state")
            db.execute("UPDATE calls SET status=?,updated_at=?,version=version+1 WHERE id=?",
                       (state, now, call_id))
            self._event(db, call_id, state, now, {"previous": call["status"]})
            return self._owned(db, call_id, owner)

    def list_calls(self, owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM calls WHERE owner=? ORDER BY created_at DESC, id", (owner,))]

    def events(self, call_id, owner):
        with self.connection() as db:
            self._owned(db, call_id, owner)
            rows = db.execute("SELECT * FROM events WHERE call_id=? ORDER BY sequence", (call_id,))
            return [{**dict(row), "data": json.loads(row["data"])} for row in rows]
