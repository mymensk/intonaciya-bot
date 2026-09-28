"""Usage report over the metrics database; test accounts are left out.

python -m intonaciya.metrics_report [--db data/metrics.sqlite3] [--days 14] [--csv out.csv]
"""

import argparse
import csv
import sqlite3
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta

from intonaciya.metrics import MSK

# Events a user triggers themselves: they count towards activity (DAU).
_ACTIVE = "event NOT IN ('llm_call')"


@dataclass(frozen=True, slots=True)
class DayStats:
    day: str
    dau: int
    new_users: int
    llm_calls: int
    calls_per_dau: float
    analyses: int
    messages_in: int
    errors: int
    tokens_in: int
    tokens_out: int
    peak_rpm: int
    peak_tpm: int
    avg_latency_ms: int
    spend: float | None
    cost_per_dau: float | None


@dataclass(frozen=True, slots=True)
class SourceFunnel:
    """How users from one acquisition channel get through to real use."""

    source: str
    users: int
    consented: int
    # Got at least one useful analysis (not an error, not a refusal).
    activated: int
    # Users old enough to be counted for D1, and those of them active on day 1.
    d1_eligible: int
    d1_returned: int
    llm_calls: int

    @property
    def activation(self) -> float | None:
        return round(self.activated / self.users, 3) if self.users else None

    @property
    def d1(self) -> float | None:
        return round(self.d1_returned / self.d1_eligible, 3) if self.d1_eligible else None

    @property
    def calls_per_user(self) -> float:
        return round(self.llm_calls / self.users, 1) if self.users else 0.0


@dataclass(frozen=True, slots=True)
class Summary:
    days: list[DayStats]
    total_users: int
    mau: int
    stickiness: float | None
    d1: float | None
    d7: float | None
    sources: list[SourceFunnel]


def _scalar(db: sqlite3.Connection, sql: str, *params: object) -> int:
    (value,) = db.execute(sql, params).fetchone()
    return value or 0


def _day_stats(db: sqlite3.Connection, day: str, spend: float | None) -> DayStats:
    real = "day = ? AND is_test = 0"
    dau = _scalar(
        db, f"SELECT COUNT(DISTINCT user_key) FROM events WHERE {real} AND {_ACTIVE}", day
    )
    new_users = _scalar(
        db,
        "SELECT COUNT(*) FROM users WHERE is_test = 0 AND date(first_seen, '+3 hours') = ?",
        day,
    )
    calls, tokens_in, tokens_out, latency = db.execute(
        "SELECT COUNT(*), SUM(tokens_in), SUM(tokens_out), AVG(latency_ms)"
        f" FROM events WHERE {real} AND event = 'llm_call'",
        (day,),
    ).fetchone()
    peak_rpm, peak_tpm = db.execute(
        "SELECT MAX(n), MAX(t) FROM (SELECT COUNT(*) AS n,"
        " SUM(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) AS t"
        f" FROM events WHERE {real} AND event = 'llm_call' GROUP BY substr(ts, 1, 16))",
        (day,),
    ).fetchone()
    return DayStats(
        day=day,
        dau=dau,
        new_users=new_users,
        llm_calls=calls,
        calls_per_dau=round(calls / dau, 1) if dau else 0.0,
        analyses=_scalar(
            db, f"SELECT COUNT(*) FROM events WHERE {real} AND event = 'analysis'", day
        ),
        messages_in=_scalar(
            db,
            f"SELECT COUNT(*) FROM events WHERE {real} AND event IN ('forward', 'screenshot')",
            day,
        ),
        errors=_scalar(db, f"SELECT COUNT(*) FROM events WHERE {real} AND status = 'error'", day),
        tokens_in=tokens_in or 0,
        tokens_out=tokens_out or 0,
        peak_rpm=peak_rpm or 0,
        peak_tpm=peak_tpm or 0,
        avg_latency_ms=round(latency or 0),
        spend=spend,
        cost_per_dau=round(spend / dau, 2) if spend is not None and dau else None,
    )


def _daily_spend(db: sqlite3.Connection) -> dict[str, float]:
    """Gateway spend per day: growth of the key's cumulative spend. Includes test usage."""
    closing = db.execute("SELECT day, MAX(spend) FROM budget GROUP BY day ORDER BY day").fetchall()
    spend: dict[str, float] = {}
    for (_, previous), (day, current) in zip(closing, closing[1:], strict=False):
        spend[day] = round(current - previous, 2)
    return spend


def _retention(db: sqlite3.Connection, offset: int, today: date) -> float | None:
    """Share of users active exactly `offset` days after their first day."""
    cohorts = db.execute(
        "SELECT user_key, date(first_seen, '+3 hours') FROM users"
        " WHERE is_test = 0 AND date(first_seen, '+3 hours') <= ?",
        ((today - timedelta(days=offset)).isoformat(),),
    ).fetchall()
    if not cohorts:
        return None
    returned = sum(
        1
        for key, first in cohorts
        if db.execute(
            f"SELECT 1 FROM events WHERE user_key = ? AND day = ? AND {_ACTIVE} LIMIT 1",
            (key, (date.fromisoformat(first) + timedelta(days=offset)).isoformat()),
        ).fetchone()
    )
    return round(returned / len(cohorts), 3)


def _source_funnels(db: sqlite3.Connection, today: date) -> list[SourceFunnel]:
    first_day = "date(u.first_seen, '+3 hours')"
    rows = db.execute(
        "SELECT COALESCE(u.source, 'direct'), COUNT(*), SUM(u.consented_at IS NOT NULL),"
        " SUM(EXISTS (SELECT 1 FROM events e WHERE e.user_key = u.user_key"
        "   AND e.event = 'analysis' AND e.status = 'none')),"
        f" SUM({first_day} <= ?),"
        " SUM(EXISTS (SELECT 1 FROM events e WHERE e.user_key = u.user_key"
        f"   AND {_ACTIVE} AND e.day = date({first_day}, '+1 day'))),"
        " SUM((SELECT COUNT(*) FROM events e WHERE e.user_key = u.user_key"
        "   AND e.event = 'llm_call'))"
        " FROM users u WHERE u.is_test = 0 GROUP BY 1 ORDER BY 2 DESC, 1",
        ((today - timedelta(days=1)).isoformat(),),
    ).fetchall()
    return [SourceFunnel(*(row[0], *(value or 0 for value in row[1:]))) for row in rows]


def build_summary(db: sqlite3.Connection, *, days: int, today: date) -> Summary:
    spend = _daily_spend(db)
    day_list = [(today - timedelta(days=offset)).isoformat() for offset in range(days)]
    stats = [_day_stats(db, day, spend.get(day)) for day in day_list]
    mau = _scalar(
        db,
        f"SELECT COUNT(DISTINCT user_key) FROM events WHERE is_test = 0 AND {_ACTIVE} AND day > ?",
        (today - timedelta(days=30)).isoformat(),
    )
    week = stats[:7]
    avg_dau = sum(s.dau for s in week) / len(week) if week else 0
    return Summary(
        days=stats,
        total_users=_scalar(db, "SELECT COUNT(*) FROM users WHERE is_test = 0"),
        mau=mau,
        stickiness=round(avg_dau / mau, 3) if mau else None,
        d1=_retention(db, 1, today),
        d7=_retention(db, 7, today),
        sources=_source_funnels(db, today),
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def format_summary(summary: Summary) -> str:
    """Compact plain-text report for the admin chat."""
    lines = [
        f"Пользователей всего: {summary.total_users}, MAU: {summary.mau}",
        f"Stickiness (DAU/MAU за 7 дней): {_pct(summary.stickiness)}",
        f"Удержание D1: {_pct(summary.d1)}, D7: {_pct(summary.d7)}",
        "",
        "День | DAU | новые | запросы к LLM (на DAU) | разборы | ошибки | ₽ на DAU",
    ]
    for s in summary.days:
        cost = "—" if s.cost_per_dau is None else f"{s.cost_per_dau:.2f}"
        lines.append(
            f"{s.day[5:]} | {s.dau} | {s.new_users} | {s.llm_calls} ({s.calls_per_dau})"
            f" | {s.analyses} | {s.errors} | {cost}"
        )
    today = summary.days[0]
    lines += [
        "",
        f"Сегодня: токены {today.tokens_in}→{today.tokens_out}, пик {today.peak_rpm} запр/мин"
        f" и {today.peak_tpm} токенов/мин, средняя задержка {today.avg_latency_ms} мс",
    ]
    if summary.sources:
        lines += ["", "Каналы: пришли → согласие → разбор (активация) | D1 | запросов на чел."]
        lines += [
            f"{f.source}: {f.users} → {f.consented} → {f.activated} ({_pct(f.activation)})"
            f" | {_pct(f.d1)} | {f.calls_per_user}"
            for f in summary.sources
        ]
    return "\n".join(lines)


def write_csv(summary: Summary, out: object) -> None:
    writer = csv.DictWriter(out, fieldnames=[f.name for f in fields(DayStats)])  # type: ignore[arg-type]
    writer.writeheader()
    for s in sorted(summary.days, key=lambda s: s.day):
        writer.writerow(asdict(s))


def write_sources_csv(summary: Summary, out: object) -> None:
    writer = csv.writer(out)  # type: ignore[arg-type]
    writer.writerow(
        ["source", "users", "consented", "activated", "activation", "d1", "calls_per_user"]
    )
    for f in summary.sources:
        writer.writerow(
            [f.source, f.users, f.consented, f.activated, f.activation, f.d1, f.calls_per_user]
        )


def _write(path: str, writer: object, summary: Summary) -> None:
    if path == "-":
        writer(summary, sys.stdout)  # type: ignore[operator]
        return
    with open(path, "w", newline="", encoding="utf-8") as out:
        writer(summary, out)  # type: ignore[operator]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/metrics.sqlite3")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--csv", help="write daily stats to this CSV file ('-' for stdout)")
    parser.add_argument("--sources-csv", help="write the funnel by source ('-' for stdout)")
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    summary = build_summary(db, days=args.days, today=datetime.now(MSK).date())
    if args.csv:
        _write(args.csv, write_csv, summary)
    if args.sources_csv:
        _write(args.sources_csv, write_sources_csv, summary)
    if "-" not in (args.csv, args.sources_csv):
        print(format_summary(summary))


if __name__ == "__main__":
    main()
