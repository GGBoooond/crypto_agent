"""FastAPI Web应用"""
import asyncio
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from loguru import logger

from core.state_store import StateStore
from core.orchestrator import Orchestrator
from config import settings
from web.api.backtest import register_backtest_routes
from evolution.skill_lifecycle import SkillLifecycleManager
from memory.skill_manage import SkillManager


class ConnectionManager:
    """WebSocket连接管理器"""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket连接断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """广播消息给所有连接"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        for conn in disconnected:
            self.disconnect(conn)


def create_app(orchestrator: Optional[Orchestrator] = None) -> FastAPI:
    """创建FastAPI应用"""
    
    app = FastAPI(
        title="Crypto Agent Dashboard",
        description="智能加密货币量化交易机器人监控面板",
        version="1.0.0"
    )
    
    # 存储全局对象
    app.state.orchestrator = orchestrator
    app.state.state_store = StateStore()
    app.state.ws_manager = ConnectionManager()
    
    # 静态文件
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
    backtest_static_path = static_path / "backtest"
    if backtest_static_path.exists():
        app.mount("/backtest", StaticFiles(directory=str(backtest_static_path), html=True), name="backtest")
    
    @app.get("/", response_class=HTMLResponse)
    async def index():
        """返回主页面"""
        html_path = Path(__file__).parent / "static" / "index.html"
        if html_path.exists():
            return html_path.read_text(encoding='utf-8')
        return HTMLResponse("<h1>Crypto Agent Dashboard</h1><p>静态文件未找到</p>")
    
    @app.get("/api/status")
    async def get_status():
        """获取系统状态"""
        state_store = app.state.state_store
        orchestrator = app.state.orchestrator
        
        return JSONResponse({
            'system_status': state_store.system_status,
            'agent_status': state_store.agent_status,
            'trading_enabled': state_store.trading_enabled,
            'balance': state_store.balance,
            'daily_pnl': state_store.daily_pnl,
            'stats': state_store.stats,
            'health': await orchestrator.health_check() if orchestrator else None
        })
    
    @app.get("/api/positions")
    async def get_positions():
        """获取持仓信息"""
        state_store = app.state.state_store
        return JSONResponse({
            'positions': {k: v.to_dict() for k, v in state_store.positions.items()}
        })
    
    @app.get("/api/market")
    async def get_market():
        """获取市场数据"""
        state_store = app.state.state_store
        return JSONResponse({
            'market_data': state_store.market_data,
            'klines': {k: [
                {
                    'timestamp': kl['timestamp'].isoformat() if hasattr(kl['timestamp'], 'isoformat') else str(kl['timestamp']),
                    'open': kl['open'],
                    'high': kl['high'],
                    'low': kl['low'],
                    'close': kl['close'],
                    'volume': kl['volume']
                } for kl in v[-50:]  # 只返回最近50根K线
            ] for k, v in state_store.kline_data.items()}
        })
    
    @app.get("/api/trades")
    async def get_trades():
        """获取交易记录"""
        state_store = app.state.state_store
        return JSONResponse({
            'trades': [t.to_dict() for t in list(state_store.trades)[-50:]]
        })
    
    @app.get("/api/signals")
    async def get_signals():
        """获取信号记录"""
        state_store = app.state.state_store
        return JSONResponse({
            'signals': list(state_store.signals)[-30:]
        })
    
    @app.get("/api/logs")
    async def get_logs():
        """获取日志"""
        state_store = app.state.state_store
        return JSONResponse({
            'logs': list(state_store.logs)[-100:]
        })
    
    @app.post("/api/control/{action}")
    async def control_system(action: str):
        """控制系统"""
        orchestrator = app.state.orchestrator
        state_store = app.state.state_store
        
        if not orchestrator:
            return JSONResponse({'error': '系统未初始化'}, status_code=500)
        
        if action == 'pause':
            for agent in orchestrator.agents.values():
                await agent.pause()
            return {'status': 'paused'}
        
        elif action == 'resume':
            for agent in orchestrator.agents.values():
                await agent.resume()
            await state_store.enable_trading()
            return {'status': 'resumed'}
        
        elif action == 'enable_trading':
            await state_store.enable_trading()
            return {'status': 'trading_enabled'}
        
        elif action == 'disable_trading':
            await state_store.disable_trading('用户手动禁用')
            return {'status': 'trading_disabled'}
        
        else:
            return JSONResponse({'error': f'未知操作: {action}'}, status_code=400)

    @app.get("/admin/skill_drafts")
    async def list_skill_drafts():
        manager = SkillManager()
        return JSONResponse({"drafts": manager.list_drafts()})

    @app.post("/admin/skill_drafts/{name}/promote")
    async def promote_skill_draft(name: str):
        manager = SkillManager()
        lifecycle = SkillLifecycleManager()
        try:
            path = manager.promote_draft(name)
            lifecycle.ensure_skill(name)
        except FileNotFoundError:
            return JSONResponse({"error": f"draft not found: {name}"}, status_code=404)
        except FileExistsError:
            return JSONResponse({"error": f"skill already exists: {name}"}, status_code=409)
        return JSONResponse({"status": "promoted", "path": str(path)})

    @app.post("/admin/skill_drafts/{name}/reject")
    async def reject_skill_draft(name: str):
        manager = SkillManager()
        try:
            path = manager.reject_draft(name)
        except FileNotFoundError:
            return JSONResponse({"error": f"draft not found: {name}"}, status_code=404)
        return JSONResponse({"status": "rejected", "path": str(path)})

    @app.get("/admin/skill_lifecycle")
    async def list_skill_lifecycle():
        lifecycle = SkillLifecycleManager()
        return JSONResponse({"skills": lifecycle.to_public_payload()})
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket实时数据推送"""
        manager = app.state.ws_manager
        await manager.connect(websocket)
        
        try:
            # 启动数据推送任务
            push_task = asyncio.create_task(
                push_updates(websocket, app.state.state_store)
            )
            
            while True:
                # 接收客户端消息
                data = await websocket.receive_text()
                # 可以处理客户端请求
                
        except WebSocketDisconnect:
            manager.disconnect(websocket)
            push_task.cancel()
        except Exception as e:
            logger.error(f"WebSocket错误: {e}")
            manager.disconnect(websocket)

    register_backtest_routes(app)
    
    return app


async def push_updates(websocket: WebSocket, state_store: StateStore):
    """定期推送更新"""
    while True:
        try:
            await asyncio.sleep(2)  # 每2秒推送一次
            
            snapshot = state_store.get_snapshot()
            await websocket.send_json({
                'type': 'update',
                'data': snapshot
            })
            
        except Exception:
            break
