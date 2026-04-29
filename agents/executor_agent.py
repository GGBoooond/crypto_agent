"""执行Agent - 负责执行交易订单"""
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType, Signal, SignalType
from core.state_store import Trade, Position
from exchange import OKXExchange
from config import settings


class ExecutorAgent(BaseAgent):
    """
    执行Agent (V4 - 止盈止损挂单版)
    
    核心变化：
    1. 开仓后自动挂止盈止损条件单
    2. 支持动态调整止盈止损单
    3. 持仓信息中保存止盈止损订单ID
    """
    
    def __init__(self):
        super().__init__(name="ExecutorAgent")
        self.exchange: Optional[OKXExchange] = None
        self._pending_orders = {}
        # 记录每个symbol的止盈止损订单ID
        self._tp_sl_orders: Dict[str, Dict[str, str]] = {}
    
    def _register_handlers(self):
        """注册消息处理器"""
        self.register_handler(MessageType.SIGNAL_APPROVED, self._on_approved_signal)
        self.register_handler(MessageType.POSITION_CLOSED, self._on_position_closed)
    
    async def _on_position_closed(self, message: Message):
        """
        处理持仓平仓事件
        当止盈/止损触发时，清理另一个未触发的条件单
        """
        try:
            data = message.data
            symbol = data.get('symbol')
            cleanup = data.get('cleanup_algo_orders', False)
            
            if cleanup and symbol and symbol in self._tp_sl_orders:
                await self.log("INFO", f"清理 {symbol} 残余条件单...")
                
                existing_orders = self._tp_sl_orders.get(symbol, {})
                
                # 尝试取消止盈单（可能已触发不存在）
                if existing_orders.get('tp_order_id'):
                    await self.exchange.cancel_algo_order(
                        existing_orders['tp_order_id'], symbol
                    )
                
                # 尝试取消止损单（可能已触发不存在）
                if existing_orders.get('sl_order_id'):
                    await self.exchange.cancel_algo_order(
                        existing_orders['sl_order_id'], symbol
                    )
                
                # 清除本地记录
                del self._tp_sl_orders[symbol]
                await self.log("INFO", f"{symbol} 条件单已清理")
                
        except Exception as e:
            await self.log("ERROR", f"清理条件单失败: {e}")
    
    async def on_start(self):
        """启动时初始化交易所连接"""
        self.exchange = OKXExchange()
        await self.exchange.initialize()
        await self.log("INFO", f"执行Agent已启动 (测试模式: {settings.test_mode})")
    
    async def on_stop(self):
        """停止时关闭连接"""
        if self.exchange:
            await self.exchange.close()
    
    async def _on_approved_signal(self, message: Message):
        """处理通过风控的信号"""
        try:
            data = message.data
            signal_data = data['signal']
            current_price = data['current_price']
            is_modified = data.get('modified', False)
            is_risk_close = data.get('risk_close', False)
            
            signal = Signal.from_dict(signal_data)
            
            await self.log(
                "INFO",
                f"收到交易信号: {signal.signal_type.value} {signal.symbol} "
                f"数量: {signal.amount} {'(已修改)' if is_modified else ''}"
            )
            
            # 执行交易
            await self._execute_signal(signal, current_price, is_risk_close)
            
        except Exception as e:
            await self.log("ERROR", f"执行交易失败: {e}")
            import traceback
            traceback.print_exc()
    
    async def _execute_signal(
        self,
        signal: Signal,
        current_price: float,
        is_risk_close: bool = False
    ):
        """
        执行交易信号 (V4 - 支持止盈止损挂单)
        """
        symbol = signal.symbol
        amount = signal.amount or settings.trading_amount
        
        # 获取当前持仓
        position = self.state_store.positions.get(symbol)
        
        try:
            # ==== 处理调整止盈止损信号 ====
            if signal.metadata.get('adjust_tp_sl'):
                await self._adjust_tp_sl_orders(symbol, signal, position)
                return
            
            # ==== 处理平仓信号 ====
            if signal.signal_type in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]:
                if position:
                    await self._close_position(position, signal.reason)
                return
            
            # ==== 处理开仓信号 ====
            if signal.signal_type == SignalType.BUY:
                # 如果有空仓，先平掉
                if position and position.side == 'short':
                    await self._close_position(position, "反向开仓，先平空")
                    await asyncio.sleep(1)
                
                # 开多仓
                await self._open_long(symbol, amount, signal)
                
            elif signal.signal_type == SignalType.SELL:
                # 如果有多仓，先平掉
                if position and position.side == 'long':
                    await self._close_position(position, "反向开仓，先平多")
                    await asyncio.sleep(1)
                
                # 开空仓
                await self._open_short(symbol, amount, signal)
            
            elif signal.signal_type == SignalType.HOLD:
                # HOLD 信号可能包含调整止盈止损的请求
                if signal.metadata.get('adjust_tp_sl'):
                    await self._adjust_tp_sl_orders(symbol, signal, position)
                else:
                    await self.log("DEBUG", "HOLD信号，不执行交易")
                
        except Exception as e:
            await self.log("ERROR", f"订单执行失败: {e}")
            import traceback
            traceback.print_exc()
            await self.emit(
                MessageType.ORDER_FAILED,
                {
                    'signal': signal.to_dict(),
                    'error': str(e)
                }
            )
    
    async def _wait_for_position(
        self,
        symbol: str,
        side: str,
        max_wait: float = 5.0,
        interval: float = 0.5
    ) -> Optional[Dict[str, Any]]:
        """
        等待持仓确认
        
        Args:
            symbol: 交易对
            side: 预期持仓方向 ('long' or 'short')
            max_wait: 最大等待时间(秒)
            interval: 轮询间隔(秒)
            
        Returns:
            持仓信息字典，如果超时则返回 None
        """
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                positions = await self.exchange.fetch_positions(symbol)
                for pos in positions:
                    if (pos.get('symbol') == symbol and 
                        pos.get('side') == side and 
                        float(pos.get('size', 0)) > 0):
                        return pos
            except Exception as e:
                await self.log("WARNING", f"查询持仓异常: {e}")
            
            await asyncio.sleep(interval)
            elapsed += interval
        
        return None

    async def _open_long(self, symbol: str, amount: float, signal: Signal):
        """开多仓 + 挂止盈止损单"""
        await self.log("INFO", f"开多仓: {amount} {symbol}")
        
        result = await self.exchange.create_market_order(
            symbol=symbol,
            side='buy',
            amount=amount
        )
        
        if result.status == 'failed':
            await self.log("ERROR", f"开多失败: {result}")
            return
        
        # 检查是否需要等待成交确认
        actual_filled = result.filled
        actual_price = result.price
        
        if actual_filled <= 0 or actual_price <= 0:
            await self.log("INFO", "订单已提交，等待成交确认...")
            # 等待持仓出现
            position = await self._wait_for_position(symbol, 'long', max_wait=5.0)
            
            if position:
                actual_filled = float(position.get('size', 0))
                actual_price = float(position.get('entry_price', 0))
                await self.log("INFO", f"持仓确认: {actual_filled} @ ${actual_price:,.4f}")
            else:
                await self.log("ERROR", "开仓超时: 未检测到持仓，跳过挂止盈止损单")
                # 仍然广播订单结果（可能部分成交）
                await self.emit(
                    MessageType.ORDER_FILLED,
                    {
                        'order': result.to_dict(),
                        'signal': signal.to_dict()
                    }
                )
                return
        else:
            await self.log(
                "INFO",
                f"开多成功: {actual_filled} @ ${actual_price:,.4f}"
            )
        
        # 挂止盈止损单 (仅 V4 策略会设置 place_tp_sl_orders=True)
        tp_price = signal.metadata.get('tp_price') or signal.take_profit
        sl_price = signal.metadata.get('sl_price') or signal.stop_loss
        
        if tp_price and sl_price and signal.metadata.get('place_tp_sl_orders', False):
            if actual_filled > 0:
                await self._place_tp_sl_orders(
                    symbol=symbol,
                    amount=actual_filled,
                    side='long',
                    tp_price=tp_price,
                    sl_price=sl_price,
                    entry_price=actual_price
                )
            else:
                await self.log("WARNING", "成交数量为0，跳过挂止盈止损单")
        
        # 广播订单结果
        await self.emit(
            MessageType.ORDER_FILLED,
            {
                'order': result.to_dict(),
                'signal': signal.to_dict()
            }
        )
    
    async def _open_short(self, symbol: str, amount: float, signal: Signal):
        """开空仓 + 挂止盈止损单"""
        await self.log("INFO", f"开空仓: {amount} {symbol}")
        
        result = await self.exchange.create_market_order(
            symbol=symbol,
            side='sell',
            amount=amount
        )
        
        if result.status == 'failed':
            await self.log("ERROR", f"开空失败: {result}")
            return
        
        # 检查是否需要等待成交确认
        actual_filled = result.filled
        actual_price = result.price
        
        if actual_filled <= 0 or actual_price <= 0:
            await self.log("INFO", "订单已提交，等待成交确认...")
            # 等待持仓出现
            position = await self._wait_for_position(symbol, 'short', max_wait=5.0)
            
            if position:
                actual_filled = float(position.get('size', 0))
                actual_price = float(position.get('entry_price', 0))
                await self.log("INFO", f"持仓确认: {actual_filled} @ ${actual_price:,.4f}")
            else:
                await self.log("ERROR", "开仓超时: 未检测到持仓，跳过挂止盈止损单")
                # 仍然广播订单结果（可能部分成交）
                await self.emit(
                    MessageType.ORDER_FILLED,
                    {
                        'order': result.to_dict(),
                        'signal': signal.to_dict()
                    }
                )
                return
        else:
            await self.log(
                "INFO",
                f"开空成功: {actual_filled} @ ${actual_price:,.4f}"
            )
        
        # 挂止盈止损单 (仅 V4 策略会设置 place_tp_sl_orders=True)
        tp_price = signal.metadata.get('tp_price') or signal.take_profit
        sl_price = signal.metadata.get('sl_price') or signal.stop_loss
        
        if tp_price and sl_price and signal.metadata.get('place_tp_sl_orders', False):
            if actual_filled > 0:
                await self._place_tp_sl_orders(
                    symbol=symbol,
                    amount=actual_filled,
                    side='short',
                    tp_price=tp_price,
                    sl_price=sl_price,
                    entry_price=actual_price
                )
            else:
                await self.log("WARNING", "成交数量为0，跳过挂止盈止损单")
        
        # 广播订单结果
        await self.emit(
            MessageType.ORDER_FILLED,
            {
                'order': result.to_dict(),
                'signal': signal.to_dict()
            }
        )
    
    async def _place_tp_sl_orders(
        self,
        symbol: str,
        amount: float,
        side: str,
        tp_price: float,
        sl_price: float,
        entry_price: float
    ):
        """
        挂止盈止损条件单
        
        Args:
            symbol: 交易对
            amount: 持仓数量
            side: 持仓方向 ('long' or 'short')
            tp_price: 止盈价格
            sl_price: 止损价格
            entry_price: 入场价格
        """
        await self.log("INFO", f"挂止盈止损单: TP={tp_price:.5f}, SL={sl_price:.5f}")
        
        # 确定平仓方向
        # 多仓用 sell 平仓，空仓用 buy 平仓
        close_side = 'sell' if side == 'long' else 'buy'
        
        tp_order_id = None
        sl_order_id = None
        
        # 1. 挂止盈单 (Take Profit)
        tp_result = await self.exchange.create_algo_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            trigger_price=tp_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='tp'  # 止盈单
        )
        
        if tp_result.get('status') == 'live':
            tp_order_id = tp_result.get('order_id')
            await self.log("INFO", f"止盈单挂单成功: {tp_order_id}")
        else:
            await self.log("ERROR", f"止盈单挂单失败: {tp_result.get('error', 'Unknown')}")
        
        # 2. 挂止损单 (Stop Loss)
        sl_result = await self.exchange.create_algo_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            trigger_price=sl_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='sl'  # 止损单
        )
        
        if sl_result.get('status') == 'live':
            sl_order_id = sl_result.get('order_id')
            await self.log("INFO", f"止损单挂单成功: {sl_order_id}")
        else:
            await self.log("ERROR", f"止损单挂单失败: {sl_result.get('error', 'Unknown')}")
        
        # 3. 记录订单ID到本地存储
        self._tp_sl_orders[symbol] = {
            'tp_order_id': tp_order_id,
            'sl_order_id': sl_order_id,
            'tp_price': tp_price,
            'sl_price': sl_price
        }
        
        # 4. 更新 StateStore 中的持仓信息
        position = self.state_store.positions.get(symbol)
        if position:
            position.tp_order_id = tp_order_id
            position.sl_order_id = sl_order_id
            position.tp_price = tp_price
            position.sl_price = sl_price
            await self.log("DEBUG", "持仓信息已更新止盈止损订单ID")

    async def _adjust_tp_sl_orders(
        self,
        symbol: str,
        signal: Signal,
        position: Optional[Any]
    ):
        """
        调整止盈止损单：撤销旧单，挂新单
        """
        if not position:
            await self.log("WARNING", f"调整止盈止损失败: 无持仓 {symbol}")
            return
        
        new_tp = signal.metadata.get('tp_price') or signal.take_profit or getattr(position, 'tp_price', None)
        new_sl = signal.metadata.get('sl_price') or signal.stop_loss or getattr(position, 'sl_price', None)
        
        if not new_tp or not new_sl:
            await self.log("WARNING", f"调整止盈止损失败: 缺少价格参数 (TP={new_tp}, SL={new_sl})")
            return
        
        # 查询真实持仓数量（确保使用最新数据）
        actual_size = position.size
        actual_side = position.side
        try:
            positions = await self.exchange.fetch_positions(symbol)
            for pos in positions:
                if (pos.get('symbol') == symbol and 
                    float(pos.get('size', 0)) > 0):
                    actual_size = float(pos.get('size', 0))
                    actual_side = pos.get('side', position.side)
                    break
        except Exception as e:
            await self.log("WARNING", f"查询持仓失败，使用缓存数据: {e}")
        
        if actual_size <= 0:
            await self.log("WARNING", "调整止盈止损失败: 持仓数量为0")
            return
        
        await self.log("INFO", f"调整止盈止损: 新TP={new_tp:.5f}, 新SL={new_sl:.5f}, 数量={actual_size}")
        
        # 1. 撤销现有的止盈止损单
        existing_orders = self._tp_sl_orders.get(symbol, {})
        
        if existing_orders.get('tp_order_id'):
            await self.exchange.cancel_algo_order(existing_orders['tp_order_id'], symbol)
            await self.log("DEBUG", f"已撤销旧止盈单: {existing_orders['tp_order_id']}")
        
        if existing_orders.get('sl_order_id'):
            await self.exchange.cancel_algo_order(existing_orders['sl_order_id'], symbol)
            await self.log("DEBUG", f"已撤销旧止损单: {existing_orders['sl_order_id']}")
        
        # 2. 挂新的止盈止损单
        await self._place_tp_sl_orders(
            symbol=symbol,
            amount=actual_size,
            side=actual_side,
            tp_price=new_tp,
            sl_price=new_sl,
            entry_price=position.entry_price
        )

    async def _close_position(self, position, reason: str):
        """平仓 + 取消止盈止损单"""
        await self.log("INFO", f"平仓: {position.side} {position.size} {position.symbol}")
        
        symbol = position.symbol
        
        # 1. 先取消所有止盈止损条件单
        existing_orders = self._tp_sl_orders.get(symbol, {})
        if existing_orders:
            if existing_orders.get('tp_order_id'):
                await self.exchange.cancel_algo_order(existing_orders['tp_order_id'], symbol)
                await self.log("DEBUG", f"已撤销止盈单: {existing_orders['tp_order_id']}")
            if existing_orders.get('sl_order_id'):
                await self.exchange.cancel_algo_order(existing_orders['sl_order_id'], symbol)
                await self.log("DEBUG", f"已撤销止损单: {existing_orders['sl_order_id']}")
            # 清除记录
            del self._tp_sl_orders[symbol]
        
        # 2. 市价平仓
        side = 'sell' if position.side == 'long' else 'buy'
        
        result = await self.exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=position.size,
            reduce_only=True
        )
        
        if result.status == 'failed':
            await self.log("ERROR", f"平仓失败: {result}")
            return
        
        # 计算盈亏
        if position.side == 'long':
            pnl = (result.price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - result.price) * position.size
        
        await self.log(
            "INFO",
            f"平仓成功: {position.size} @ ${result.price:,.2f} "
            f"盈亏: ${pnl:+,.2f}"
        )
        
        # 记录交易
        trade = Trade(
            trade_id=result.order_id,
            symbol=position.symbol,
            side=f"close_{position.side}",
            amount=position.size,
            price=result.price,
            pnl=pnl,
            fee=result.fee
        )
        await self.state_store.add_trade(trade)
        
        # 广播平仓结果
        await self.emit(
            MessageType.POSITION_CLOSED,
            {
                'position': position.to_dict(),
                'close_price': result.price,
                'pnl': pnl,
                'reason': reason
            }
        )
    
    async def handle_message(self, message: Message):
        """处理其他消息"""
        if message.msg_type == MessageType.SYSTEM_STOP:
            await self.stop()
