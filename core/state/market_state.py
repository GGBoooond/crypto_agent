"""Market state holder."""
from typing import Any, Dict, List


class MarketState:
    def __init__(self):
        self.market_data: Dict[str, Any] = {}
        self.kline_data: Dict[str, List[Dict[str, Any]]] = {}

    def update_market_data(self, symbol: str, data: Dict[str, Any]) -> None:
        self.market_data[symbol] = data

    def update_kline(self, symbol: str, klines: List[Dict[str, Any]]) -> None:
        self.kline_data[symbol] = klines

