"""Simulador conservador SOLO para investigación.

Señal al cierre i, entrada apertura i+1. Stop/TP activos durante vela;
trailing se actualiza únicamente al CIERRE de la vela para la siguiente.
En gap adverso, fill al open; si stop y target coinciden, stop primero.
"""
from __future__ import annotations
import math
import numpy as np


def simulate(df, sl_mult: float, tp_mult: float, cost_pct: float,
             trail_atr_mult: float = 1.5) -> list[float]:
    if sl_mult <= 0 or tp_mult <= 0 or cost_pct < 0:
        raise ValueError("Parámetros de simulación inválidos")
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    signal = df["signal"].to_numpy(int)
    n = len(df)
    results = []
    i = 200  # calentamiento: evita ATR inicial completado retrospectivamente
    while i < n - 1:
        side = signal[i]
        if side not in (-1, 1) or not math.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        entry = o[i + 1]
        risk = sl_mult * atr[i]
        if not math.isfinite(entry) or entry <= 0 or risk <= 0:
            i += 1
            continue
        sl = entry - side * risk
        tp = entry + side * tp_mult * atr[i]
        if sl <= 0 and side == 1:
            i += 1
            continue
        exit_price = None
        j = i + 1
        while j < n:
            # El stop fijado antes de la vela se ejecuta primero, incluyendo gaps.
            if side == 1:
                if o[j] <= sl:
                    exit_price = o[j]
                elif low[j] <= sl:
                    exit_price = sl
                elif o[j] >= tp:
                    exit_price = tp  # conservador: no regalar gap favorable
                elif h[j] >= tp:
                    exit_price = tp
            else:
                if o[j] >= sl:
                    exit_price = o[j]
                elif h[j] >= sl:
                    exit_price = sl
                elif o[j] <= tp:
                    exit_price = tp
                elif low[j] <= tp:
                    exit_price = tp
            if exit_price is not None:
                break
            # Solo después de comprobar fills, usar el cierre para el stop FUTURO.
            favorable_close = side * (c[j] - entry)
            if favorable_close >= risk:
                sl = max(sl, entry) if side == 1 else min(sl, entry)
            if favorable_close >= 2 * risk and math.isfinite(atr[j]) and atr[j] > 0:
                trail = c[j] - side * trail_atr_mult * atr[j]
                sl = max(sl, trail) if side == 1 else min(sl, trail)
            j += 1
        if exit_price is None:
            break  # operación abierta no se cuenta como ganadora
        gross_r = side * (exit_price - entry) / risk
        # Comisión y spread aproximados en ambos extremos.
        fees_r = (cost_pct / 100.0) * (entry + exit_price) / risk
        results.append(float(gross_r - fees_r))
        i = j + 1
    return results
