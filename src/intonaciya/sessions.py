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
    # After an analysis: what was asked first and the latest answer, so the user
    # can ask for changes ("bolder", "shorter") without resending the dialogue.
    first_request: str | None = None
    last_answer: str | None = None

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
        # A dialogue sent after an answer is a new conversation, not a continuation.
        if session is None or session.last_answer is not None:
            session = self._sessions[user_id] = Session()
        session.chunks[message_id] = list(lines)
        session.updated_at = time.monotonic()
        return session

    def remember_answer(self, user_id: int, request: str, answer: str) -> None:
        session = self.get(user_id)
        if session is None:
            return
        session.first_request = session.first_request or request
        session.last_answer = answer
        session.updated_at = time.monotonic()

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
