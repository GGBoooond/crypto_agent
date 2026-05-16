"""Base class for AI-powered strategies that consume the harness context layers.

The legacy AI strategies all duplicated the same plumbing:
    - construct AsyncOpenAI client
    - cobble a prompt from raw klines
    - call chat.completions.create with timeout/JSON parsing
    - report a coarse `+1200 token` to the budget manager

BaseAIStrategy centralises that wiring so that subclasses only need to
implement strategy-specific hooks (indicators, hard trigger, payload, signal
extraction). The actual prompt is composed by ``PromptBuilder.build_messages``
so MEMORY / USER / Skills / regime layers are always present, and token usage
is reported back from ``response.usage``.

The class follows a template-method pattern: subclasses **do not override**
``analyze``; they only fill in hooks. The orchestration flow is::

    analyze
      -> _compute_indicators
      -> if has_position: _on_position_pre_llm (may short-circuit)
      -> else: _check_hard_trigger
      -> _collect_extra_payload
      -> _build_trigger_payload
      -> _run_llm
      -> _extract_signal
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from core.message import Signal
from harness.context import KlineSummarizer, PromptBuilder, StrategyContext

from .base_strategy import BaseStrategy
from .llm_client import LLMClient


class BaseAIStrategy(BaseStrategy):
    """Common scaffolding for AI strategies (template-method pattern).

    Subclasses MUST implement:
        - ``_extract_signal``: turn the parsed AI decision into a ``Signal``

    Subclasses MAY override (all hooks have safe defaults):
        - ``_compute_indicators``       : compute pandas indicators from klines
        - ``_check_hard_trigger``       : python-level filter before LLM call
        - ``_on_position_pre_llm``      : pre-LLM short-circuit when holding
        - ``_collect_extra_payload``    : async IO for additional context
        - ``_build_trigger_payload``    : strategy-specific prompt payload

    Subclasses **must not** override ``analyze``; the orchestration is fixed.
    """

    # ---- Pipeline tuning (subclasses override via class attributes) ----
    MIN_KLINES: int = 50
    REQUIRES_HARD_TRIGGER: bool = True
    MAX_TOKENS: int = 800
    TEMPERATURE: float = 0.2
    POSITION_CHECK_MAX_TOKENS: int = 600

    # ---- Prompt contract (subclasses override) ----
    SYSTEM_ROLE_OVERRIDE: Optional[str] = None
    DECISION_SCHEMA: str = ""
    USER_INSTRUCTION: str = ""

    # ---- Internal LLM call defaults ----
    DEFAULT_TIMEOUT_SECONDS: float = 45.0

    def __init__(
        self,
        name: str,
        weight: float = 1.0,
        prompt_builder: Optional[PromptBuilder] = None,
        kline_summarizer: Optional[KlineSummarizer] = None,
        budget_manager: Optional[Any] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        super().__init__(name=name, weight=weight)
        self.llm_client = llm_client or LLMClient()
        # ``self.client`` 作为旧字段保留：仍然指向底层 ``AsyncOpenAI`` 实例，
        # 主要给现存测试 ``patch.object(strategy.client.chat.completions, "create")`` 用。
        self.client = self.llm_client.async_client
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.kline_summarizer = kline_summarizer or KlineSummarizer()
        self.budget_manager = budget_manager
        self._last_llm_usage: Dict[str, int] = {}
        self._last_model_version: Optional[str] = None

    # ------------------------------------------------------------------
    # Indicators (hook)
    # ------------------------------------------------------------------
    def _compute_indicators(
        self, klines: List[Dict[str, Any]]
    ) -> Optional[pd.DataFrame]:
        """Compute pandas DataFrame with technical indicators.

        Default returns ``None``: the strategy operates without dataframe-level
        indicators (e.g. pure-prompt策略). Subclasses that need RSI/ATR/etc
        should override and return a populated DataFrame.
        """
        return None

    # ------------------------------------------------------------------
    # Hard triggers (hook)
    # ------------------------------------------------------------------
    def _check_hard_trigger(
        self,
        df: Optional[pd.DataFrame],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Decide whether to invoke the LLM for a fresh open signal.

        Default implementation always returns ``(True, "", {})``: the LLM is
        consulted on every analyse cycle. Subclasses combine ``REQUIRES_HARD_TRIGGER``
        with this method to enforce stricter pre-filters.
        """
        return True, "", {}

    # ------------------------------------------------------------------
    # Position pre-LLM hooks (optional)
    # ------------------------------------------------------------------
    async def _on_position_pre_llm(
        self,
        *,
        symbol: str,
        df: Optional[pd.DataFrame],
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Dict[str, Any],
        context: Optional[StrategyContext],
    ) -> Optional[Signal]:
        """Optional local short-circuit before invoking the LLM on a position.

        Useful for cheap, deterministic actions (e.g. trailing stop adjustment)
        that should bypass the LLM cost. Returning a ``Signal`` skips the LLM
        call entirely; returning ``None`` lets ``analyze`` continue into the
        position-check LLM path.
        """
        return None

    # ------------------------------------------------------------------
    # Extra payload (optional async IO)
    # ------------------------------------------------------------------
    async def _collect_extra_payload(
        self,
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Dict[str, Any]:
        """Gather any extra structured data to inject into the prompt.

        Common use: BTC trend, funding rate, support/resistance levels. Return
        a dict whose keys are merged into the trigger payload before being
        rendered into the prompt's ``[EXTRA_CONTEXT]`` section.
        """
        return {}

    # ------------------------------------------------------------------
    # Trigger payload (hook)
    # ------------------------------------------------------------------
    def _build_trigger_payload(
        self,
        *,
        df: Optional[pd.DataFrame],
        trigger_ctx: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        mode: str,
        extra: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Render strategy-specific trigger payload for the prompt.

        Default returns ``None`` so the LLM only sees regime + memory + summary
        layers. Subclasses typically return a dict with ``mode``, ``signal_dir``,
        ``trigger_reason``, ``indicators``, ``decision_schema`` and any extra
        keys needed by ``_extract_signal``.
        """
        return None

    # ------------------------------------------------------------------
    # Signal extraction (must be implemented by subclasses)
    # ------------------------------------------------------------------
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
    # Orchestration (fixed template — do not override in subclasses)
    # ------------------------------------------------------------------
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None,
        context: Optional[StrategyContext] = None,
    ) -> Optional[Signal]:
        if not self.enabled:
            return None
        if not klines or len(klines) < self.MIN_KLINES:
            logger.warning(
                f"[{self.name}] K线数据不足(需{self.MIN_KLINES}+): "
                f"{len(klines) if klines else 0}"
            )
            return None

        df = self._compute_indicators(klines)

        if self._has_open_position(position):
            early_signal = await self._on_position_pre_llm(
                symbol=symbol,
                df=df,
                klines=klines,
                market_data=market_data,
                position=position,
                context=context,
            )
            if early_signal is not None:
                return early_signal
            mode = "position_check"
            trigger_ctx: Dict[str, Any] = {
                "signal_dir": str(position.get("side", "")).upper(),
                "trigger": "POSITION_CHECK",
            }
            max_tokens = self.POSITION_CHECK_MAX_TOKENS
        else:
            triggered, _reason, trigger_ctx = self._check_hard_trigger(
                df, position, context
            )
            if not triggered and self.REQUIRES_HARD_TRIGGER:
                return None
            mode = "open"
            max_tokens = self.MAX_TOKENS

        extra = await self._collect_extra_payload(
            symbol=symbol,
            klines=klines,
            market_data=market_data,
            position=position,
            context=context,
        )
        trigger_payload = self._build_trigger_payload(
            df=df,
            trigger_ctx=trigger_ctx,
            position=position,
            mode=mode,
            extra=extra,
        )
        return await self._run_llm(
            symbol=symbol,
            klines=klines,
            market_data=market_data,
            position=position,
            context=context,
            trigger_payload=trigger_payload,
            max_tokens=max_tokens,
            temperature=self.TEMPERATURE,
            system_role_override=self.SYSTEM_ROLE_OVERRIDE,
            indicators_df=df,
        )

    @staticmethod
    def _has_open_position(position: Optional[Dict[str, Any]]) -> bool:
        if not position:
            return False
        try:
            return float(position.get("size", 0)) > 0
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Internal helpers (LLM call, JSON extraction)
    # ------------------------------------------------------------------
    def _resolve_context(
        self,
        klines: List[Dict[str, Any]],
        context: Optional[StrategyContext],
        indicators_df: Optional[pd.DataFrame] = None,
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
        max_tokens: int = 800,
        temperature: float = 0.2,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        model: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, int]]:
        """Send messages to the LLM and return (parsed_decision, usage_info)."""
        usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat_completion(
                    model=model or self.llm_client.model,
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

        # 不同 LLM provider 的 usage 字段差异较大，解析失败时走下方估算兜底
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

        # 预算记录失败不应阻断主链路（最坏情况下只是日预算偏差）
        if self.budget_manager is not None and usage["total_tokens"] > 0:
            try:
                self.budget_manager.record_usage(usage["total_tokens"])
            except Exception:
                pass
        self._last_llm_usage = usage
        try:
            self._last_model_version = getattr(response, "model", None)
        except Exception:
            self._last_model_version = None

        content = self._extract_response_text(response)
        if not content or not content.strip():
            self._log_empty_content_diagnostics(response, usage)
            return None, usage

        parsed = self._extract_json(content)
        if parsed is None:
            preview = content.strip().replace("\n", " ")[:200]
            logger.warning(
                f"[{self.name}] Failed to parse JSON from LLM response | preview={preview!r}"
            )
        return parsed, usage

    def _log_empty_content_diagnostics(
        self, response: Any, usage: Dict[str, int]
    ) -> None:
        """Log enough context to diagnose why ``message.content`` came back empty."""
        finish_reason: Optional[str] = None
        message_attrs: List[str] = []
        reasoning_preview: Optional[str] = None
        try:
            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            message = getattr(choice, "message", None)
            if message is not None:
                for key in (
                    "content",
                    "reasoning_content",
                    "reasoning",
                    "tool_calls",
                    "function_call",
                ):
                    value = getattr(message, key, None)
                    if value:
                        message_attrs.append(key)
                raw_reasoning = getattr(message, "reasoning_content", None) or getattr(
                    message, "reasoning", None
                )
                reasoning_text = self._content_to_text(raw_reasoning)
                if reasoning_text:
                    reasoning_preview = reasoning_text.replace("\n", " ")[:200]
        except Exception:
            # 诊断信息抓取失败时，下方主告警仍会照常发出
            pass

        logger.warning(
            f"[{self.name}] LLM returned empty content | "
            f"finish_reason={finish_reason} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"prompt_tokens={usage.get('prompt_tokens')} "
            f"populated_fields={message_attrs or None} "
            f"reasoning_preview={reasoning_preview!r}"
        )

        if finish_reason == "length":
            logger.warning(
                f"[{self.name}] Output truncated by max_tokens; "
                f"consider raising max_tokens above {self.MAX_TOKENS} "
                f"(reasoning-style models need extra budget)."
            )

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        """Extract text from various OpenAI-compatible response formats."""
        try:
            message = response.choices[0].message
        except Exception:
            return ""

        content = getattr(message, "content", None)
        text = BaseAIStrategy._content_to_text(content)
        if text:
            return text

        # 部分 provider 把 payload 放在 tool/function call 的 arguments 里
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            function_obj = getattr(tool_call, "function", None)
            arguments = getattr(function_obj, "arguments", None)
            tool_text = BaseAIStrategy._content_to_text(arguments)
            if tool_text:
                return tool_text

        function_call = getattr(message, "function_call", None)
        if function_call is not None:
            arguments = getattr(function_call, "arguments", None)
            fn_text = BaseAIStrategy._content_to_text(arguments)
            if fn_text:
                return fn_text
        return ""

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    normalized = item.strip()
                    if normalized:
                        parts.append(normalized)
                    continue
                if not isinstance(item, dict):
                    continue
                for key in ("text", "content", "value"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                        break
            return "\n".join(parts).strip()
        return ""

    async def _run_llm(
        self,
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
        trigger_payload: Optional[Dict[str, Any]],
        max_tokens: int = 800,
        temperature: float = 0.2,
        system_role_override: Optional[str] = None,
        indicators_df: Optional[pd.DataFrame] = None,
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
            metadata.setdefault("skill_used", self._format_skill_ids(ctx))
            self._merge_decision_metadata(metadata, decision)
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
    def _format_skill_ids(context: StrategyContext) -> Optional[str]:
        builder = context.prompt_builder
        if builder is None:
            return None
        skill_ids = getattr(builder, "last_injected_skill_ids", [])
        if not skill_ids:
            return None
        return ",".join(str(skill_id) for skill_id in skill_ids)

    @staticmethod
    def _merge_decision_metadata(
        metadata: Dict[str, Any], decision: Dict[str, Any]
    ) -> None:
        for key in ("fine_regime", "key_observations", "confidence_breakdown"):
            value = decision.get(key)
            if value is not None:
                metadata.setdefault(key, value)

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None

        for candidate in BaseAIStrategy._candidate_json_strings(text):
            parsed = BaseAIStrategy._loads_json_candidate(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _candidate_json_strings(text: str) -> List[str]:
        candidates: List[str] = [text.strip()]
        code_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, flags=re.DOTALL)
        candidates.extend(block.strip() for block in code_blocks if block and block.strip())
        brace_candidate = BaseAIStrategy._extract_outer_braces(text)
        if brace_candidate:
            candidates.append(brace_candidate)
        unique: List[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    @staticmethod
    def _extract_outer_braces(text: str) -> Optional[str]:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]

    @staticmethod
    def _loads_json_candidate(candidate: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            cleaned = re.sub(r"//.*?(?=\n|$)", "", candidate)
            cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
            try:
                parsed = json.loads(cleaned)
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
