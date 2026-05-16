"""
策略Agent - 可插拔策略管理
支持：
- 单策略模式：使用单一策略
- 投票模式：多策略加权投票
"""
import asyncio
import hashlib
import inspect
import json
from typing import List, Optional, Dict, Any
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType, Signal, SignalType, Confidence
from strategies import StrategyFactory, BaseStrategy
from strategies.base_ai_strategy import BaseAIStrategy
from indicators import TechnicalIndicators
from config import settings
from harness.cost import CostBudgetManager, StrategyDegrader
from harness.context import (
    KlineSummarizer,
    RegimeTagger,
    PromptBuilder,
    StrategyContext,
)
from harness.observability import TraceRecorder
from harness.verification.schema import parse_signal_dict
from harness.verification.sanity import SanityVerifier
from harness.verification.policy_gate import PolicyGate, as_verification_result


class StrategyAgent(BaseAgent):
    """
    策略Agent - 管理和执行交易策略
    """
    
    def __init__(self):
        super().__init__(name="StrategyAgent")
        
        self.mode = settings.strategy_mode  # single / voting
        self.vote_threshold = settings.vote_threshold
        self.indicators = TechnicalIndicators()
        self.budget_manager = CostBudgetManager(
            daily_token_limit=settings.llm_daily_token_limit,
            per_call_limit=settings.llm_per_call_token_limit,
        )
        self.degrader = StrategyDegrader()
        self.kline_summarizer = KlineSummarizer()
        self.regime_tagger = RegimeTagger()
        self.prompt_builder = PromptBuilder()
        self.trace = TraceRecorder()
        self.sanity_verifier = SanityVerifier()
        self.policy_gate = PolicyGate()
        
        # 动态加载策略
        self.strategies: List[BaseStrategy] = []
        self._load_strategies()
        # 把共享的 prompt builder / budget manager 注入给 AI 策略，避免重复加载 MEMORY/SKILL
        self._wire_ai_strategies()
        
        # 统计
        self.stats = {
            'total_analyses': 0,
            'signals_generated': 0,
            'buy_signals': 0,
            'sell_signals': 0,
        }
    
    def _load_strategies(self):
        """从配置加载策略"""
        strategy_names = settings.get_enabled_strategies()
        
        if not strategy_names:
            logger.warning("未配置任何策略，使用默认ai_scalping")
            strategy_names = ["ai_scalping"]
        
        self.strategies = StrategyFactory.create_multiple(strategy_names)
        
        logger.info(f"策略模式: {self.mode}")
        logger.info(f"已加载策略: {[s.name for s in self.strategies]}")
    
    def _register_handlers(self):
        self.register_handler(MessageType.MARKET_DATA, self._on_market_data)
    
    async def _on_market_data(self, message: Message):
        """处理市场数据"""
        try:
            data = message.data
            symbol = data['symbol']
            klines = data['klines']
            ticker = data['ticker']
            
            self.stats['total_analyses'] += 1
            
            if not klines or len(klines) < 10:
                await self.log("WARNING", "K线数据不足")
                return
            
            # 当前持仓
            position = None
            if symbol in self.state_store.positions:
                position = self.state_store.positions[symbol].to_dict()
            
            indicators_df = self._compute_shared_indicators(klines)
            regime_enum, regime_metrics = self.regime_tagger.detect_with_metrics(
                klines, indicators_df=indicators_df
            )
            regime = regime_enum.value
            regime_extra = self._build_regime_extra(regime_metrics)
            kline_summary = self.kline_summarizer.summarize(
                klines, indicators_df=indicators_df
            )
            prompt = self.prompt_builder.build(
                symbol, regime, kline_summary, position, regime_extra=regime_extra
            )
            trace_id = self.trace.new_trace_id()
            strategy_ctx = StrategyContext(
                regime=regime,
                regime_extra=regime_extra,
                kline_summary=kline_summary,
                prompt_builder=self.prompt_builder,
                trace_id=trace_id,
            )

            # 预算检查（超过预算时降级到技术指标）。预算估算改为基于实际 prompt 字符数
            estimated_tokens = max(
                1200,
                PromptBuilder.estimate_tokens([{"role": "user", "content": prompt}]),
            )
            budget_check = self.budget_manager.check_before_call(expected_tokens=estimated_tokens)
            use_fallback = not budget_check.allowed

            # 执行策略分析
            if use_fallback:
                final_signal = await self.degrader.fallback_signal(symbol, klines, ticker, position)
            elif self.mode == "single":
                final_signal = await self._single_strategy_analyze(
                    symbol, klines, ticker, position, strategy_ctx
                )
            else:
                final_signal = await self._voting_analyze(
                    symbol, klines, ticker, position, strategy_ctx
                )
            
            # 发送信号
            # 注意: HOLD 信号如果带有 adjust_tp_sl 标记，也需要发送（用于调整止盈止损）
            should_emit = final_signal and (
                final_signal.signal_type != SignalType.HOLD or 
                final_signal.metadata.get('adjust_tp_sl', False)
            )
            
            if should_emit:
                # 三层校验：schema -> sanity -> policy（trace_id 已在前面生成）
                schema_result = parse_signal_dict(final_signal.to_dict())
                if not schema_result.passed or not schema_result.signal:
                    await self.log("WARNING", f"Schema reject: {schema_result.reason}")
                    self.trace.record(
                        {
                            "trace_id": trace_id,
                            "symbol": symbol,
                            "stage": "verification_schema_reject",
                            "verification_result": schema_result.reason,
                            "llm_prompt": prompt,
                            "llm_response": str(final_signal.to_dict()),
                            "regime": regime,
                            "fine_regime": final_signal.metadata.get("fine_regime"),
                        }
                    )
                    return

                sanity_result = self.sanity_verifier.verify(
                    schema_result.signal,
                    float(klines[-1]["close"]),
                    {"atr": schema_result.signal.metadata.get("atr")},
                )
                if not sanity_result.passed:
                    await self.log("WARNING", f"Sanity reject: {sanity_result.reason}")
                    self.trace.record(
                        {
                            "trace_id": trace_id,
                            "symbol": symbol,
                            "stage": "verification_sanity_reject",
                            "verification_result": sanity_result.reason,
                            "llm_prompt": prompt,
                            "llm_response": str(schema_result.signal.to_dict()),
                            "regime": regime,
                            "fine_regime": schema_result.signal.metadata.get("fine_regime"),
                        }
                    )
                    return

                policy_result = await self.policy_gate.check_signal(
                    schema_result.signal,
                    float(klines[-1]["close"]),
                    self.state_store.balance,
                    self.state_store.positions.get(symbol),
                )
                policy_verification = as_verification_result(policy_result)
                if not policy_verification.passed or not policy_verification.signal:
                    await self.log("WARNING", f"Policy reject: {policy_verification.reason}")
                    self.trace.record(
                        {
                            "trace_id": trace_id,
                            "symbol": symbol,
                            "stage": "verification_policy_reject",
                            "verification_result": policy_verification.reason,
                            "llm_prompt": prompt,
                            "llm_response": str(schema_result.signal.to_dict()),
                            "regime": regime,
                            "fine_regime": schema_result.signal.metadata.get("fine_regime"),
                        }
                    )
                    return

                final_signal = policy_verification.signal
                self.stats['signals_generated'] += 1
                
                if final_signal.signal_type == SignalType.BUY:
                    self.stats['buy_signals'] += 1
                elif final_signal.signal_type == SignalType.SELL:
                    self.stats['sell_signals'] += 1
                
                await self.state_store.add_signal(final_signal.to_dict())

                # 如策略真实拿到了 prompt_messages（走 harness 路径），优先记录策略真实 prompt
                strategy_prompt_text, llm_usage = self._extract_prompt_and_usage(
                    final_signal, fallback_prompt=prompt
                )
                prompt_hash = hashlib.sha256(
                    strategy_prompt_text.encode("utf-8")
                ).hexdigest()[:16]
                kline_compression_ratio = self._compute_compression_ratio(
                    klines, strategy_prompt_text
                )
                market_metadata = self._build_market_metadata(data)
                model_version = self._extract_model_version()

                await self.emit(
                    MessageType.SIGNAL_GENERATED,
                    {
                        'signal': final_signal.to_dict(),
                        'current_price': klines[-1]['close'],
                        'trace_id': trace_id,
                        'regime': regime,
                        'prompt': strategy_prompt_text,
                        'kline_summary': kline_summary,
                        'llm_usage': llm_usage,
                    },
                    target="RiskAgent"
                )

                self.trace.record(
                    {
                        "trace_id": trace_id,
                        "symbol": symbol,
                        "regime": regime,
                        "stage": "signal_generated",
                        "llm_prompt": strategy_prompt_text,
                        "llm_response": str(final_signal.to_dict()),
                        "verification_result": policy_verification.reason,
                        "side": final_signal.signal_type.value,
                        "qty": final_signal.amount,
                        "skill_used": final_signal.metadata.get("skill_used"),
                        "fine_regime": final_signal.metadata.get("fine_regime"),
                        "prompt_tokens": llm_usage.get("prompt_tokens"),
                        "completion_tokens": llm_usage.get("completion_tokens"),
                        "kline_compression_ratio": kline_compression_ratio,
                        "model_id": settings.ai_model,
                        "model_version": model_version,
                        "prompt_hash": prompt_hash,
                        **market_metadata,
                    }
                )
                
                # 调整止盈止损用不同的日志格式
                if final_signal.metadata.get('adjust_tp_sl'):
                    tp = final_signal.metadata.get('tp_price') or final_signal.take_profit
                    sl = final_signal.metadata.get('sl_price') or final_signal.stop_loss
                    tp_text = f"{float(tp):.5f}" if tp is not None else "None"
                    sl_text = f"{float(sl):.5f}" if sl is not None else "None"
                    await self.log(
                        "INFO",
                        f"🔧 调整TP/SL: TP={tp_text}, SL={sl_text}"
                    )
                else:
                    await self.log(
                        "INFO",
                        f"📊 信号: {final_signal.signal_type.value} | "
                        f"{final_signal.confidence.value} | {final_signal.reason[:40]}"
                    )
                
        except Exception as e:
            await self.log("ERROR", f"策略分析失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    async def _single_strategy_analyze(
        self,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict],
        context: Optional[StrategyContext] = None,
    ) -> Optional[Signal]:
        """单策略模式"""
        if not self.strategies:
            return None

        strategy = self.strategies[0]

        try:
            signal = await self._invoke_analyze(strategy, symbol, klines, ticker, position, context)
            return signal
        except Exception as e:
            await self.log("ERROR", f"策略{strategy.name}执行失败: {e}")
            fallback = await self.degrader.fallback_signal(symbol, klines, ticker, position)
            if fallback:
                fallback.metadata["degraded_from"] = strategy.name
            return fallback
    
    async def _voting_analyze(
        self,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict],
        context: Optional[StrategyContext] = None,
    ) -> Optional[Signal]:
        """投票模式"""
        signals: List[Signal] = []
        
        # 并行执行所有策略
        tasks = []
        for strategy in self.strategies:
            if strategy.enabled:
                tasks.append(
                    self._get_signal(strategy, symbol, klines, ticker, position, context)
                )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Signal):
                signals.append(result)
        
        if not signals:
            return None
        
        return self._aggregate_signals(signals, symbol)
    
    async def _get_signal(
        self,
        strategy: BaseStrategy,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict],
        context: Optional[StrategyContext] = None,
    ) -> Optional[Signal]:
        """获取单个策略信号"""
        try:
            return await self._invoke_analyze(strategy, symbol, klines, ticker, position, context)
        except Exception as e:
            logger.error(f"策略{strategy.name}异常: {e}")
            return None
    
    def _aggregate_signals(
        self,
        signals: List[Signal],
        symbol: str
    ) -> Optional[Signal]:
        """信号投票聚合"""
        if not signals:
            return None
        
        # 权重统计
        weights = {'buy': 0.0, 'sell': 0.0, 'hold': 0.0, 'close': 0.0}
        reasons = {'buy': [], 'sell': [], 'close': []}
        
        confidence_mult = {
            Confidence.HIGH: 1.0,
            Confidence.MEDIUM: 0.7,
            Confidence.LOW: 0.4
        }
        
        for sig in signals:
            w = sig.weight * confidence_mult.get(sig.confidence, 0.5)
            
            if sig.signal_type == SignalType.BUY:
                weights['buy'] += w
                reasons['buy'].append(sig.reason)
            elif sig.signal_type == SignalType.SELL:
                weights['sell'] += w
                reasons['sell'].append(sig.reason)
            elif sig.signal_type in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]:
                weights['close'] += w
                reasons['close'].append(sig.reason)
            else:
                weights['hold'] += w
        
        total = sum(weights.values())
        if total == 0:
            return None
        
        # 确定方向
        threshold = self.vote_threshold
        
        if weights['close'] / total > threshold:
            # 平仓信号优先
            signal_type = SignalType.CLOSE_LONG  # 会在executor中判断实际方向
            reason_list = reasons['close']
        elif weights['buy'] / total > threshold and weights['buy'] > weights['sell']:
            signal_type = SignalType.BUY
            reason_list = reasons['buy']
        elif weights['sell'] / total > threshold and weights['sell'] > weights['buy']:
            signal_type = SignalType.SELL
            reason_list = reasons['sell']
        else:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                confidence=Confidence.LOW,
                reason="投票未达阈值",
                strategy_name="Voting"
            )
        
        # 置信度
        score = max(weights['buy'], weights['sell'], weights['close']) / total
        confidence = Confidence.HIGH if score >= 0.7 else Confidence.MEDIUM if score >= 0.5 else Confidence.LOW
        
        # 止损止盈取平均
        sl_list = [s.stop_loss for s in signals if s.stop_loss and s.signal_type == signal_type]
        tp_list = [s.take_profit for s in signals if s.take_profit and s.signal_type == signal_type]
        
        return Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=" | ".join(reason_list[:2]),
            stop_loss=sum(sl_list)/len(sl_list) if sl_list else None,
            take_profit=sum(tp_list)/len(tp_list) if tp_list else None,
            amount=settings.trading_amount,
            strategy_name="Voting",
            weight=1.0,
            metadata={'weights': weights}
        )
    
    async def handle_message(self, message: Message):
        if message.msg_type == MessageType.SYSTEM_STOP:
            await self.stop()
    
    def get_stats(self) -> Dict[str, Any]:
        return self.stats

    # ------------------------------------------------------------------
    # Helpers for strategy invocation, prompt extraction, compression metric
    # ------------------------------------------------------------------
    def _wire_ai_strategies(self) -> None:
        """Inject shared PromptBuilder + budget manager into AI strategies.

        Avoids each strategy maintaining its own MEMORY/SKILL caches and lets
        token usage flow back into the global budget manager.
        """
        for strategy in self.strategies:
            if isinstance(strategy, BaseAIStrategy):
                strategy.prompt_builder = self.prompt_builder
                strategy.kline_summarizer = self.kline_summarizer
                strategy.budget_manager = self.budget_manager

    def _compute_shared_indicators(self, klines: List[Dict[str, Any]]) -> Optional[Any]:
        """Use the first AI strategy's indicator set for shared context layers."""
        for strategy in self.strategies:
            if not isinstance(strategy, BaseAIStrategy):
                continue
            try:
                return strategy._compute_indicators(klines)
            except Exception as exc:
                logger.debug(f"共享指标计算失败({strategy.name}): {exc}")
                return None
        return None

    @staticmethod
    def _build_regime_extra(regime_metrics: Any) -> str:
        return (
            f"change_pct={regime_metrics.change_pct} "
            f"volatility={regime_metrics.volatility} "
            f"sample_size={regime_metrics.sample_size} "
            f"atr_rank={regime_metrics.atr_rank} "
            f"adx={regime_metrics.adx} "
            f"bb_width_rank={regime_metrics.bb_width_rank} "
            f"volume_price_corr={regime_metrics.volume_price_corr}"
        )

    async def _invoke_analyze(
        self,
        strategy: BaseStrategy,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict],
        context: Optional[StrategyContext],
    ) -> Optional[Signal]:
        """Call strategy.analyze with optional context, gracefully degrading
        for legacy strategies whose signature does not accept it."""
        analyze = strategy.analyze
        try:
            sig = inspect.signature(analyze)
            if "context" in sig.parameters:
                return await analyze(symbol, klines, ticker, position, context=context)
        except (TypeError, ValueError):
            pass
        return await analyze(symbol, klines, ticker, position)

    @staticmethod
    def _extract_prompt_and_usage(
        signal: Signal,
        fallback_prompt: str,
    ) -> tuple:
        """Pull the actual prompt sent to the LLM (if available) and usage."""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        prompt_text = fallback_prompt
        try:
            metadata = signal.metadata or {}
            messages = metadata.get("prompt_messages")
            if messages:
                prompt_text = "\n\n".join(
                    f"[{m.get('role', '')}]\n{m.get('content', '')}"
                    for m in messages
                )
            llm_usage = metadata.get("llm_usage")
            if llm_usage:
                usage = {
                    "prompt_tokens": int(llm_usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(llm_usage.get("completion_tokens", 0) or 0),
                    "total_tokens": int(llm_usage.get("total_tokens", 0) or 0),
                }
        except Exception:
            pass
        return prompt_text, usage

    @staticmethod
    def _compute_compression_ratio(klines: List[Dict[str, Any]], prompt_text: str) -> float:
        """Naive ratio: prompt_chars / raw_kline_chars (lower = more compressed)."""
        if not klines or not prompt_text:
            return 0.0
        raw_chars = sum(
            len(str(k.get("open", ""))) + len(str(k.get("close", "")))
            + len(str(k.get("high", ""))) + len(str(k.get("low", "")))
            + len(str(k.get("volume", "")))
            for k in klines
        )
        if raw_chars == 0:
            return 0.0
        return round(len(prompt_text) / raw_chars, 3)

    @staticmethod
    def _build_market_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract trace-safe market metadata from MARKET_DATA event."""
        depth = data.get("book_depth_top5")
        try:
            depth_json = json.dumps(depth, ensure_ascii=True) if depth is not None else None
        except (TypeError, ValueError):
            depth_json = None
        return {
            "funding_rate": data.get("funding_rate"),
            "next_funding_time": data.get("next_funding_time"),
            "open_interest": data.get("open_interest"),
            "bid_ask_spread": data.get("bid_ask_spread"),
            "book_imbalance": data.get("book_imbalance"),
            "book_depth_top5": depth_json,
        }

    def _extract_model_version(self) -> Optional[str]:
        """Best-effort pull model version from AI strategies."""
        for strategy in self.strategies:
            if isinstance(strategy, BaseAIStrategy):
                model_version = getattr(strategy, "_last_model_version", None)
                if model_version:
                    return str(model_version)
        return None
