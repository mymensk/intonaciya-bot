from collections.abc import Sequence

from intonaciya.llm.base import Completion, Message


class StubProvider:
    """Stand-in for local runs without LLM credentials: echoes what the model would get."""

    model = "stub"

    async def complete(self, messages: Sequence[Message]) -> Completion:
        user_prompt = messages[-1].content
        text = (
            f"[Тестовый режим: GigaChat не подключён. Вот что получила бы модель]\n\n{user_prompt}"
        )
        return Completion(text=text, finish_reason="stop", prompt_tokens=0, completion_tokens=0)
