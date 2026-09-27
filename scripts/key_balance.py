"""Show spend and remaining budget of the LLM gateway key from .env.

Usage:
    python scripts/key_balance.py
"""

import asyncio

from intonaciya.config import Settings
from intonaciya.llm.budget import fetch_key_budget


async def main() -> None:
    settings = Settings()
    if not (settings.llm_api_key and settings.llm_base_url):
        raise SystemExit("LLM_BASE_URL and LLM_API_KEY are not set")

    budget = await fetch_key_budget(settings.llm_api_key.get_secret_value(), settings.llm_base_url)
    print(f"Ключ:       {budget.alias or '—'}")
    print(f"Потрачено:  {budget.spend:,.2f}".replace(",", " "))
    if budget.max_budget is None:
        print("Бюджет:     не ограничен")
        return
    print(f"Бюджет:     {budget.max_budget:,.2f}".replace(",", " "))
    print(f"Остаток:    {budget.remaining:,.2f} ({1 - budget.used_share:.2%})".replace(",", " "))


if __name__ == "__main__":
    asyncio.run(main())
