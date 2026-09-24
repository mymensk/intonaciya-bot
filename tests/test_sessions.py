import pytest

from intonaciya import sessions as sessions_module
from intonaciya.dialogue import Line
from intonaciya.sessions import SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(sessions_module.time, "monotonic", fake)
    return fake


def test_add_accumulates_lines(clock: FakeClock) -> None:
    store = SessionStore()
    store.add(1, 10, [Line("them", "a")])
    session = store.add(1, 11, [Line("me", "b"), Line("me", "c")])
    assert session.lines == [Line("them", "a"), Line("me", "b"), Line("me", "c")]
    assert store.get(2) is None


def test_lines_follow_message_order_not_arrival(clock: FakeClock) -> None:
    store = SessionStore()
    store.add(1, 12, [Line("me", "second")])
    session = store.add(1, 11, [Line("them", "first")])
    assert [line.text for line in session.lines] == ["first", "second"]


def test_session_expires_after_ttl(clock: FakeClock) -> None:
    store = SessionStore(ttl_s=10)
    store.add(1, 10, [Line("them", "a")])
    clock.now = 11
    assert store.get(1) is None


def test_activity_extends_session(clock: FakeClock) -> None:
    store = SessionStore(ttl_s=10)
    store.add(1, 10, [Line("them", "a")])
    clock.now = 8
    store.add(1, 11, [Line("them", "b")])
    clock.now = 16
    session = store.get(1)
    assert session is not None
    assert len(session.lines) == 2


def test_pop_removes_session(clock: FakeClock) -> None:
    store = SessionStore()
    store.add(1, 10, [Line("them", "a")])
    assert store.pop(1) is not None
    assert store.get(1) is None
