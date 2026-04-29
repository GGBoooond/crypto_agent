"""OKX交易所实现"""
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import ccxt.async_support as ccxt
from loguru import logger

from .base_exchange import BaseExchange, OrderResult
from .okx_client_pool import OKXClientPool
from config import settings


class OKXExchange(BaseExchange):
    """
    OKX交易所适配器
    使用ccxt异步接口
    """
    
    def __init__(
        self,
        api_key: str = None,
        secret_key: str = None,
        passphrase: str = None,
        test_mode: bool = False
    ):
        super().__init__(
            api_key=api_key or settings.okx_api_key,
            secret_key=secret_key or settings.okx_secret_key,
            passphrase=passphrase or settings.okx_passphrase
        )
        self.test_mode = test_mode or settings.test_mode
        self.account_type = settings.okx_account_type
        self.exchange: Optional[ccxt.okx] = None
        self._pool = OKXClientPool()
        self._broker_id = 'f1ee03b510d5SUDE'  # broker tag

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """安全转换为 float，处理 None 等异常值"""
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_status(status: Any, filled: float, remaining: float) -> str:
        """补齐订单状态"""
        if status:
            return str(status)
        if remaining == 0 and filled > 0:
            return "closed"
        if remaining > 0:
            return "open"
        return "open"
    
    async def initialize(self) -> bool:
        """初始化交易所连接"""
        try:
            if self._initialized and self.exchange is not None:
                return True

            self.exchange = await self._pool.acquire(
                self.api_key,
                self.secret_key,
                self.passphrase
            )
            
            # 测试连接
            if not self.test_mode:
                await self.exchange.load_markets()
                balance = await self.fetch_balance()
                logger.info(f"OKX连接成功, USDT余额: {balance.get('USDT', 0):.2f}")
            else:
                logger.info("OKX测试模式初始化完成")
            
            self._initialized = True
            return True
            
        except Exception as e:
            if self.exchange is not None:
                await self._pool.release(
                    self.api_key,
                    self.secret_key,
                    self.passphrase
                )
                self.exchange = None
            logger.error(f"OKX初始化失败: {e}")
            return False
    
    async def fetch_balance(self) -> Dict[str, float]:
        """获取账户余额"""
        if self.test_mode:
            return {'USDT': 10000.0, 'BTC': 0.0}
        
        try:
            balance = await self.exchange.fetch_balance({'type': self.account_type})
            return {
                currency: float(data.get('free', 0))
                for currency, data in balance.items()
                if isinstance(data, dict) and float(data.get('free', 0)) > 0
            }
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return {}
    
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """获取最新行情"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {
                'symbol': symbol,
                'last': ticker['last'],
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'high': ticker['high'],
                'low': ticker['low'],
                'volume': ticker['baseVolume'],
                'change': ticker['percentage'],
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            return {}
    
    async def fetch_ohlcv(
        self, 
        symbol: str, 
        timeframe: str, 
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取K线数据"""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            result = []
            for candle in ohlcv:
                result.append({
                    'timestamp': datetime.fromtimestamp(candle[0] / 1000),
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': float(candle[5])
                })
            
            return result
            
        except Exception as e:
            logger.error(f"获取K线失败: {e}")
            return []
    
    async def fetch_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        """获取持仓"""
        if self.test_mode:
            return []
        
        try:
            symbols = [symbol] if symbol else None
            positions = await self.exchange.fetch_positions(symbols)
            
            result = []
            for pos in positions:
                contracts = float(pos['contracts']) if pos['contracts'] else 0
                if contracts > 0:
                    result.append({
                        'symbol': pos['symbol'],
                        'side': pos['side'],
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': int(pos['leverage']) if pos['leverage'] else 1,
                        'liquidation_price': float(pos['liquidationPrice']) if pos.get('liquidationPrice') else None,
                        'margin_mode': pos.get('marginMode', 'cross')
                    })
            
            return result
            
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False
    ) -> OrderResult:
        """创建市价单"""
        if self.test_mode:
            logger.info(f"[测试模式] 市价{side} {amount} {symbol}")
            return OrderResult(
                order_id=f"test_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                symbol=symbol,
                side=side,
                amount=amount,
                price=0,
                status='closed',
                filled=amount,
                timestamp=datetime.now()
            )
        
        try:
            # 根据方向确定持仓方向（双向持仓模式需要）
            pos_side = 'long' if side == 'buy' else 'short'
            
            params = {
                'tag': self._broker_id,
                'tdMode': 'cross',  # 全仓模式
                'posSide': pos_side,  # 持仓方向
                'ccy': 'USDT',  # 保证金币种
            }
            if reduce_only:
                params['reduceOnly'] = True
                # 平仓时方向相反
                params['posSide'] = 'short' if side == 'buy' else 'long'
            
            order = await self.exchange.create_market_order(
                symbol, side, amount, params=params
            )

            filled = self._to_float(order.get('filled'), 0)
            remaining = self._to_float(order.get('remaining'), 0)
            status = self._normalize_status(order.get('status'), filled, remaining)
            
            return OrderResult(
                order_id=order['id'],
                symbol=symbol,
                side=side,
                amount=amount,
                price=self._to_float(order.get('average') or order.get('price'), 0),
                status=status,
                filled=filled,
                remaining=remaining,
                fee=self._to_float(order.get('fee', {}).get('cost') if order.get('fee') else None, 0),
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"创建市价单失败: {e}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=side,
                amount=amount,
                price=0,
                status='failed',
                timestamp=datetime.now()
            )
    
    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False
    ) -> OrderResult:
        """创建限价单"""
        if self.test_mode:
            logger.info(f"[测试模式] 限价{side} {amount} {symbol} @ {price}")
            return OrderResult(
                order_id=f"test_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                status='open',
                timestamp=datetime.now()
            )
        
        try:
            # 根据方向确定持仓方向（双向持仓模式需要）
            pos_side = 'long' if side == 'buy' else 'short'
            
            params = {
                'tag': self._broker_id,
                'tdMode': 'cross',  # 全仓模式
                'posSide': pos_side,  # 持仓方向
                'ccy': 'USDT',  # 保证金币种
            }
            if reduce_only:
                params['reduceOnly'] = True
                # 平仓时方向相反
                params['posSide'] = 'short' if side == 'buy' else 'long'
            
            order = await self.exchange.create_limit_order(
                symbol, side, amount, price, params=params
            )

            filled = self._to_float(order.get('filled'), 0)
            remaining = self._to_float(order.get('remaining'), 0)
            status = self._normalize_status(order.get('status'), filled, remaining)
            
            return OrderResult(
                order_id=order['id'],
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                status=status,
                filled=filled,
                remaining=remaining,
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"创建限价单失败: {e}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                status='failed',
                timestamp=datetime.now()
            )
    
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        if self.test_mode:
            logger.info(f"[测试模式] 取消订单 {order_id}")
            return True
        
        try:
            await self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return False
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆"""
        if self.test_mode:
            logger.info(f"[测试模式] 设置杠杆 {leverage}x")
            return True
        
        try:
            await self.exchange.set_leverage(
                leverage, symbol,
                {'mgnMode': 'cross'}
            )
            logger.info(f"设置{symbol}杠杆为{leverage}x")
            return True
        except Exception as e:
            logger.error(f"设置杠杆失败: {e}")
            return False
    
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """获取订单详情"""
        if self.test_mode:
            return {'id': order_id, 'status': 'closed'}
        
        try:
            order = await self.exchange.fetch_order(order_id, symbol)
            return order
        except Exception as e:
            logger.error(f"获取订单详情失败: {e}")
            return {}
    
    async def create_algo_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        trigger_price: float,
        order_type: str = 'conditional',  # 'conditional' for stop-loss/take-profit
        reduce_only: bool = True,
        algo_type: str = 'tp'  # 'tp' = 止盈, 'sl' = 止损
    ) -> Dict[str, Any]:
        """
        创建条件单 (止盈/止损)
        OKX Algo Order API: 当价格触及 trigger_price 时，以市价执行
        
        Args:
            symbol: 交易对
            side: 'buy' or 'sell'
            amount: 数量
            trigger_price: 触发价格
            order_type: 'conditional' (条件单)
            reduce_only: 是否只减仓
            algo_type: 'tp' 止盈单, 'sl' 止损单
        """
        if self.test_mode:
            order_id = f"algo_test_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            logger.info(f"[测试模式] 条件单 {algo_type.upper()} {side} {amount} {symbol} @ trigger={trigger_price}")
            return {'order_id': order_id, 'status': 'live'}
        
        try:
            # 转换 symbol 格式: DOGE/USDT:USDT -> DOGE-USDT-SWAP
            inst_id = symbol.replace('/', '-').replace(':USDT', '-SWAP')
            
            # OKX 条件单参数
            # 平多用 sell, posSide=long; 平空用 buy, posSide=short
            pos_side = 'long' if side == 'sell' else 'short'
            
            # OKX 使用不同的参数名区分止盈/止损
            # 止盈: tpTriggerPx + tpOrdPx
            # 止损: slTriggerPx + slOrdPx
            request_params = {
                'instId': inst_id,
                'tdMode': 'cross',
                'side': side,
                'posSide': pos_side,
                'ordType': 'conditional',  # 条件单类型
                'sz': str(amount),
                'reduceOnly': 'true' if reduce_only else 'false',
            }
            
            # 根据 algo_type 设置不同的触发价参数
            if algo_type == 'tp':
                request_params['tpTriggerPx'] = str(trigger_price)
                request_params['tpOrdPx'] = '-1'  # -1 表示市价成交
                request_params['tpTriggerPxType'] = 'last'  # 使用最新价触发
            else:  # sl
                request_params['slTriggerPx'] = str(trigger_price)
                request_params['slOrdPx'] = '-1'  # -1 表示市价成交
                request_params['slTriggerPxType'] = 'last'  # 使用最新价触发
            
            logger.debug(f"条件单参数: {request_params}")
            
            # 使用 CCXT 的 request 方法直接调用 OKX REST API
            response = await self.exchange.request(
                'trade/order-algo',
                api='private',
                method='POST',
                params=request_params
            )
            
            if response.get('code') == '0':
                data = response.get('data', [{}])[0]
                order_id = data.get('algoId', '')
                logger.info(f"条件单创建成功: {order_id} | {algo_type.upper()} {side} @ {trigger_price}")
                return {'order_id': order_id, 'status': 'live'}
            else:
                error_msg = response.get('data', [{}])[0].get('sMsg', response.get('msg', ''))
                logger.error(f"条件单创建失败: {error_msg}")
                return {'order_id': '', 'status': 'failed', 'error': error_msg}
                
        except Exception as e:
            logger.error(f"创建条件单异常: {e}")
            import traceback
            traceback.print_exc()
            return {'order_id': '', 'status': 'failed', 'error': str(e)}

    async def cancel_algo_order(self, algo_order_id: str, symbol: str) -> bool:
        """取消条件单"""
        if self.test_mode:
            logger.info(f"[测试模式] 取消条件单 {algo_order_id}")
            return True
        
        if not algo_order_id:
            logger.warning("取消条件单失败: 订单ID为空")
            return False
        
        try:
            inst_id = symbol.replace('/', '-').replace(':USDT', '-SWAP')
            
            # OKX 取消条件单需要传递一个数组
            cancel_params = [{
                'algoId': algo_order_id,
                'instId': inst_id,
            }]
            
            response = await self.exchange.request(
                'trade/cancel-algos',
                api='private',
                method='POST',
                params=cancel_params
            )
            
            if response.get('code') == '0':
                logger.info(f"条件单已取消: {algo_order_id}")
                return True
            else:
                error_msg = response.get('msg', 'Unknown error')
                # 如果订单已经不存在，也视为成功
                if '51400' in str(response) or '51401' in str(response):
                    logger.info(f"条件单已不存在(可能已触发): {algo_order_id}")
                    return True
                logger.error(f"取消条件单失败: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"取消条件单异常: {e}")
            return False

    async def cancel_all_algo_orders(self, symbol: str) -> bool:
        """取消某个交易对的所有条件单"""
        if self.test_mode:
            logger.info(f"[测试模式] 取消 {symbol} 所有条件单")
            return True
        
        try:
            # 先获取所有未成交的条件单
            inst_id = symbol.replace('/', '-').replace(':USDT', '-SWAP')
            params = {
                'instType': 'SWAP',
                'instId': inst_id,
                'ordType': 'conditional',
            }
            
            response = await self.exchange.request(
                'trade/orders-algo-pending',
                api='private',
                method='GET',
                params=params
            )
            
            if response.get('code') != '0':
                logger.error(f"获取待处理条件单失败: {response.get('msg')}")
                return False
            
            orders = response.get('data', [])
            if not orders:
                logger.debug(f"{symbol} 没有待取消的条件单")
                return True
            
            # 逐个取消
            for order in orders:
                algo_id = order.get('algoId')
                if algo_id:
                    await self.cancel_algo_order(algo_id, symbol)
            
            logger.info(f"已取消 {len(orders)} 个条件单")
            return True
            
        except Exception as e:
            logger.error(f"取消所有条件单异常: {e}")
            return False

    async def close(self):
        """关闭连接"""
        if self.exchange:
            await self._pool.release(
                self.api_key,
                self.secret_key,
                self.passphrase
            )
            self.exchange = None
            self._initialized = False
            logger.info("OKX连接已关闭")
