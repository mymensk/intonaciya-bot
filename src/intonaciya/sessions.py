import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from intonaciya.dialogue import Line

# Conversations are private: keep them in memory only, and not for long.
SESSION_TTL_S = 15 * 60


@dataclass(slots=True)
class Session:
    # Lines per source Telegram message. Forwards and screenshots are handled
    # concurrently and may finish out of order; message IDs restore the order.
    chunks: dict[int, list[Line]] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)

    @property
    def lines(self) -> list[Line]:
        return [line for key in sorted(self.chunks) for line in self.chunks[key]]


class SessionStore:
    """In-memory buffer of the user's conversation. Never persisted."""

    def __init__(self, ttl_s: float = SESSION_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._sessions: dict[int, Session] = {}

    def add(self, user_id: int, message_id: int, lines: Sequence[Line]) -> Session:
        session = self.get(user_id)
        if session is None:
            session = self._sessions[user_id] = Session()
        session.chunks[message_id] = list(lines)
        session.updated_at = time.monotonic()
        return session

    def get(self, user_id: int) -> Session | None:
        session = self._sessions.get(user_id)
        if session and time.monotonic() - session.updated_at > self._ttl_s:
            del self._sessions[user_id]
            return None
        return session

    def pop(self, user_id: int) -> Session | None:
        session = self.get(user_id)
        self._sessions.pop(user_id, None)
        return session
