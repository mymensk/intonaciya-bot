"""Run coaching scenarios through GigaChat and measure the refusal rate.

Usage:
    python scripts/check_gigachat_filters.py --models GigaChat,GigaChat-Pro --repeat 3
    python scripts/check_gigachat_filters.py --dry-run   # render prompts, no API calls

Writes a Markdown report and raw JSON to reports/ (not tracked by git).
"""

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from string import Template

import httpx

from intonaciya.config import Settings
from intonaciya.llm import GigaChatProvider, Message, RefusalKind, classify_refusal

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
DEFAULT_SCENARIOS = Path(__file__).resolve().parent / "data" / "filter_scenarios.json"
DEFAULT_OUT_DIR = ROOT / "reports"

AUTHOR_LABELS = {"me": "Я", "them": "Собеседник"}


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    category: str
    situation: str
    dialogue: list[dict[str, str]]
    request: str


@dataclass(slots=True)
class RunResult:
    model: str
    scenario_id: str
    category: str
    attempt: int
    outcome: str  # a RefusalKind value or "error"
    finish_reason: str | None = None
    text: str = ""
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def load_scenarios(path: Path) -> list[Scenario]:
    return [Scenario(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def build_messages(scenario: Scenario) -> list[Message]:
    system = (PROMPTS_DIR / "coach_system.md").read_text(encoding="utf-8").strip()
    user_template = Template((PROMPTS_DIR / "coach_user.md").read_text(encoding="utf-8"))
    dialogue = "\n".join(
        f"{AUTHOR_LABELS[line['author']]}: {line['text']}" for line in scenario.dialogue
    )
    user = user_template.substitute(
        situation=scenario.situation,
        dialogue=dialogue or "(переписки ещё нет)",
        request=scenario.request,
    ).strip()
    return [Message("system", system), Message("user", user)]


async def run_one(
    provider: GigaChatProvider,
    scenario: Scenario,
    attempt: int,
    semaphore: asyncio.Semaphore,
) -> RunResult:
    result = RunResult(
        model=provider.model,
        scenario_id=scenario.id,
        category=scenario.category,
        attempt=attempt,
        outcome="error",
    )
    async with semaphore:
        try:
            completion = await provider.complete(build_messages(scenario))
        except httpx.HTTPStatusError as exc:
            result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return result
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

    result.outcome = classify_refusal(completion).value
    result.finish_reason = completion.finish_reason
    result.text = completion.text
    result.prompt_tokens = completion.prompt_tokens
    result.completion_tokens = completion.completion_tokens
    return result


def _rate(part: int, total: int) -> str:
    return f"{part / total:.0%}" if total else "—"


def summarize(results: list[RunResult], key: str) -> list[str]:
    groups: dict[str, list[RunResult]] = defaultdict(list)
    for result in results:
        groups[getattr(result, key)].append(result)

    lines = [
        f"| {'Модель' if key == 'model' else 'Категория'} "
        "| Прогонов | Жёсткие | Мягкие | Ошибки | Доля отказов | Токены (вход/выход, ср.) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, group in groups.items():
        hard = sum(r.outcome == RefusalKind.HARD for r in group)
        soft = sum(r.outcome == RefusalKind.SOFT for r in group)
        errors = sum(r.outcome == "error" for r in group)
        answered = [r for r in group if r.outcome != "error"]
        tokens = (
            f"{sum(r.prompt_tokens for r in answered) // len(answered)}"
            f" / {sum(r.completion_tokens for r in answered) // len(answered)}"
            if answered
            else "—"
        )
        refusal_rate = _rate(hard + soft, len(answered))
        lines.append(
            f"| {name} | {len(group)} | {hard} | {soft} | {errors} | {refusal_rate} | {tokens} |"
        )
    return lines


def render_report(results: list[RunResult], scenarios: list[Scenario]) -> str:
    lines = [
        f"# Проверка фильтров GigaChat — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "Жёсткий отказ — ответ заблокирован API (finish_reason = blacklist).",
        "Мягкий отказ — модель ответила отказом или увела тему (эвристика, проверяйте глазами).",
        "",
        "## По моделям",
        "",
        *summarize(results, "model"),
    ]

    for model in dict.fromkeys(r.model for r in results):
        lines += ["", f"## {model}: по категориям", ""]
        lines += summarize([r for r in results if r.model == model], "category")

    lines += ["", "## Ответы", ""]
    by_scenario = {s.id: s for s in scenarios}
    for result in results:
        scenario = by_scenario[result.scenario_id]
        lines += [
            f"### {result.model} · {scenario.id} · попытка {result.attempt} · **{result.outcome}**",
            "",
            f"_{scenario.category}. {scenario.request}_",
            "",
            result.error or result.text or "(пустой ответ)",
            "",
        ]
    return "\n".join(lines)


def print_dry_run(scenarios: list[Scenario]) -> None:
    for scenario in scenarios:
        print(f"===== {scenario.id} ({scenario.category}) =====")
        for message in build_messages(scenario)[1:]:
            print(message.content)
        print()
    print(f"Сценариев: {len(scenarios)}. Системный промпт: prompts/coach_system.md")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", help="Comma-separated models; defaults to GIGACHAT_MODEL")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per scenario (default: 1)")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    # Freemium plans for individuals process one request at a time.
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without API calls")
    args = parser.parse_args()

    scenarios = load_scenarios(args.scenarios)
    if args.dry_run:
        print_dry_run(scenarios)
        return

    settings = Settings()
    models = args.models.split(",") if args.models else [settings.gigachat_model]
    providers = [
        GigaChatProvider(
            settings.gigachat_auth_key.get_secret_value(),
            scope=settings.gigachat_scope,
            model=model.strip(),
            ca_bundle=settings.gigachat_ca_bundle,
        )
        for model in models
    ]
    semaphore = asyncio.Semaphore(args.concurrency)
    try:
        results = await asyncio.gather(
            *(
                run_one(provider, scenario, attempt, semaphore)
                for provider in providers
                for scenario in scenarios
                for attempt in range(1, args.repeat + 1)
            )
        )
    finally:
        for provider in providers:
            await provider.aclose()

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    report_path = args.out / f"gigachat_filters_{stamp}.md"
    report_path.write_text(render_report(results, scenarios), encoding="utf-8")
    (args.out / f"gigachat_filters_{stamp}.json").write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n".join(summarize(results, "model")))
    print(f"\nОтчёт: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
