"""Market regime detection."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class MarketRegime(str, Enum):
    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    RANGING = "ranging"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"


@dataclass
class RegimeMetrics:
    """Numeric inputs that drove the regime decision (for prompt explanation)."""

    change_pct: float
    volatility: float
    sample_size: int
    atr_rank: float = 0.0
    adx: float = 0.0
    bb_width_rank: float = 0.0
    volume_price_corr: float = 0.0


class RegimeTagger:
    """Multi-factor regime detector using volatility, trend and volume context."""

    def detect(self, klines: List[Dict[str, Any]]) -> MarketRegime:
        regime, _ = self.detect_with_metrics(klines)
        return regime

    def detect_with_metrics(
        self,
        klines: List[Dict[str, Any]],
        indicators_df: Optional[pd.DataFrame] = None,
    ) -> Tuple[MarketRegime, RegimeMetrics]:
        if len(klines) < 20:
            return MarketRegime.RANGING, RegimeMetrics(0.0, 0.0, len(klines))

        df = self._build_dataframe(klines, indicators_df)
        metrics = self._build_metrics(df)
        trend_score = self._trend_score(metrics)

        if metrics.atr_rank >= 0.9 and metrics.bb_width_rank >= 0.8:
            return MarketRegime.HIGH_VOLATILITY, metrics
        if trend_score >= 2:
            return MarketRegime.STRONG_TREND_UP, metrics
        if trend_score == 1:
            return MarketRegime.WEAK_TREND_UP, metrics
        if trend_score <= -2:
            return MarketRegime.STRONG_TREND_DOWN, metrics
        if trend_score == -1:
            return MarketRegime.WEAK_TREND_DOWN, metrics
        return MarketRegime.RANGING, metrics

    def _build_dataframe(
        self,
        klines: List[Dict[str, Any]],
        indicators_df: Optional[pd.DataFrame],
    ) -> pd.DataFrame:
        df = pd.DataFrame(klines).copy()
        df[["open", "high", "low", "close", "volume"]] = df[
            ["open", "high", "low", "close", "volume"]
        ].astype(float)
        if indicators_df is not None:
            for column in ("atr", "adx", "bb_width"):
                if column in indicators_df.columns:
                    df[column] = indicators_df[column].values[-len(df):]
        return self._ensure_indicator_columns(df)

    def _ensure_indicator_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if "atr" not in df.columns:
            df["atr"] = self._atr(df)
        if "adx" not in df.columns:
            df["adx"] = self._adx(df)
        if "bb_width" not in df.columns:
            ma20 = df["close"].rolling(20).mean()
            std = df["close"].rolling(20).std()
            df["bb_width"] = (std * 4) / ma20.replace(0, np.nan)
        return df

    def _build_metrics(self, df: pd.DataFrame) -> RegimeMetrics:
        window = df.tail(min(80, len(df)))
        closes = window["close"].tail(20)
        first = float(closes.iloc[0])
        last = float(closes.iloc[-1])
        returns = closes.pct_change().abs().dropna() * 100
        change_pct = ((last - first) / first * 100) if first else 0.0
        return RegimeMetrics(
            change_pct=round(change_pct, 3),
            volatility=round(float(returns.mean() or 0.0), 3),
            sample_size=len(closes),
            atr_rank=round(self._rank_latest(window["atr"]), 3),
            adx=round(float(window["adx"].iloc[-1] or 0.0), 3),
            bb_width_rank=round(self._rank_latest(window["bb_width"]), 3),
            volume_price_corr=round(self._volume_price_corr(window), 3),
        )

    @staticmethod
    def _trend_score(metrics: RegimeMetrics) -> int:
        direction = 1 if metrics.change_pct > 0 else -1 if metrics.change_pct < 0 else 0
        if direction == 0:
            return 0
        score = 0
        if abs(metrics.change_pct) >= 4:
            score += 2
        elif abs(metrics.change_pct) >= 1:
            score += 1
        if metrics.adx >= 25:
            score += 1
        if abs(metrics.volume_price_corr) >= 0.35:
            score += 1 if metrics.volume_price_corr * direction > 0 else -1
        if metrics.bb_width_rank <= 0.2 and metrics.adx < 20:
            score -= 1
        return score * direction

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def _adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        atr = self._atr(df, period).replace(0, np.nan)
        up_move = df["high"] - df["high"].shift()
        down_move = df["low"].shift() - df["low"]
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=df.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=df.index,
        )
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).fillna(0)
        return dx.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def _rank_latest(series: pd.Series) -> float:
        clean = series.dropna()
        if clean.empty:
            return 0.0
        latest = float(clean.iloc[-1])
        return float((clean <= latest).sum() / len(clean))

    @staticmethod
    def _volume_price_corr(window: pd.DataFrame) -> float:
        returns = window["close"].pct_change()
        volume_change = window["volume"].pct_change()
        corr = returns.corr(volume_change)
        if corr is None or np.isnan(corr):
            return 0.0
        return float(corr)
