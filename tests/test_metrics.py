import csv
import io
from datetime import UTC, date, datetime

import pytest
from aiogram.types import CallbackQuery, Message

from intonaciya import metrics as metrics_module
from intonaciya.bot.handlers import _is_open, _over_limit
from intonaciya.config import Settings
from intonaciya.metrics import Metrics, clean_source
from intonaciya.metrics_report import build_summary, format_summary, write_csv

USER, OTHER, TESTER = 1001, 1002, 42


@pytest.fixture
def metrics() -> Metrics:
    return Metrics(":memory:", salt="secret", test_user_ids=frozenset({TESTER}))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    def set_time(moment: str) -> None:
        # Moscow is UTC+3: 21:00 UTC is already the next day there.
        monkeypatch.setattr(
            metrics_module, "_now", lambda: datetime.fromisoformat(moment).replace(tzinfo=UTC)
        )

    return set_time


def test_user_key_is_stable_and_hides_the_id(metrics: Metrics) -> None:
    key = metrics.user_key(USER)
    assert key == metrics.user_key(USER)
    assert str(USER) not in key
    assert key != Metrics(":memory:", salt="other", test_user_ids=frozenset()).user_key(USER)


def test_consent_is_remembered(metrics: Metrics) -> None:
    assert not metrics.has_consented(USER)
    metrics.record_consent(USER)
    assert metrics.has_consented(USER)
    assert not metrics.has_consented(OTHER)


def test_forget_withdraws_consent_and_unlinks_events(metrics: Metrics) -> None:
    metrics.record_consent(USER)
    metrics.record("llm_call", USER)
    metrics.forget(USER)
    assert not metrics.has_consented(USER)
    db = metrics.connection
    assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM events WHERE user_key IS NULL").fetchone() == (2,)


def test_first_source_wins(metrics: Metrics) -> None:
    metrics.register(USER, "ads")
    metrics.register(USER, "ref_1")
    assert metrics.connection.execute("SELECT source FROM users").fetchone() == ("ads",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("tg_psy", "tg_psy"), ("a b", "other"), ("x" * 40, "other")],
)
def test_clean_source(raw: str | None, expected: str | None) -> None:
    assert clean_source(raw) == expected


def test_llm_calls_are_counted_per_moscow_day(metrics: Metrics, clock) -> None:
    clock("2026-10-01T20:00:00")  # 23:00 MSK
    metrics.record("llm_call", USER)
    assert metrics.llm_calls_today(USER) == 1
    clock("2026-10-01T21:30:00")  # 00:30 MSK, next day
    assert metrics.llm_calls_today(USER) == 0


def test_record_takes_the_user_from_context(metrics: Metrics) -> None:
    token = metrics_module.current_user_id.set(USER)
    try:
        metrics.record("llm_call", purpose="vision")
    finally:
        metrics_module.current_user_id.reset(token)
    assert metrics.llm_calls_today(USER) == 1


def test_limit_skips_test_accounts(metrics: Metrics) -> None:
    settings = Settings(_env_file=None, daily_llm_limit=2)
    for _ in range(2):
        metrics.record("llm_call", USER)
        metrics.record("llm_call", TESTER)
    assert _over_limit(USER, metrics, settings)
    assert not _over_limit(TESTER, metrics, settings)
    assert not _over_limit(OTHER, metrics, settings)


def test_test_ids_include_admin() -> None:
    settings = Settings(_env_file=None, test_user_ids="1, 2", admin_user_id="3")
    assert settings.test_user_id_set == {1, 2, 3}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("/start", True), ("/start ads", True), ("/privacy", True), ("/reset", False), ("hi", False)],
)
def test_open_commands(text: str, expected: bool) -> None:
    assert _is_open(Message.model_construct(text=text)) is expected


def test_consent_button_is_open() -> None:
    assert _is_open(CallbackQuery.model_construct(data="consent"))
    assert not _is_open(CallbackQuery.model_construct(data="analyze"))


def test_summary_counts_real_users_only(metrics: Metrics, clock) -> None:
    clock("2026-10-01T09:00:00")
    metrics.record_budget(100.0, 50_000)
    for user in (USER, OTHER, TESTER):
        metrics.register(user, "ads" if user == USER else None)
        metrics.record("start", user)
    for _ in range(3):
        metrics.record("llm_call", USER, purpose="coach", tokens_in=1000, tokens_out=300)
    metrics.record("llm_call", OTHER, purpose="vision", status="error")
    metrics.record("llm_call", TESTER, purpose="coach", tokens_in=5000)
    metrics.record("analysis", USER, status="none")
    clock("2026-10-02T09:00:00")
    metrics.record("start", USER)
    metrics.record_budget(130.0, 50_000)

    summary = build_summary(metrics.connection, days=2, today=date(2026, 10, 2))
    today, yesterday = summary.days
    assert (yesterday.dau, yesterday.new_users, yesterday.llm_calls) == (2, 2, 4)
    assert yesterday.calls_per_dau == 2.0
    assert yesterday.tokens_in == 3000
    assert yesterday.errors == 1
    assert yesterday.peak_tpm == 3900
    assert (today.dau, today.spend, today.cost_per_dau) == (1, 30.0, 30.0)
    assert summary.total_users == 2
    assert summary.d1 == 0.5
    ads, direct = summary.sources
    assert (ads.source, ads.users, ads.activated, ads.d1_returned, ads.llm_calls) == (
        "ads",
        1,
        1,
        1,
        3,
    )
    assert (ads.activation, ads.d1, ads.calls_per_user) == (1.0, 1.0, 3.0)
    assert (direct.source, direct.users, direct.activated, direct.d1) == ("direct", 1, 0, 0.0)
    report = format_summary(summary)
    assert "DAU" in report
    assert "ads: 1 → 0 → 1 (100%)" in report

    out = io.StringIO()
    write_csv(summary, out)
    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert [row["day"] for row in rows] == ["2026-10-01", "2026-10-02"]
