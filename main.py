"""
Crypto Agent - 智能加密货币量化交易机器人
主入口

配置说明：
- 所有配置通过.env文件管理
- 策略通过ENABLED_STRATEGIES配置，可插拔
- 支持单策略/投票两种模式
"""
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger

from config import settings
from core.orchestrator import Orchestrator
from core.state_store import StateStore
from agents import MarketAgent, RiskAgent, ExecutorAgent, LoggerAgent
from agents.strategy_agent import StrategyAgent
from web.app import create_app
from utils.logger import setup_logger


class CryptoAgentSystem:
    """系统主类"""
    
    def __init__(self):
        self.orchestrator = Orchestrator()
        self.state_store = StateStore()
        self.app = None
        self._shutdown = asyncio.Event()
    
    def setup(self):
        """初始化"""
        logger.info("初始化Agent...")
        
        agents = [
            LoggerAgent(),
            MarketAgent(),
            StrategyAgent(),
            RiskAgent(),
            ExecutorAgent(),
        ]
        
        for agent in agents:
            self.orchestrator.register_agent(agent)
        
        self.app = create_app(self.orchestrator)
        
        logger.info(f"已注册 {len(agents)} 个Agent")
    
    async def start(self):
        """启动"""
        enabled = settings.get_enabled_strategies()
        
        logger.info("=" * 50)
        logger.info("🚀 Crypto Agent 启动")
        logger.info("=" * 50)
        logger.info(f"交易对: {settings.trading_symbol}")
        logger.info(f"周期: {settings.trading_timeframe}")
        logger.info(f"分析间隔: {settings.trading_interval}秒")
        logger.info(f"每次交易: {settings.trading_amount}张")
        logger.info(f"杠杆: {settings.trading_leverage}x")
        logger.info(f"策略: {enabled}")
        logger.info(f"模式: {settings.strategy_mode}")
        logger.info(f"测试模式: {'是' if settings.test_mode else '否(实盘)'}")
        logger.info("-" * 50)
        
        if not settings.test_mode:
            logger.warning("⚠️ 实盘模式！")
        
        await self.orchestrator.start()
        await self._shutdown.wait()
    
    async def stop(self):
        """停止"""
        logger.info("停止系统...")
        await self.orchestrator.stop()
        logger.info("系统已停止")
    
    def signal_handler(self):
        self._shutdown.set()


async def run_server(app, host: str, port: int):
    """运行Web服务"""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    setup_logger(log_level="INFO", log_file="crypto_agent.log")
    
    system = CryptoAgentSystem()
    
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, system.signal_handler)
        except NotImplementedError:
            pass
    
    try:
        system.setup()
        await asyncio.gather(
            system.start(),
            run_server(system.app, settings.web_host, settings.web_port)
        )
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"系统错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await system.stop()


if __name__ == "__main__":
    strategies = settings.get_enabled_strategies()
    
    print(f"""
╔══════════════════════════════════════════════════════╗
║  🤖 Crypto Agent - 智能量化交易机器人                 ║
╠══════════════════════════════════════════════════════╣
║  交易对: {settings.trading_symbol:<20}              ║
║  策略:   {', '.join(strategies):<20}              ║
║  间隔:   {settings.trading_interval}秒                                     ║
║  监控:   http://localhost:{settings.web_port}                      ║
╚══════════════════════════════════════════════════════╝
    """)
    
    asyncio.run(main())
