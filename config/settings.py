"""
全局配置管理 - 可插拔配置系统
支持通过环境变量和配置文件灵活配置
"""
import os
from typing import Optional, List, Dict, Any
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    """全局配置类 - 所有配置集中管理"""
    
    # ==================== 交易所配置 ====================
    okx_api_key: str = Field(default="", alias="OKX_API_KEY")
    okx_secret_key: str = Field(default="", alias="OKX_SECRET_KEY")
    okx_passphrase: str = Field(default="", alias="OKX_PASSPHRASE")
    okx_account_type: str = Field(default="swap", alias="OKX_ACCOUNT_TYPE")
    
    # ==================== AI配置 ====================
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    ai_model: str = Field(default="deepseek-chat", alias="AI_MODEL")
    ai_temperature: float = Field(default=0.3, alias="AI_TEMPERATURE")
    llm_daily_token_limit: int = Field(default=200000, alias="LLM_DAILY_TOKEN_LIMIT")
    llm_per_call_token_limit: int = Field(default=4000, alias="LLM_PER_CALL_TOKEN_LIMIT")
    
    # ==================== 交易配置 ====================
    trading_symbol: str = Field(default="DOGE/USDT:USDT", alias="TRADING_SYMBOL")
    trading_amount: float = Field(default=100, alias="TRADING_AMOUNT")  # 张数
    trading_leverage: int = Field(default=5, alias="TRADING_LEVERAGE")
    trading_timeframe: str = Field(default="1m", alias="TRADING_TIMEFRAME")
    trading_interval: int = Field(default=120, alias="TRADING_INTERVAL")  # 分析间隔(秒)
    test_mode: bool = Field(default=True, alias="TEST_MODE")
    
    # ==================== 策略配置 ====================
    # 启用的策略列表(逗号分隔): ai_scalping,technical,trend
    enabled_strategies: str = Field(default="ai_scalping", alias="ENABLED_STRATEGIES")
    # 策略模式: single(单策略) / voting(投票)
    strategy_mode: str = Field(default="single", alias="STRATEGY_MODE")
    # 投票阈值(仅voting模式)
    vote_threshold: float = Field(default=0.4, alias="VOTE_THRESHOLD")
    
    # ==================== AI剥头皮策略专属配置 ====================
    scalping_min_profit_percent: float = Field(default=0.3, alias="SCALPING_MIN_PROFIT")  # 最小盈利%
    scalping_max_loss_percent: float = Field(default=0.5, alias="SCALPING_MAX_LOSS")  # 最大亏损%
    scalping_hold_minutes: int = Field(default=10, alias="SCALPING_HOLD_MINUTES")  # 最长持仓分钟
    
    # ==================== 风控配置 ====================
    max_position_ratio: float = Field(default=0.3, alias="MAX_POSITION_RATIO")
    stop_loss_ratio: float = Field(default=0.02, alias="STOP_LOSS_RATIO")
    daily_stop_loss_ratio: float = Field(default=0.05, alias="DAILY_STOP_LOSS_RATIO")
    max_leverage: int = Field(default=10, alias="MAX_LEVERAGE")
    max_consecutive_losses: int = Field(default=5, alias="MAX_CONSECUTIVE_LOSSES")
    max_daily_trades: int = Field(default=50, alias="MAX_DAILY_TRADES")  # 每日最大交易次数
    
    # ==================== Web配置 ====================
    web_host: str = Field(default="0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(default=8888, alias="WEB_PORT")
    
    # ==================== 稳定性配置 ====================
    reconnect_attempts: int = Field(default=5, alias="RECONNECT_ATTEMPTS")
    reconnect_delay: int = Field(default=5, alias="RECONNECT_DELAY")
    heartbeat_interval: int = Field(default=30, alias="HEARTBEAT_INTERVAL")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
    
    def get_enabled_strategies(self) -> List[str]:
        """获取启用的策略列表"""
        return [s.strip() for s in self.enabled_strategies.split(",") if s.strip()]
    
    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        """获取特定策略的配置"""
        configs = {
            "ai_scalping": {
                "min_profit": self.scalping_min_profit_percent,
                "max_loss": self.scalping_max_loss_percent,
                "hold_minutes": self.scalping_hold_minutes,
            },
            "technical": {
                "rsi_period": 14,
                "macd_fast": 12,
                "macd_slow": 26,
            },
            "trend": {
                "ema_periods": [5, 10, 20],
            }
        }
        return configs.get(strategy_name, {})


# 全局配置实例
settings = Settings()
