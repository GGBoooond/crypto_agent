"""
纯 prompt 驱动策略基类（PromptOnlyAIStrategy）
架构定位: 让"加 prompt 就能新增策略"成为现实，覆盖 0/轻/重 三档触发器需求

设计理念:
1. 默认零触发器：每次 analyse 都直问 LLM，最少代码上线新策略
2. 声明式 TRIGGER_RULES：通过 module-level 函数列出几条独立、可读、可单测的规则，零样板
3. 标准 BUY/SELL/HOLD/CLOSE 解析：_extract_signal_default 涵盖绝大多数纯 prompt 策略

执行流程:
1. 基类 ``BaseAIStrategy.analyze()`` 执行模板编排
2. ``_check_hard_trigger`` 按 ``REQUIRES_HARD_TRIGGER`` + ``TRIGGER_RULES`` 决策是否调 LLM
3. ``_build_trigger_payload`` 装配 indicators + decision_schema + user_instruction
4. LLM 返回 JSON 后由 ``_extract_signal_default`` 映射成 Signal
"""
from __future__ import annotations

from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from config import settings
from core.message import Confidence, Signal, SignalType
from harness.context import StrategyContext

from .base_ai_strategy import BaseAIStrategy


TriggerRule = Callable[
    [pd.DataFrame, Optional[Dict[str, Any]]],
    Optional[Tuple[str, str, Dict[str, Any]]],
]
"""规则函数签名：(df, position) -> None | (signal_dir, reason, ctx_extra)。

返回 None 表示本规则未命中；返回三元组表示命中并附带额外上下文。
"""


class PromptOnlyAIStrategy(BaseAIStrategy):
    """让"新策略接入"在 0、轻、重三档触发器需求下都有清晰路径。

    模式 A — 纯 prompt（零代码）：
        - ``REQUIRES_HARD_TRIGGER = False``（默认）
        - 每次 analyse 都进入 LLM
        - 子类只需声明 ``SYSTEM_ROLE_OVERRIDE`` / ``DECISION_SCHEMA`` /
          ``USER_INSTRUCTION`` + 实现 ``_extract_signal``

    模式 B — 声明式触发器：
        - ``REQUIRES_HARD_TRIGGER = True`` + ``TRIGGER_RULES = [_rule_a, _rule_b]``
        - 第一条命中即调 LLM，全未命中 -> return None
        - 子类需实现 ``_compute_indicators`` 提供规则需要的列

    模式 C — 完全自定义：
        - 直接覆盖 ``_check_hard_trigger``
        - 这种情况通常直接继承 ``BaseAIStrategy`` 即可
    """

    # ---- Pipeline tuning ----
    REQUIRES_HARD_TRIGGER: bool = False
    TRIGGER_RULES: ClassVar[List[TriggerRule]] = []

    # ---- Prompt contract ----
    DECISION_SCHEMA: str = ""
    USER_INSTRUCTION: str = ""

    # ------------------------------------------------------------------
    # Hard triggers
    # ------------------------------------------------------------------
    def _check_hard_trigger(
        self,
        df: Optional[pd.DataFrame],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # 模式 A：直放行，让 LLM 兜底
        if not self.REQUIRES_HARD_TRIGGER:
            return True, "", {}

        # 模式 B：声明式规则但未配置，明确报错而非静默放行——避免新人配错以为有效
        if not self.TRIGGER_RULES:
            logger.warning(
                f"[{self.name}] REQUIRES_HARD_TRIGGER=True 但未配置 TRIGGER_RULES，"
                f"本次 analyse 直接跳过；如需每次都调 LLM 请改为 False。"
            )
            return False, "", {}

        if df is None or df.empty:
            return False, "", {}

        for rule in self.TRIGGER_RULES:
            try:
                hit = rule(df, position)
            except Exception as exc:
                logger.warning(
                    f"[{self.name}] 触发规则 {rule.__name__} 抛异常: {exc}"
                )
                continue
            if hit is None:
                continue
            signal_dir, reason, ctx_extra = hit
            return True, reason, {
                "signal_dir": signal_dir,
                "trigger": reason,
                **ctx_extra,
            }
        return False, "", {}

    # ------------------------------------------------------------------
    # Trigger payload
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
        snapshot = self._snapshot_indicators(df) if df is not None else {}
        payload: Dict[str, Any] = {
            "mode": mode,
            "decision_schema": self.DECISION_SCHEMA,
            "user_instruction": self.USER_INSTRUCTION,
            "indicators": snapshot,
        }
        if trigger_ctx:
            payload["trigger_reason"] = trigger_ctx.get("trigger") or "DIRECT_LLM"
            if "signal_dir" in trigger_ctx:
                payload["signal_dir"] = trigger_ctx["signal_dir"]
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _snapshot_indicators(df: pd.DataFrame) -> Dict[str, Any]:
        """Sample the latest indicator row as a flat dict for the prompt."""
        if df is None or df.empty:
            return {}
        last = df.iloc[-1]
        snapshot: Dict[str, Any] = {}
        # 只取常见技术指标列；忽略原始 OHLCV 与中间计算列
        for column in ("rsi", "macd", "hist", "atr", "ema20", "ema50", "ema200",
                       "upper_bb", "lower_bb", "ma20", "adx"):
            if column in df.columns:
                value = last.get(column)
                if pd.notna(value):
                    snapshot[column] = round(float(value), 6)
        snapshot["close"] = round(float(last["close"]), 6) if "close" in df.columns else None
        return {k: v for k, v in snapshot.items() if v is not None}

    # ------------------------------------------------------------------
    # Signal extraction (default impl reusable by subclasses)
    # ------------------------------------------------------------------
    def _extract_signal_default(
        self,
        ai_decision: Dict[str, Any],
        *,
        symbol: str,
        position: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
        **_kwargs: Any,
    ) -> Optional[Signal]:
        """Standard mapping for BUY/SELL/HOLD/CLOSE prompt-only strategies.

        Subclasses with ``DECISION_SCHEMA`` matching the standard contract
        ``{action, confidence, reason, entry_price, stop_loss, take_profit}``
        can simply delegate ``_extract_signal`` to this helper.
        """
        action = str(ai_decision.get("action", "HOLD")).upper()
        confidence_str = str(ai_decision.get("confidence", "LOW")).upper()
        reason = str(ai_decision.get("reason", "AI 无理由"))

        signal_type = self._map_action_to_signal_type(action, position)
        if signal_type is None:
            logger.info(f"[{self.name}] AI 决策 HOLD/未知: {reason}")
            return None

        confidence = self._map_confidence(confidence_str)
        stop_loss = self._safe_float(
            ai_decision.get("stop_loss", ai_decision.get("sl_price"))
        )
        take_profit = self._safe_float(
            ai_decision.get("take_profit", ai_decision.get("tp_price"))
        )

        return Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            amount=settings.trading_amount,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "ai_decision": ai_decision,
                "current_price": current_price,
            },
        )

    @staticmethod
    def _map_action_to_signal_type(
        action: str, position: Optional[Dict[str, Any]]
    ) -> Optional[SignalType]:
        if action in ("BUY", "EXECUTE_LONG"):
            return SignalType.BUY
        if action in ("SELL", "EXECUTE_SHORT"):
            return SignalType.SELL
        if action == "CLOSE":
            if position and str(position.get("side", "")).lower() == "short":
                return SignalType.CLOSE_SHORT
            return SignalType.CLOSE_LONG
        return None

    @staticmethod
    def _map_confidence(confidence_str: str) -> Confidence:
        return {
            "HIGH": Confidence.HIGH,
            "MEDIUM": Confidence.MEDIUM,
            "LOW": Confidence.LOW,
        }.get(confidence_str, Confidence.LOW)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
