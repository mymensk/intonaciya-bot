import httpx
import pytest

from intonaciya.llm.budget import KeyBudget, crossed_threshold, fetch_key_budget

THRESHOLDS = (0.5, 0.8, 0.95)


async def test_fetch_key_budget_calls_gateway_root() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"info": {"key_alias": "team", "spend": 12.5, "max_budget": 100}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    budget = await fetch_key_budget("k", "https://gw.example:4000/v1/", client=client)

    assert budget == KeyBudget(alias="team", spend=12.5, max_budget=100.0)
    assert budget.remaining == 87.5
    assert str(requests[0].url) == "https://gw.example:4000/key/info"
    assert requests[0].headers["Authorization"] == "Bearer k"


def test_unlimited_budget_has_no_share() -> None:
    budget = KeyBudget(alias=None, spend=5, max_budget=None)
    assert budget.remaining is None
    assert crossed_threshold(budget, THRESHOLDS, set()) is None


@pytest.mark.parametrize(
    ("spend", "alerted", "expected"),
    [
        (10, set(), None),
        (50, set(), 0.5),
        (85, set(), 0.8),
        (85, {0.5, 0.8}, None),
        (96, {0.5, 0.8}, 0.95),
    ],
)
def test_crossed_threshold(spend: float, alerted: set[float], expected: float | None) -> None:
    budget = KeyBudget(alias=None, spend=spend, max_budget=100)
    assert crossed_threshold(budget, THRESHOLDS, alerted) == expected
