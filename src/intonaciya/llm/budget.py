"""Spend and budget of an LLM gateway key (LiteLLM proxy `/key/info`)."""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class KeyBudget:
    alias: str | None
    spend: float
    max_budget: float | None

    @property
    def remaining(self) -> float | None:
        return None if self.max_budget is None else self.max_budget - self.spend

    @property
    def used_share(self) -> float | None:
        return self.spend / self.max_budget if self.max_budget else None


def _gateway_root(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url.removesuffix("/v1")


async def fetch_key_budget(
    api_key: str, base_url: str, *, client: httpx.AsyncClient | None = None
) -> KeyBudget:
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        response = await client.get(
            f"{_gateway_root(base_url)}/key/info",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    info = response.json().get("info", {})
    max_budget = info.get("max_budget")
    return KeyBudget(
        alias=info.get("key_alias"),
        spend=float(info.get("spend") or 0),
        max_budget=float(max_budget) if max_budget is not None else None,
    )


def crossed_threshold(
    budget: KeyBudget, thresholds: tuple[float, ...], already_alerted: set[float]
) -> float | None:
    """Highest threshold the spend has reached that was not alerted yet."""
    share = budget.used_share
    if share is None:
        return None
    reached = [t for t in thresholds if share >= t and t not in already_alerted]
    return max(reached) if reached else None
