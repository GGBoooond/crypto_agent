"""辅助函数"""
from typing import Any, Optional
from datetime import datetime


def format_price(price: float, decimals: int = 2) -> str:
    """格式化价格"""
    return f"${price:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """格式化百分比"""
    return f"{value:+.{decimals}f}%"


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """安全转换为整数"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def timestamp_to_str(timestamp: datetime) -> str:
    """时间戳转字符串"""
    return timestamp.strftime('%Y-%m-%d %H:%M:%S')


def calculate_pnl_percent(entry_price: float, current_price: float, side: str) -> float:
    """计算盈亏百分比"""
    if entry_price == 0:
        return 0.0
    
    if side == 'long':
        return ((current_price - entry_price) / entry_price) * 100
    else:  # short
        return ((entry_price - current_price) / entry_price) * 100


def calculate_position_value(size: float, price: float, leverage: int = 1) -> float:
    """计算持仓价值"""
    return size * price


def timeframe_to_seconds(timeframe: str) -> int:
    """将时间周期转换为秒数"""
    mapping = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '30m': 1800,
        '1h': 3600,
        '4h': 14400,
        '1d': 86400,
    }
    return mapping.get(timeframe, 3600)
