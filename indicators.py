"""
Motor de Indicadores Técnicos Vectorizados (Pandas / NumPy).
Incluye:
- Medias Móviles Exponenciales y Simples (EMA rápida, EMA lenta, EMA de tendencia 200)
- RSI (Relative Strength Index de Wilder)
- MACD (Línea MACD, Línea de Señal e Histograma)
- Bandas de Bollinger (Superior, Media, Inferior, %B y Ancho de Banda)
- ATR (Average True Range) para Stop Loss y Take Profit dinámicos según volatilidad
- ADX / Fuerza de Tendencia para distinguir entre mercados en tendencia (Cripto) y laterales (Forex)
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
) -> pd.DataFrame:
    """Calcula todos los indicadores técnicos sobre un DataFrame OHLCV y devuelve una copia enriquecida."""
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]

    # 1. Medias Móviles (EMA rápida, lenta y filtro de tendencia)
    out["ema_fast"] = close.ewm(span=ema_fast, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=ema_slow, adjust=False).mean()
    out["ema_trend"] = close.ewm(span=ema_trend, adjust=False).mean()

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

    # 6. Pendiente normalizada de tendencia (Fuerza direccional)
    out["trend_strength"] = ((out["ema_fast"] - out["ema_slow"]).abs() / out["atr"].replace(0, np.nan)).fillna(0.0)

    return out
