"""
行情Agent - 负责获取市场数据
特性：
- 可配置的数据获取间隔
- 自动重连机制
- 心跳检测
"""
import asyncio
from typing import Optional
from datetime import datetime
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType
from exchange import OKXExchange
from config import settings


class MarketAgent(BaseAgent):
    """
    行情Agent - 市场数据获取和广播
    """
    
    def __init__(self):
        super().__init__(name="MarketAgent")
        self.exchange: Optional[OKXExchange] = None
        self.symbol = settings.trading_symbol
        self.timeframe = settings.trading_timeframe
        self.interval = settings.trading_interval  # 数据获取间隔
        
        self._fetch_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # 统计
        self._fetch_count = 0
        self._error_count = 0
        self._consecutive_errors = 0
        self._last_success: Optional[datetime] = None
    
    async def on_start(self):
        """启动"""
        await self._init_exchange()
        
        self._fetch_task = asyncio.create_task(self._fetch_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        await self.log("INFO", f"行情Agent启动 | {self.symbol} | 间隔:{self.interval}秒")
    
    async def _init_exchange(self) -> bool:
        """初始化交易所"""
        try:
            if self.exchange:
                try:
                    await self.exchange.close()
                except:
                    pass
            
            self.exchange = OKXExchange()
            if not await self.exchange.initialize():
                await self.log("ERROR", "交易所初始化失败")
                return False
            
            await self.exchange.set_leverage(self.symbol, settings.trading_leverage)
            
            balance = await self.exchange.fetch_balance()
            await self.state_store.update_balance(balance)
            
            self._consecutive_errors = 0
            await self.log("INFO", f"交易所连接成功 | 杠杆:{settings.trading_leverage}x")
            return True
            
        except Exception as e:
            await self.log("ERROR", f"初始化异常: {e}")
            return False
    
    async def _reconnect(self) -> bool:
        """重连"""
        for i in range(settings.reconnect_attempts):
            await self.log("WARNING", f"尝试重连 ({i+1}/{settings.reconnect_attempts})")
            await asyncio.sleep(settings.reconnect_delay * (i + 1))
            if await self._init_exchange():
                return True
        return False
    
    async def on_stop(self):
        """停止"""
        for task in [self._fetch_task, self._heartbeat_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        if self.exchange:
            await self.exchange.close()
        
        await self.log("INFO", f"行情Agent停止 | 获取:{self._fetch_count} 错误:{self._error_count}")
    
    async def _fetch_loop(self):
        """数据获取循环"""
        await self._fetch_and_broadcast()  # 首次立即执行
        
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                await self._fetch_and_broadcast()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                self._consecutive_errors += 1
                await self.log("ERROR", f"获取数据异常: {e}")
                
                if self._consecutive_errors >= 5:
                    if not await self._reconnect():
                        await self.log("ERROR", "重连失败，等待下次尝试")
                    self._consecutive_errors = 0
                else:
                    await asyncio.sleep(5)
    
    async def _heartbeat_loop(self):
        """心跳检测"""
        while self._running:
            try:
                await asyncio.sleep(settings.heartbeat_interval)
                
                if self._last_success:
                    elapsed = (datetime.now() - self._last_success).total_seconds()
                    if elapsed > self.interval * 3:
                        await self.log("WARNING", f"数据中断 {elapsed:.0f}秒，尝试重连")
                        await self._reconnect()
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.log("ERROR", f"心跳异常: {e}")
    
    async def _fetch_and_broadcast(self):
        """获取并广播数据"""
        try:
            self._fetch_count += 1
            
            # K线数据
            klines = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=300)
            if not klines:
                self._consecutive_errors += 1
                return
            
            await self.state_store.update_kline(self.symbol, klines)
            
            # 行情
            ticker = await self.exchange.fetch_ticker(self.symbol)
            await self.state_store.update_market_data(self.symbol, ticker)
            
            # 持仓
            try:
                positions = await self.exchange.fetch_positions(self.symbol)
                
                # 检查持仓变化：从有持仓变为无持仓（可能是止盈/止损触发）
                had_position = self.symbol in self.state_store.positions
                
                if positions:
                    from core.state_store import Position
                    pos = positions[0]
                    await self.state_store.update_position(Position(
                        symbol=pos['symbol'],
                        side=pos['side'],
                        size=pos['size'],
                        entry_price=pos['entry_price'],
                        leverage=pos['leverage'],
                        unrealized_pnl=pos['unrealized_pnl']
                    ))
                else:
                    # 如果之前有持仓，现在没有了，说明可能止盈/止损触发
                    if had_position:
                        await self.log("INFO", "检测到持仓已平仓（可能是止盈/止损触发）")
                        # 广播持仓平仓事件，让 ExecutorAgent 清理残余条件单
                        await self.emit(MessageType.POSITION_CLOSED, {
                            'symbol': self.symbol,
                            'reason': 'TP/SL triggered or manual close',
                            'cleanup_algo_orders': True
                        })
                    await self.state_store.update_position(None, symbol=self.symbol)
            except Exception as e:
                logger.debug(f"获取持仓异常: {e}")
            
            # 余额
            try:
                balance = await self.exchange.fetch_balance()
                await self.state_store.update_balance(balance)
            except Exception as e:
                logger.debug(f"获取余额异常: {e}")
            
            # 广播
            await self.emit(MessageType.MARKET_DATA, {
                'symbol': self.symbol,
                'klines': klines,
                'ticker': ticker,
                'timestamp': datetime.now().isoformat()
            })
            
            self._last_success = datetime.now()
            self._consecutive_errors = 0
            
            price = klines[-1]['close'] if klines else 0
            await self.log("DEBUG", f"📈 {self.symbol} @ {price:.6f}")
            
        except Exception as e:
            self._error_count += 1
            self._consecutive_errors += 1
            await self.log("ERROR", f"获取数据失败: {e}")
    
    async def handle_message(self, message: Message):
        if message.msg_type == MessageType.SYSTEM_STOP:
            await self.stop()
    
    def get_stats(self) -> dict:
        """获取统计"""
        return {
            'fetch_count': self._fetch_count,
            'error_count': self._error_count,
            'last_success': self._last_success.isoformat() if self._last_success else None
        }
