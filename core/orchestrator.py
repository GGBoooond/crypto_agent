"""协调器 - 管理所有Agent的生命周期和消息传递"""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger

from .base_agent import BaseAgent, AgentState
from .message import Message, MessageType
from .state_store import StateStore


class Orchestrator:
    """
    协调器
    负责管理所有Agent的生命周期、消息路由和系统协调
    """
    
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.state_store = StateStore()
        self._running = False
        self._message_history: List[Message] = []
        
    def register_agent(self, agent: BaseAgent):
        """注册Agent"""
        agent.orchestrator = self
        self.agents[agent.name] = agent
        logger.info(f"已注册Agent: {agent.name}")
    
    def unregister_agent(self, name: str):
        """注销Agent"""
        if name in self.agents:
            del self.agents[name]
            logger.info(f"已注销Agent: {name}")
    
    async def start(self):
        """启动所有Agent"""
        self._running = True
        await self.state_store.set_system_status("running")
        
        logger.info("=" * 50)
        logger.info("Crypto Agent 系统启动中...")
        logger.info("=" * 50)
        
        # 按顺序启动Agent
        start_order = [
            'LoggerAgent',
            'MarketAgent', 
            'StrategyAgent',
            'RiskAgent',
            'ExecutorAgent'
        ]
        
        for name in start_order:
            if name in self.agents:
                await self.agents[name].start()
                await asyncio.sleep(0.1)  # 短暂延迟确保顺序
        
        # 启动其他未在列表中的Agent
        for name, agent in self.agents.items():
            if name not in start_order:
                await agent.start()
        
        # 发送系统启动消息
        await self.dispatch(Message(
            msg_type=MessageType.SYSTEM_START,
            sender="Orchestrator",
            data={'timestamp': datetime.now().isoformat()}
        ))
        
        logger.info("所有Agent已启动")
    
    async def stop(self):
        """停止所有Agent"""
        logger.info("正在停止系统...")
        
        # 发送系统停止消息
        await self.dispatch(Message(
            msg_type=MessageType.SYSTEM_STOP,
            sender="Orchestrator",
            data={'timestamp': datetime.now().isoformat()}
        ))
        
        await asyncio.sleep(0.5)
        
        # 逆序停止Agent
        stop_order = list(self.agents.keys())[::-1]
        for name in stop_order:
            await self.agents[name].stop()
        
        self._running = False
        await self.state_store.set_system_status("stopped")
        logger.info("系统已停止")
    
    async def dispatch(self, message: Message):
        """
        分发消息
        如果target为None，则广播给所有Agent
        否则只发送给目标Agent
        """
        # 记录消息历史
        self._message_history.append(message)
        if len(self._message_history) > 1000:
            self._message_history = self._message_history[-500:]
        
        if message.target:
            # 定向发送
            if message.target in self.agents:
                await self.agents[message.target].receive(message)
        else:
            # 广播（排除发送者）
            for name, agent in self.agents.items():
                if name != message.sender:
                    await agent.receive(message)
    
    async def send_to(self, target: str, msg_type: MessageType, data=None, sender: str = "Orchestrator"):
        """发送消息给特定Agent"""
        message = Message(
            msg_type=msg_type,
            sender=sender,
            data=data,
            target=target
        )
        await self.dispatch(message)
    
    async def broadcast(self, msg_type: MessageType, data=None, sender: str = "Orchestrator"):
        """广播消息给所有Agent"""
        message = Message(
            msg_type=msg_type,
            sender=sender,
            data=data
        )
        await self.dispatch(message)
    
    def get_agent(self, name: str) -> Optional[BaseAgent]:
        """获取Agent"""
        return self.agents.get(name)
    
    def get_agent_states(self) -> Dict[str, str]:
        """获取所有Agent状态"""
        return {
            name: agent.state.value 
            for name, agent in self.agents.items()
        }
    
    async def health_check(self) -> Dict[str, any]:
        """健康检查"""
        return {
            'running': self._running,
            'agents': self.get_agent_states(),
            'message_count': len(self._message_history),
            'timestamp': datetime.now().isoformat()
        }
