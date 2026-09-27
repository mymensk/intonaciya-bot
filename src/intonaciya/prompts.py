from collections.abc import Sequence
from functools import cache
from pathlib import Path
from string import Template

from intonaciya.dialogue import Line, format_dialogue
from intonaciya.llm import Message

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def build_coach_messages(situation: str, lines: Sequence[Line], request: str) -> list[Message]:
    user = Template(load_prompt("coach_user.md")).substitute(
        situation=situation,
        dialogue=format_dialogue(lines) or "(переписки ещё нет)",
        request=request,
    )
    return [Message("system", load_prompt("coach_system.md")), Message("user", user)]
