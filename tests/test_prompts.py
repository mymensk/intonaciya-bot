from intonaciya.dialogue import Line
from intonaciya.llm.stub import StubProvider
from intonaciya.prompts import build_coach_messages


def test_build_coach_messages() -> None:
    messages = build_coach_messages(
        "Знакомство", [Line("them", "Привет"), Line("me", "Привет!")], "Как продолжить?"
    )

    assert [m.role for m in messages] == ["system", "user"]
    assert "коуч" in messages[0].content
    assert messages[1].content == (
        "Ситуация: Знакомство\n\nПереписка:\nСобеседник: Привет\nЯ: Привет!\n\n"
        "Что нужно: Как продолжить?"
    )


def test_build_coach_messages_without_dialogue() -> None:
    messages = build_coach_messages("Знакомство", [], "Первое сообщение")
    assert "(переписки ещё нет)" in messages[1].content


async def test_stub_echoes_user_prompt() -> None:
    messages = build_coach_messages("Знакомство", [Line("them", "Привет")], "Ответ")
    completion = await StubProvider().complete(messages)
    assert messages[1].content in completion.text
