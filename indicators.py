"""
Motor de Indicadores Técnicos Vectorizados (Pandas / NumPy).
Incluye:
- Medias Móviles Exponenciales (EMA rápida, EMA lenta, EMA 100, EMA 200)
- RSI (Relative Strength Index de Wilder)
- MACD (Línea MACD, Línea de Señal e Histograma)
- Bandas de Bollinger (Superior, Media, Inferior, %B y Ancho de Banda)
- ATR (Average True Range) para Stop Loss y Take Profit dinámicos según volatilidad
- ADX (+DI, -DI, ADX) para detectar fuerza real de tendencia vs mercado lateral
- Volumen Relativo (vol_ratio) y Canales de Donchian (rupturas institucionales)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_indicators(
    df: pd.DataFrame,
    ema_fast: int = 12,
    ema_slow: int = 26,
    ema_trend: int = 100,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    adx_period: int = 14,
) -> pd.DataFrame:
    """Calcula todos los indicadores técnicos sobre un DataFrame OHLCV y devuelve una copia enriquecida."""
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"] if "volume" in out.columns else pd.Series(0.0, index=out.index)

    # 1. Medias Móviles (EMA rápida, lenta, tendencia 100 y macro 200)
    out["ema_fast"] = close.ewm(span=ema_fast, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=ema_slow, adjust=False).mean()
    out["ema_trend"] = close.ewm(span=ema_trend, adjust=False).mean()
    out["ema_200"] = close.ewm(span=200, adjust=False).mean()

    # 2. RSI (Suavizado de Wilder)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # 3. MACD (Moving Average Convergence Divergence)
    ema_m_fast = close.ewm(span=macd_fast, adjust=False).mean()
    ema_m_slow = close.ewm(span=macd_slow, adjust=False).mean()
    out["macd"] = ema_m_fast - ema_m_slow
    out["macd_signal"] = out["macd"].ewm(span=macd_signal, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    # 4. Bandas de Bollinger
    bb_mid = close.rolling(window=bb_period).mean()
    bb_dev = close.rolling(window=bb_period).std(ddof=0)
    out["bb_mid"] = bb_mid
    out["bb_upper"] = bb_mid + bb_std * bb_dev
    out["bb_lower"] = bb_mid - bb_std * bb_dev
    band_range = (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["bb_pct_b"] = ((close - out["bb_lower"]) / band_range).fillna(0.5)
    out["bb_width"] = (band_range / bb_mid.replace(0, np.nan)).fillna(0.0)

    # 5. ATR (Average True Range) para Stop Loss / Take Profit adaptativo
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    out["atr"] = true_range.ewm(alpha=1.0 / atr_period, min_periods=atr_period, adjust=False).mean().bfill()

    # 6. ADX (+DI, -DI, ADX) — Detector de régimen (Tendencia fuerte vs Rango)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr_smooth = true_range.ewm(alpha=1.0 / adx_period, min_periods=adx_period, adjust=False).mean().replace(0, np.nan)
    plus_di = 100.0 * pd.Series(plus_dm, index=out.index).ewm(alpha=1.0 / adx_period, min_periods=adx_period, adjust=False).mean() / atr_smooth
    minus_di = 100.0 * pd.Series(minus_dm, index=out.index).ewm(alpha=1.0 / adx_period, min_periods=adx_period, adjust=False).mean() / atr_smooth
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0.0)
    out["plus_di"] = plus_di.fillna(0.0)
    out["minus_di"] = minus_di.fillna(0.0)
    out["adx"] = dx.ewm(alpha=1.0 / adx_period, min_periods=adx_period, adjust=False).mean().fillna(20.0)

    # 7. Volumen Relativo (Volumen actual vs media de 20 velas) y Canales de Donchian
    vol_sma = volume.rolling(window=20).mean().replace(0, np.nan)
    out["vol_ratio"] = (volume / vol_sma).fillna(1.0)
    out["donchian_high"] = high.rolling(window=20).max().shift(1).bfill()
    out["donchian_low"] = low.rolling(window=20).min().shift(1).bfill()

    # 8. Pendiente normalizada de tendencia (Fuerza direccional)
    out["trend_strength"] = ((out["ema_fast"] - out["ema_slow"]).abs() / out["atr"].replace(0, np.nan)).fillna(0.0)

    return out
