"""Agent基类"""
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime
from typing import Optional, Callable, List, TYPE_CHECKING
from loguru import logger

from .message import Message, MessageType
from .state_store import StateStore

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class AgentState(Enum):
    """Agent状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class BaseAgent(ABC):
    """
    Agent基类
    所有Agent都需要继承此类并实现handle_message方法
    """
    
    def __init__(self, name: str):
        self.name = name
        self.state = AgentState.IDLE
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.orchestrator: Optional['Orchestrator'] = None
        self.state_store = StateStore()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # 消息处理器映射
        self._handlers: dict[MessageType, Callable] = {}
        
        # 初始化时注册处理器
        self._register_handlers()
    
    def _register_handlers(self):
        """注册消息处理器（子类可重写）"""
        pass
    
    def register_handler(self, msg_type: MessageType, handler: Callable):
        """注册特定类型消息的处理器"""
        self._handlers[msg_type] = handler
    
    async def start(self):
        """启动Agent"""
        if self._running:
            return
        
        self._running = True
        self.state = AgentState.RUNNING
        await self.state_store.set_agent_status(self.name, "running")
        
        logger.info(f"[{self.name}] Agent启动")
        
        # 启动初始化
        await self.on_start()
        
        # 启动消息处理循环
        self._task = asyncio.create_task(self._run_loop())
    
    async def stop(self):
        """停止Agent"""
        self._running = False
        self.state = AgentState.STOPPED
        await self.state_store.set_agent_status(self.name, "stopped")
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        await self.on_stop()
        logger.info(f"[{self.name}] Agent已停止")
    
    async def pause(self):
        """暂停Agent"""
        self.state = AgentState.PAUSED
        await self.state_store.set_agent_status(self.name, "paused")
        logger.info(f"[{self.name}] Agent已暂停")
    
    async def resume(self):
        """恢复Agent"""
        if self.state == AgentState.PAUSED:
            self.state = AgentState.RUNNING
            await self.state_store.set_agent_status(self.name, "running")
            logger.info(f"[{self.name}] Agent已恢复")
    
    async def _run_loop(self):
        """主运行循环"""
        while self._running:
            try:
                # 等待消息，设置超时以便检查运行状态
                try:
                    message = await asyncio.wait_for(
                        self.message_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if self.state == AgentState.PAUSED:
                    # 暂停状态，将消息放回队列
                    await self.message_queue.put(message)
                    await asyncio.sleep(0.5)
                    continue
                
                # 处理消息
                await self._process_message(message)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state = AgentState.ERROR
                await self.state_store.set_agent_status(self.name, "error")
                logger.error(f"[{self.name}] 处理消息时出错: {e}")
                await self.on_error(e)
    
    async def _process_message(self, message: Message):
        """处理消息"""
        try:
            # 检查是否有注册的处理器
            if message.msg_type in self._handlers:
                await self._handlers[message.msg_type](message)
            else:
                # 使用通用处理方法
                await self.handle_message(message)
        except Exception as e:
            logger.error(f"[{self.name}] 处理消息失败 [{message.msg_type}]: {e}")
            raise
    
    async def receive(self, message: Message):
        """接收消息（由Orchestrator调用）"""
        await self.message_queue.put(message)
    
    async def emit(self, msg_type: MessageType, data=None, target: str = None):
        """发送消息给其他Agent"""
        if self.orchestrator:
            message = Message(
                msg_type=msg_type,
                sender=self.name,
                data=data,
                target=target
            )
            await self.orchestrator.dispatch(message)
    
    async def log(self, level: str, message: str):
        """记录日志"""
        log_entry = {
            'level': level,
            'agent': self.name,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        await self.state_store.add_log(log_entry)
        
        # 同时输出到控制台
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(f"[{self.name}] {message}")
    
    @abstractmethod
    async def handle_message(self, message: Message):
        """
        处理消息（子类必须实现）
        """
        pass
    
    async def on_start(self):
        """启动时的初始化（子类可重写）"""
        pass
    
    async def on_stop(self):
        """停止时的清理（子类可重写）"""
        pass
    
    async def on_error(self, error: Exception):
        """错误处理（子类可重写）"""
        await self.log("ERROR", f"发生错误: {error}")
