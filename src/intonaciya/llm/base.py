from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class LLMProvider(Protocol):
    model: str

    async def complete(self, messages: Sequence[Message]) -> Completion: ...
