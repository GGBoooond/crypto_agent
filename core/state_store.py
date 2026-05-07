"""共享状态存储"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque
from .state import MarketState, PositionState, StatsState


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str  # 'long' or 'short'
    size: float
    entry_price: float
    leverage: int
    unrealized_pnl: float = 0.0
    liquidation_price: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    # 止盈止损订单 ID
    tp_order_id: Optional[str] = None  # Take Profit
    sl_order_id: Optional[str] = None  # Stop Loss
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'side': self.side,
            'size': self.size,
            'entry_price': self.entry_price,
            'leverage': self.leverage,
            'unrealized_pnl': self.unrealized_pnl,
            'liquidation_price': self.liquidation_price,
            'timestamp': self.timestamp.isoformat(),
            'tp_order_id': self.tp_order_id,
            'sl_order_id': self.sl_order_id,
            'tp_price': self.tp_price,
            'sl_price': self.sl_price
        }


@dataclass
class Trade:
    """交易记录"""
    trade_id: str
    symbol: str
    side: str
    amount: float
    price: float
    pnl: float = 0.0
    fee: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'trade_id': self.trade_id,
            'symbol': self.symbol,
            'side': self.side,
            'amount': self.amount,
            'price': self.price,
            'pnl': self.pnl,
            'fee': self.fee,
            'timestamp': self.timestamp.isoformat()
        }


class StateStore:
    """
    共享状态存储
    线程安全的状态管理，所有Agent共享
    """
    
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # 账户信息
        self.balance: Dict[str, float] = {}
        self.initial_balance: float = 0.0
        self.market_state = MarketState()
        self.position_state = PositionState()
        self.stats_state = StatsState()
        
        # 持仓信息
        self.positions: Dict[str, Position] = {}
        
        # 当前市场数据
        self.market_data: Dict[str, Any] = {}
        self.kline_data: Dict[str, List[Dict]] = {}
        
        # 交易历史
        self.trades: deque = deque(maxlen=1000)
        
        # 信号历史
        self.signals: deque = deque(maxlen=100)
        
        # AI分析事件历史
        self.ai_events: deque = deque(maxlen=50)
        
        # 日志历史
        self.logs: deque = deque(maxlen=500)
        
        # 系统状态
        self.system_status: str = "stopped"  # stopped, running, paused, error
        self.agent_status: Dict[str, str] = {}
        
        # 风控状态
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.trading_enabled: bool = True
        
        # 统计数据
        self.stats: Dict[str, Any] = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'win_rate': 0.0
        }
        
        self._initialized = True
    
    async def update_balance(self, balance: Dict[str, float]):
        """更新账户余额"""
        async with self._lock:
            self.balance = balance
            if self.initial_balance == 0 and 'USDT' in balance:
                self.initial_balance = balance['USDT']
    
    async def update_position(self, position: Optional[Position], symbol: Optional[str] = None):
        """
        更新持仓。
        - position 有值：更新或新增该 symbol 的持仓，同时保留已有的 tp/sl 订单信息。
        - position 为 None + symbol 有值：仅删除指定 symbol 的持仓。
        - position 为 None + symbol 为 None：兜底清空所有持仓（不推荐，仅向后兼容）。
        """
        async with self._lock:
            if position:
                symbol = position.symbol
                # 如果已有持仓，保留止盈止损订单信息
                if symbol in self.positions:
                    existing = self.positions[symbol]
                    # 只有当新的 position 没有这些字段时才保留旧的
                    if position.tp_order_id is None and existing.tp_order_id:
                        position.tp_order_id = existing.tp_order_id
                    if position.sl_order_id is None and existing.sl_order_id:
                        position.sl_order_id = existing.sl_order_id
                    if position.tp_price is None and existing.tp_price:
                        position.tp_price = existing.tp_price
                    if position.sl_price is None and existing.sl_price:
                        position.sl_price = existing.sl_price
                self.positions[symbol] = position
                self.position_state.save(position)
            elif symbol:
                # 仅清除指定 symbol 的持仓，不影响其他 symbol
                self.positions.pop(symbol, None)
                self.position_state.save(None, symbol=symbol)
            else:
                # 兜底：清空全部持仓（向后兼容，正常不应走到这里）
                self.positions = {}
                self.position_state.save(None)
    
    async def add_trade(self, trade: Trade):
        """添加交易记录"""
        async with self._lock:
            self.trades.append(trade)
            self.stats['total_trades'] += 1
            self.stats_state.total_trades = self.stats['total_trades']
            self.stats['total_pnl'] += trade.pnl
            self.stats_state.total_pnl = self.stats['total_pnl']
            
            if trade.pnl > 0:
                self.stats['winning_trades'] += 1
                self.stats_state.winning_trades = self.stats['winning_trades']
                self.consecutive_losses = 0
            elif trade.pnl < 0:
                self.stats['losing_trades'] += 1
                self.stats_state.losing_trades = self.stats['losing_trades']
                self.consecutive_losses += 1
            
            # 更新胜率
            if self.stats['total_trades'] > 0:
                self.stats['win_rate'] = (
                    self.stats['winning_trades'] / self.stats['total_trades']
                )
                self.stats_state.win_rate = self.stats['win_rate']
            
            # 更新日盈亏
            self.daily_pnl += trade.pnl
    
    async def add_signal(self, signal: Dict[str, Any]):
        """添加信号记录"""
        async with self._lock:
            self.signals.append(signal)

    async def add_ai_event(self, event: Dict[str, Any]):
        """添加AI分析事件"""
        async with self._lock:
            self.ai_events.append(event)
    
    async def add_log(self, log: Dict[str, Any]):
        """添加日志"""
        async with self._lock:
            self.logs.append(log)
    
    async def update_market_data(self, symbol: str, data: Dict[str, Any]):
        """更新市场数据"""
        async with self._lock:
            self.market_data[symbol] = data
            self.market_state.update_market_data(symbol, data)
    
    async def update_kline(self, symbol: str, klines: List[Dict]):
        """更新K线数据"""
        async with self._lock:
            self.kline_data[symbol] = klines
            self.market_state.update_kline(symbol, klines)
    
    async def set_system_status(self, status: str):
        """设置系统状态"""
        async with self._lock:
            self.system_status = status
    
    async def set_agent_status(self, agent_name: str, status: str):
        """设置Agent状态"""
        async with self._lock:
            self.agent_status[agent_name] = status
    
    async def check_trading_enabled(self) -> bool:
        """检查是否允许交易"""
        async with self._lock:
            return self.trading_enabled
    
    async def disable_trading(self, reason: str):
        """禁用交易"""
        async with self._lock:
            self.trading_enabled = False
            self.logs.append({
                'level': 'WARNING',
                'message': f'交易已禁用: {reason}',
                'timestamp': datetime.now().isoformat()
            })
    
    async def enable_trading(self):
        """启用交易"""
        async with self._lock:
            self.trading_enabled = True
    
    async def reset_daily_stats(self):
        """重置每日统计"""
        async with self._lock:
            self.daily_pnl = 0.0
    
    def get_snapshot(self) -> Dict[str, Any]:
        """获取状态快照（用于Web展示）"""
        return {
            'balance': self.balance,
            'positions': {k: v.to_dict() for k, v in self.positions.items()},
            'market_data': self.market_data,
            'recent_trades': [t.to_dict() for t in list(self.trades)[-20:]],
            'recent_signals': list(self.signals)[-20:],
            'recent_ai_events': list(self.ai_events)[-20:],
            'recent_logs': list(self.logs)[-50:],
            'system_status': self.system_status,
            'agent_status': self.agent_status,
            'trading_enabled': self.trading_enabled,
            'daily_pnl': self.daily_pnl,
            'stats': self.stats
        }
