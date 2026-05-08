"""Base class for AI-powered strategies that consume the harness context layers.

The legacy AI strategies all duplicated the same plumbing:
    - construct AsyncOpenAI client
    - cobble a prompt from raw klines
    - call chat.completions.create with timeout/JSON parsing
    - report a coarse `+1200 token` to the budget manager

BaseAIStrategy centralises that wiring so that subclasses only need to
implement the strategy-specific ``_build_trigger_payload`` and
``_extract_signal`` hooks. The actual prompt is composed by
``PromptBuilder.build_messages`` so MEMORY / USER / Skills / regime layers
are always present, and token usage is reported back from ``response.usage``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from openai import AsyncOpenAI

from config import settings
from core.message import Signal
from harness.context import KlineSummarizer, PromptBuilder, StrategyContext

from .base_strategy import BaseStrategy


class BaseAIStrategy(BaseStrategy):
    """Common scaffolding for AI strategies.

    Subclasses MUST implement:
        - ``analyze`` (high level orchestration; usually calls ``_run_llm`` once)
        - ``_extract_signal`` (turn the parsed AI decision into a ``Signal``)

    Subclasses MAY override:
        - ``_build_trigger_payload`` to render strategy-specific trigger context
    """

    DEFAULT_TIMEOUT_SECONDS: float = 45.0
    DEFAULT_TEMPERATURE: float = 0.2
    DEFAULT_MAX_TOKENS: int = 300

    def __init__(
        self,
        name: str,
        weight: float = 1.0,
        prompt_builder: Optional[PromptBuilder] = None,
        kline_summarizer: Optional[KlineSummarizer] = None,
        budget_manager: Optional[Any] = None,
    ):
        super().__init__(name=name, weight=weight)
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.kline_summarizer = kline_summarizer or KlineSummarizer()
        self.budget_manager = budget_manager
        self._last_llm_usage: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------
    def _build_trigger_payload(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Optional[Dict[str, Any]]:
        """Override to attach strategy-specific trigger payload.

        Default implementation returns None and lets the LLM see only the
        regime + memory + summary layers.
        """
        return None

    def _extract_signal(
        self,
        ai_decision: Dict[str, Any],
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
        trigger_payload: Optional[Dict[str, Any]],
    ) -> Optional[Signal]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_context(
        self,
        klines: List[Dict[str, Any]],
        context: Optional[StrategyContext],
        indicators_df: Optional[Any] = None,
    ) -> StrategyContext:
        """Always produce a StrategyContext, falling back to local computation.

        Newer strategies receive ``context`` from the StrategyAgent; older code
        paths or tests can still call the strategy directly without a context
        and we will compute a minimal one inline.
        """
        if context is not None and context.kline_summary:
            ctx = context
            if indicators_df is not None and not ctx.kline_summary.get("indicators"):
                ctx.kline_summary = self.kline_summarizer.summarize(
                    klines, indicators_df=indicators_df
                )
            if ctx.prompt_builder is None:
                ctx.prompt_builder = self.prompt_builder
            return ctx

        from harness.context import RegimeTagger

        regime, metrics = RegimeTagger().detect_with_metrics(klines)
        kline_summary = self.kline_summarizer.summarize(
            klines, indicators_df=indicators_df
        )
        return StrategyContext(
            regime=regime.value,
            regime_extra=f"change_pct={metrics.change_pct} volatility={metrics.volatility}",
            kline_summary=kline_summary,
            prompt_builder=self.prompt_builder,
        )

    def _build_messages(
        self,
        *,
        symbol: str,
        position: Optional[Dict[str, Any]],
        context: StrategyContext,
        strategy_payload: Optional[Dict[str, Any]],
        system_role_override: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        builder = context.prompt_builder or self.prompt_builder
        return builder.build_messages(
            symbol=symbol,
            regime=context.regime,
            kline_summary=context.kline_summary,
            position=position,
            strategy_payload=strategy_payload,
            regime_extra=context.regime_extra,
            system_role_override=system_role_override,
        )

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        model: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, int]]:
        """Send messages to the LLM and return (parsed_decision, usage_info).

        ``usage_info`` contains ``prompt_tokens`` / ``completion_tokens`` /
        ``total_tokens`` (0 when the SDK does not report). On timeout / empty
        response / JSON failure the parsed decision is ``None``.
        """
        usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=model or settings.ai_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout - 5 if timeout > 10 else timeout,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] LLM call timed out after {timeout}s")
            return None, usage
        except Exception as exc:
            logger.error(f"[{self.name}] LLM call failed: {exc}")
            return None, usage

        try:
            response_usage = getattr(response, "usage", None)
            if response_usage is not None:
                usage["prompt_tokens"] = int(getattr(response_usage, "prompt_tokens", 0) or 0)
                usage["completion_tokens"] = int(
                    getattr(response_usage, "completion_tokens", 0) or 0
                )
                usage["total_tokens"] = int(getattr(response_usage, "total_tokens", 0) or 0)
        except Exception:
            pass

        if usage["total_tokens"] == 0:
            usage["total_tokens"] = PromptBuilder.estimate_tokens(messages) + max_tokens // 2

        if self.budget_manager is not None and usage["total_tokens"] > 0:
            try:
                self.budget_manager.record_usage(usage["total_tokens"])
            except Exception:
                pass
        self._last_llm_usage = usage

        try:
            content = response.choices[0].message.content
        except Exception:
            return None, usage
        if not content or not content.strip():
            logger.warning(f"[{self.name}] LLM returned empty content")
            return None, usage

        parsed = self._extract_json(content)
        if parsed is None:
            logger.warning(f"[{self.name}] Failed to parse JSON from LLM response")
        return parsed, usage

    async def _run_llm(
        self,
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
        trigger_payload: Optional[Dict[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        system_role_override: Optional[str] = None,
        indicators_df: Optional[Any] = None,
    ) -> Optional[Signal]:
        """Top level helper: build messages -> call LLM -> hand to _extract_signal."""
        ctx = self._resolve_context(klines, context, indicators_df=indicators_df)
        messages = self._build_messages(
            symbol=symbol,
            position=position,
            context=ctx,
            strategy_payload=trigger_payload,
            system_role_override=system_role_override,
        )
        decision, usage = await self._call_llm(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if decision is None:
            return None
        signal = self._extract_signal(
            decision,
            symbol=symbol,
            klines=klines,
            market_data=market_data,
            position=position,
            context=ctx,
            trigger_payload=trigger_payload,
        )
        if signal is not None:
            metadata = signal.metadata or {}
            metadata.setdefault("llm_usage", usage)
            metadata.setdefault("regime", ctx.regime)
            kline_summary = ctx.kline_summary or {}
            metadata.setdefault(
                "kline_summary",
                {
                    "summary": kline_summary.get("summary"),
                    "tape_signature": kline_summary.get("tape_signature"),
                    "volume_anomaly": kline_summary.get("volume_anomaly"),
                },
            )
            metadata.setdefault("prompt_messages", messages)
            signal.metadata = metadata
        return signal

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return None
