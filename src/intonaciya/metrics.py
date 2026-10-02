"""Usage events in SQLite: counts, timings and tokens, never conversation text.

Telegram IDs are stored as salted HMAC digests, so usage can be counted per user
without keeping the ID itself. Accounts of the team are flagged as test ones and
excluded from reports.
"""

import hashlib
import hmac
import re
import sqlite3
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# Days are counted in Moscow time: that is where the audience is.
MSK = timezone(timedelta(hours=3))

_SOURCE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# The user of the update being handled: lets deep call sites (the vision reader)
# attribute LLM calls without threading the ID through every signature.
current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_key TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    source TEXT,
    consented_at TEXT,
    is_test INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    user_key TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    event TEXT NOT NULL,
    purpose TEXT,
    status TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    value INTEGER
);
CREATE INDEX IF NOT EXISTS events_day ON events (day, event);
CREATE INDEX IF NOT EXISTS events_user_day ON events (user_key, day);
CREATE TABLE IF NOT EXISTS budget (
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    spend REAL NOT NULL,
    max_budget REAL
);
"""


def clean_source(raw: str | None) -> str | None:
    """Deep-link payloads are user-controlled: keep only short plain tags."""
    if not raw:
        return None
    return raw if _SOURCE.fullmatch(raw) else "other"


def _now() -> datetime:
    return datetime.now(UTC)


def _day(moment: datetime) -> str:
    return moment.astimezone(MSK).date().isoformat()


class Metrics:
    def __init__(self, path: str | Path, *, salt: str, test_user_ids: frozenset[int]) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._salt = salt.encode()
        self._test_ids = test_user_ids
        self._consented: set[str] = set()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db

    def close(self) -> None:
        self._db.close()

    def user_key(self, user_id: int) -> str:
        return hmac.new(self._salt, str(user_id).encode(), hashlib.sha256).hexdigest()[:24]

    def is_test(self, user_id: int) -> bool:
        return user_id in self._test_ids

    def register(self, user_id: int, source: str | None) -> None:
        """Remembers when and where from a user came; the first source wins."""
        self._db.execute(
            "INSERT OR IGNORE INTO users (user_key, first_seen, source, is_test)"
            " VALUES (?, ?, ?, ?)",
            (self.user_key(user_id), _now().isoformat(), source, int(self.is_test(user_id))),
        )

    def has_consented(self, user_id: int) -> bool:
        key = self.user_key(user_id)
        if key in self._consented:
            return True
        row = self._db.execute(
            "SELECT 1 FROM users WHERE user_key = ? AND consented_at IS NOT NULL", (key,)
        ).fetchone()
        if row:
            self._consented.add(key)
        return row is not None

    def record_consent(self, user_id: int) -> None:
        self.register(user_id, None)
        self._db.execute(
            "UPDATE users SET consented_at = ? WHERE user_key = ? AND consented_at IS NULL",
            (_now().isoformat(), self.user_key(user_id)),
        )
        self._consented.add(self.user_key(user_id))
        self.record("consent", user_id)

    def forget(self, user_id: int) -> None:
        """Withdraws consent: drops the user and unlinks their events from them.

        Events stay as anonymous counts so that past totals (calls, tokens) hold.
        """
        key = self.user_key(user_id)
        self._db.execute("DELETE FROM users WHERE user_key = ?", (key,))
        self._db.execute("UPDATE events SET user_key = NULL WHERE user_key = ?", (key,))
        self._consented.discard(key)

    def record(
        self,
        event: str,
        user_id: int | None = None,
        *,
        purpose: str | None = None,
        status: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        latency_ms: float | None = None,
        value: int | None = None,
    ) -> None:
        if user_id is None:
            user_id = current_user_id.get()
        now = _now()
        self._db.execute(
            "INSERT INTO events (ts, day, user_key, is_test, event, purpose, status, model,"
            " tokens_in, tokens_out, latency_ms, value)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                _day(now),
                self.user_key(user_id) if user_id is not None else None,
                int(user_id is not None and self.is_test(user_id)),
                event,
                purpose,
                status,
                model,
                tokens_in,
                tokens_out,
                None if latency_ms is None else round(latency_ms),
                value,
            ),
        )

    def has_event(self, user_id: int, event: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM events WHERE user_key = ? AND event = ? LIMIT 1",
            (self.user_key(user_id), event),
        ).fetchone()
        return row is not None

    def llm_calls_today(self, user_id: int) -> int:
        (count,) = self._db.execute(
            "SELECT COUNT(*) FROM events WHERE user_key = ? AND day = ? AND event = 'llm_call'",
            (self.user_key(user_id), _day(_now())),
        ).fetchone()
        return count

    def record_budget(self, spend: float, max_budget: float | None) -> None:
        now = _now()
        self._db.execute(
            "INSERT INTO budget (ts, day, spend, max_budget) VALUES (?, ?, ?, ?)",
            (now.isoformat(), _day(now), spend, max_budget),
        )
