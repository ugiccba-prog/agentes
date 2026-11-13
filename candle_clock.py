"""
candle_clock.py — Evaluar señales UNA vez por vela cerrada, no cada 60 s.

Los stops/TP/trailing siguen revisándose cada ciclo (eso depende del precio).
Las señales nuevas solo se evalúan cuando cierra una vela nueva.

Uso en live_trader.py (modo --loop):

    from candle_clock import NewCandleGate, closed_only
    gate = NewCandleGate(interval)

    while True:
        # 1) siempre: gestionar posiciones abiertas con precio fresco
        broker.update_open_positions(atr_by_symbol=atr_by_symbol)
        # 2) solo si cerró una vela nueva: buscar entradas
        if gate.is_new_closed_candle():
            execute_live_cycle(broker, ...)   # y dentro, df_raw = closed_only(df_raw, interval)
        time.sleep(args.sleep)
"""
from __future__ import annotations

import time

import pandas as pd

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def _bucket(interval: str, now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // INTERVAL_SECONDS[interval])


class NewCandleGate:
    """Devuelve True una sola vez por cada vela que se cierra (pequeña gracia para que el feed actualice)."""

    def __init__(self, interval: str, grace_seconds: int = 5) -> None:
        self.interval = interval
        self.grace = grace_seconds
        self.last_bucket = _bucket(interval)  # arranca "ya procesada": espera al próximo cierre

    def is_new_closed_candle(self) -> bool:
        b = _bucket(self.interval, time.time() - self.grace)
        if b != self.last_bucket:
            self.last_bucket = b
            return True
        return False

    def seconds_to_next_close(self) -> float:
        step = INTERVAL_SECONDS[self.interval]
        return step - (time.time() % step) + self.grace


def closed_only(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Descarta la última fila si todavía es una vela en formación.
    Soporta índice datetime o una columna 'timestamp'/'time'/'datetime' (UTC).
    Si no encuentra marca de tiempo, devuelve df sin tocar: verificalo a mano.
    """
    if df is None or df.empty:
        return df
    step = pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    if isinstance(df.index, pd.DatetimeIndex):
        ts = df.index
    else:
        col = next((c for c in ("timestamp", "time", "datetime", "date") if c in df.columns), None)
        if col is None:
            return df
        ts = pd.DatetimeIndex(pd.to_datetime(df[col], utc=True))
    last = ts[-1]
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    if last + step > pd.Timestamp.now(tz="UTC"):
        return df.iloc[:-1]
    return df
