"""Reviewer LLM client for postmortem and patch review."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from config import settings
from harness.cost.budget import CostBudgetManager


class ReviewerClient:
    """OpenAI-compatible client isolated from strategy LLM settings."""

    def __init__(
        self,
        *,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        daily_token_limit: Optional[int] = None,
    ) -> None:
        settings.validate_reviewer_is_independent()
        self.provider = provider or settings.get_reviewer_provider()
        self.api_key = api_key or settings.get_reviewer_api_key()
        self.base_url = base_url or settings.get_reviewer_base_url()
        self.model = model or settings.get_reviewer_model()
        self.budget = CostBudgetManager(
            daily_token_limit=daily_token_limit or settings.llm_reviewer_daily_token_limit,
            per_call_limit=settings.llm_per_call_token_limit,
        )
        self._client: Optional[AsyncOpenAI] = None
        if self.api_key and self.base_url and self.model:
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 500,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Reviewer LLM is not configured")
        budget = self.budget.check_before_call(expected_tokens=max_tokens)
        if not budget.allowed:
            raise RuntimeError(f"Reviewer LLM budget rejected call: {budget.reason}")
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        usage = getattr(response, "usage", None)
        used_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        self.budget.record_usage(used_tokens)
        content = response.choices[0].message.content or "{}"
        return self._parse_json(content)

    def chat_json_sync(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 500,
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.chat_json(messages, temperature=temperature, max_tokens=max_tokens)
            )
        if loop.is_running():
            raise RuntimeError("Use async ReviewerClient.chat_json inside a running event loop")
        return loop.run_until_complete(
            self.chat_json(messages, temperature=temperature, max_tokens=max_tokens)
        )

    @staticmethod
    def _parse_json(content: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Reviewer LLM returned non-JSON content: {content[:200]}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Reviewer LLM JSON response must be an object")
        return parsed
