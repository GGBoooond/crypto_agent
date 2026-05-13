"""Backtest engine main loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from config import settings
from core.message import Signal, SignalType
from exchange import OKXExchange
from harness.verification.policy_gate import PolicyGate, as_verification_result
from harness.verification.sanity import SanityVerifier
from harness.verification.schema import parse_signal_dict
from strategies import StrategyFactory, TechnicalStrategy
from strategies.base_ai_strategy import BaseAIStrategy

from .data_provider import HistoricalDataProvider
from .replayer import LLMReplayer
from .reporter import BacktestReporter
from .stub_exchange import BacktestExchange, MarketConstraints
from .stub_state_store import BacktestStateStore


@dataclass
class BacktestConfig:
    strategy_name: str
    start: datetime
    end: datetime
    symbol: str
    llm_mode: str = "replay"
    initial_balance: float = 10000.0
    timeframe: str = "1m"
    output_dir: str = "reports"
    fidelity_check: bool = False


@dataclass
class BacktestResult:
    run_id: str
    config: Dict[str, Any]
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    metrics_by_regime: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProgressEvent:
    run_id: str
    index: int
    total: int
    progress: float
    timestamp: str
    equity: float
    trades: int


class BacktestEngine:
    """Kline-driven backtest engine."""

    def __init__(self):
        self.sanity_verifier = SanityVerifier()
        self.policy_gate = PolicyGate()
        self.reporter = BacktestReporter()

    async def run(
        self,
        config: BacktestConfig,
        on_progress: Optional[Callable[[ProgressEvent], Awaitable[None]]] = None,
        run_id: Optional[str] = None,
    ) -> BacktestResult:
        exchange = OKXExchange(test_mode=True)
        await exchange.initialize()
        data_provider = HistoricalDataProvider(exchange=exchange)
        klines = await data_provider.fetch_klines(
            symbol=config.symbol,
            timeframe=config.timeframe,
            start=config.start,
            end=config.end,
        )
        if len(klines) < 20:
            raise RuntimeError("not enough klines for backtest")

        state_store = BacktestStateStore(initial_balance=config.initial_balance)
        simulator = BacktestExchange(
            state_store=state_store, constraints=MarketConstraints(), fee_taker=0.0005
        )
        await simulator.initialize()

        strategy = StrategyFactory.create(config.strategy_name)
        if strategy is None:
            raise RuntimeError(f"unknown strategy: {config.strategy_name}")
        fallback_technical = TechnicalStrategy(weight=1.0)
        llm_replayer = LLMReplayer()

        result = BacktestResult(
            run_id=run_id or uuid4().hex[:12],
            config={
                "strategy_name": config.strategy_name,
                "start": config.start.isoformat(),
                "end": config.end.isoformat(),
                "symbol": config.symbol,
                "llm_mode": config.llm_mode,
                "initial_balance": config.initial_balance,
                "timeframe": config.timeframe,
                "replay_hits": 0,
                "technical_fallback_hits": 0,
            },
        )

        window_size = 300
        total_steps = max(len(klines) - window_size, 1)
        for index in range(window_size, len(klines)):
            current_kline = klines[index]
            simulator.step(current_kline)
            kline_window = klines[index - window_size : index]
            ticker = {
                "last": float(current_kline["close"]),
                "high": float(current_kline["high"]),
                "low": float(current_kline["low"]),
            }
            position = state_store.positions.get(config.symbol)
            signal = await self._produce_signal(
                config=config,
                result=result,
                strategy=strategy,
                fallback_strategy=fallback_technical,
                replayer=llm_replayer,
                symbol=config.symbol,
                ts=current_kline["timestamp"],
                klines=kline_window,
                ticker=ticker,
                position=position.to_dict() if position else None,
            )
            if signal is not None:
                verified_signal = await self._verify_signal(
                    signal=signal,
                    current_price=float(current_kline["close"]),
                    state_store=state_store,
                    symbol=config.symbol,
                )
                if verified_signal is not None:
                    await self._simulate_execute(verified_signal, simulator, state_store)
            result.equity_curve.append(
                self._build_equity_point(current_kline["timestamp"], state_store, ticker["last"], config.symbol)
            )
            if on_progress and ((index - window_size) % 100 == 0 or index == len(klines) - 1):
                completed_steps = (index - window_size) + 1
                progress = completed_steps / total_steps
                await on_progress(
                    ProgressEvent(
                        run_id=result.run_id,
                        index=completed_steps,
                        total=total_steps,
                        progress=progress,
                        timestamp=current_kline["timestamp"].isoformat(),
                        equity=result.equity_curve[-1]["equity"],
                        trades=len(state_store.trades),
                    )
                )

        if state_store.positions.get(config.symbol):
            final_price = float(klines[-1]["close"])
            close_side = "sell" if state_store.positions[config.symbol].side == "long" else "buy"
            await simulator.create_market_order(
                symbol=config.symbol,
                side=close_side,
                amount=state_store.positions[config.symbol].size,
                reduce_only=True,
            )
            simulator.step(klines[-1])

        result.trades = [trade.to_dict() for trade in state_store.trades]
        self.reporter.build(state_store, result)
        self.reporter.save(config.output_dir, result)
        await exchange.close()
        return result

    async def _produce_signal(
        self,
        config: BacktestConfig,
        result: BacktestResult,
        strategy,
        fallback_strategy: TechnicalStrategy,
        replayer: LLMReplayer,
        symbol: str,
        ts: datetime,
        klines: List[Dict[str, Any]],
        ticker: Dict[str, Any],
        position: Optional[Dict[str, Any]],
    ) -> Optional[Signal]:
        if config.llm_mode == "replay" and isinstance(strategy, BaseAIStrategy):
            replay = replayer.find(symbol, ts)
            replay_signal = self._signal_from_replay(replay)
            if replay_signal is not None:
                result.config["replay_hits"] += 1
                return replay_signal
            result.config["technical_fallback_hits"] += 1
            return await fallback_strategy.analyze(symbol, klines, ticker, position)
        return await strategy.analyze(symbol, klines, ticker, position)

    async def _verify_signal(
        self,
        signal: Signal,
        current_price: float,
        state_store: BacktestStateStore,
        symbol: str,
    ) -> Optional[Signal]:
        schema_result = parse_signal_dict(signal.to_dict())
        if not schema_result.passed or not schema_result.signal:
            return None
        sanity_result = self.sanity_verifier.verify(
            schema_result.signal, current_price, market_snapshot={}
        )
        if not sanity_result.passed or not sanity_result.signal:
            return None
        policy_result = await self.policy_gate.check_signal(
            sanity_result.signal,
            current_price,
            state_store.balance,
            state_store.positions.get(symbol),
        )
        verification = as_verification_result(policy_result)
        return verification.signal if verification.passed else None

    async def _simulate_execute(
        self,
        signal: Signal,
        exchange: BacktestExchange,
        state_store: BacktestStateStore,
    ) -> None:
        symbol = signal.symbol
        amount = float(signal.amount or settings.trading_amount)
        position = state_store.positions.get(symbol)
        if signal.signal_type == SignalType.BUY:
            if position and position.side == "short":
                await exchange.create_market_order(symbol, "buy", position.size, reduce_only=True)
            await exchange.create_market_order(symbol, "buy", amount)
            return
        if signal.signal_type == SignalType.SELL:
            if position and position.side == "long":
                await exchange.create_market_order(symbol, "sell", position.size, reduce_only=True)
            await exchange.create_market_order(symbol, "sell", amount)
            return
        if signal.signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT) and position:
            close_side = "sell" if position.side == "long" else "buy"
            await exchange.create_market_order(symbol, close_side, position.size, reduce_only=True)

    @staticmethod
    def _signal_from_replay(replay: Optional[Dict[str, Any]]) -> Optional[Signal]:
        if not replay:
            return None
        payload = replay.get("llm_response")
        if not isinstance(payload, dict):
            return None
        required = {"signal_type", "symbol", "confidence", "reason"}
        if not required.issubset(payload.keys()):
            return None
        try:
            return Signal.from_dict(payload)
        except Exception:
            return None

    @staticmethod
    def _build_equity_point(
        ts: datetime,
        state_store: BacktestStateStore,
        current_price: float,
        symbol: str,
    ) -> Dict[str, Any]:
        equity = float(state_store.balance.get("USDT", 0.0))
        position = state_store.positions.get(symbol)
        if position:
            if position.side == "long":
                equity += (current_price - position.entry_price) * position.size
            else:
                equity += (position.entry_price - current_price) * position.size
        return {"timestamp": ts.isoformat(), "equity": equity}
