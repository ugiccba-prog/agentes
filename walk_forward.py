"""
walk_forward.py — Parámetros validados fuera de muestra, reoptimizados 1 vez por semana.

Reemplaza la llamada a optimize_for_symbol() en cada ciclo. Idea:
  1. Se simula cada combinación (estrategia, EMAs, SL/TP) con costos reales.
  2. Se elige la mejor SOLO con el tramo de entrenamiento (60% más viejo).
  3. Se acepta únicamente si además rinde en el tramo fuera de muestra (40% más nuevo).
  4. Si no pasa, el símbolo NO se opera ("sin edge validado"). Eso también es un resultado.
  5. El resultado (aceptado o rechazado) se cachea y no se recalcula por 7 días.

Necesita ~1500+ velas por símbolo (no 400). Ajustá get_historical_data(limit=...).

Uso en live_trader.py:
    from walk_forward import get_validated_params
    df_long, source = get_historical_data(sym, interval=interval, limit=1500, use_cache=True)
    p = get_validated_params(sym, df_long)
    if p is None:
        continue   # sin edge validado, no se opera
    df_ind = compute_indicators(df_raw, ema_fast=p["ema_fast"], ema_slow=p["ema_slow"])
    df_sig = generate_signals(df_ind, strategy_name=p["strategy"])
    decision = evaluate_latest_candle(df_sig, sl_atr_mult=p["sl_atr_mult"],
                                      tp_atr_mult=p["tp_atr_mult"], lookback_bars=1)
"""
from __future__ import annotations

import itertools
import json
import math
import time
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import pandas as pd

from indicators import compute_indicators
from strategies import STRATEGY_CATALOG, generate_signals

CACHE_FILE = Path(__file__).with_name("wf_params.json")
ADAPTIVE_CONFIG_FILE = Path(__file__).with_name("adaptive_config.json")

EMA_PAIRS = [(9, 21), (12, 26), (20, 50)]
SLTP_PAIRS = [(1.2, 2.4), (1.6, 3.2), (2.0, 4.0)]


@dataclass
class WFConfig:
    train_frac: float = 0.50
    val_frac: float = 0.25
    cost_pct_per_side: float = 0.03   # spread + comisión + slippage aprox, en % del precio
    min_trades_train: int = 12
    min_trades_oos: int = 8
    min_profit_factor_oos: float = 1.15
    max_age_days: float = 7.0
    top_k_candidates: int = 5
    trail_atr_mult: float = 1.5

    @classmethod
    def from_adaptive_config(cls) -> "WFConfig":
        """Carga parámetros Walk-Forward desde adaptive_config.json['wf_config'] si existe."""
        defaults = cls()
        if not ADAPTIVE_CONFIG_FILE.exists():
            return defaults
        try:
            data = json.loads(ADAPTIVE_CONFIG_FILE.read_text(encoding="utf-8"))
            raw = data.get("wf_config", {})
            risk_raw = data.get("risk_config", {})
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            return defaults

        valid_names = {f.name for f in fields(cls)}
        kwargs: dict = {}
        for k, v in raw.items():
            if k in valid_names and v is not None:
                default_val = getattr(defaults, k)
                kwargs[k] = int(v) if isinstance(default_val, int) else float(v)
        if "trail_atr_mult" not in kwargs and isinstance(risk_raw, dict) and risk_raw.get("trail_atr_mult") is not None:
            kwargs["trail_atr_mult"] = float(risk_raw["trail_atr_mult"])
        return cls(**kwargs)


# ---------------------------------------------------------------- simulación
def simulate(
    df: pd.DataFrame,
    sl_mult: float,
    tp_mult: float,
    cost_pct: float,
    trail_atr_mult: float = 1.5,
) -> list[float]:
    """
    Devuelve los resultados de cada trade en múltiplos de R, netos de costos.
    Señal en el cierre de la vela i -> entrada en la APERTURA de i+1 (sin look-ahead).
    Si SL y TP caen en la misma vela, se asume SL primero (conservador).
    Aplica breakeven en +1R y trailing stop de trail_atr_mult*ATR desde +2R.
    Una posición a la vez por símbolo.
    """
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    sig = df["signal"].to_numpy(int)
    n = len(df)

    results: list[float] = []
    i = 31
    while i < n - 1:
        s = sig[i]
        if s == 0 or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        entry = o[i + 1]
        risk = sl_mult * atr[i]
        sl = entry - s * risk
        tp = entry + s * tp_mult * atr[i]
        cost_r = (2 * cost_pct / 100.0 * entry) / risk
        r = None
        j = i + 1
        while j < n:
            hit_sl = lo[j] <= sl if s == 1 else h[j] >= sl
            hit_tp = h[j] >= tp if s == 1 else lo[j] <= tp
            if hit_sl:
                r = (sl - entry) / risk if s == 1 else (entry - sl) / risk
                break
            if hit_tp:
                r = tp_mult / sl_mult
                break
            fav = max(h[j] - entry, c[j] - entry) if s == 1 else max(entry - lo[j], entry - c[j])
            if fav >= risk:
                sl = max(sl, entry) if s == 1 else min(sl, entry)
            if fav >= 2.0 * risk and np.isfinite(atr[j]) and atr[j] > 0:
                tr = (c[j] - trail_atr_mult * atr[j]) if s == 1 else (c[j] + trail_atr_mult * atr[j])
                sl = max(sl, tr) if s == 1 else min(sl, tr)
            j += 1
        if r is None:  # no cerró antes de terminar la data: se ignora
            break
        results.append(r - cost_r)
        i = j + 1
    return results


def metrics(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "expectancy": 0.0, "pf": 0.0, "win_rate": 0.0, "max_dd_r": 0.0}
    arr = np.array(rs)
    wins = arr[arr > 0].sum()
    losses = -arr[arr < 0].sum()
    eq = np.cumsum(arr)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    return {
        "n": n,
        "expectancy": float(arr.mean()),
        "pf": float(wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0),
        "win_rate": float((arr > 0).mean()),
        "max_dd_r": dd,
    }


# ---------------------------------------------------------------- walk-forward
WF_SCHEMA_VERSION = 2  # v2: train / validación / test intocable (sin sesgo de selección en el test)


def _score(m: dict) -> float:
    return m["expectancy"] * math.sqrt(m["n"])


def walk_forward_select(df_raw: pd.DataFrame, cfg: WFConfig | None = None) -> dict | None:
    """
    Tres tramos cronológicos:
      TRAIN (50%)  -> se generan candidatos (las 63 combinaciones) y se quedan los top-k.
      VALID (25%)  -> entre esos top-k se elige UNO (acá sí hay selección).
      TEST  (25%)  -> el elegido se confirma en datos que nunca participaron de ninguna elección.
    Se acepta solo si: el test no pierde (exp > 0) y el tramo fuera de muestra completo
    (validación + test) supera los mínimos de trades, expectancy y profit factor.
    """
    cfg = cfg or WFConfig.from_adaptive_config()
    n = len(df_raw)
    if n < 800:
        return None
    s_train = int(n * cfg.train_frac)
    s_val = int(n * (cfg.train_frac + cfg.val_frac))

    candidates: list[tuple[float, dict, dict]] = []
    for (ef, es) in EMA_PAIRS:
        df_ind = compute_indicators(df_raw, ema_fast=ef, ema_slow=es)
        for strat in STRATEGY_CATALOG:
            df_sig = generate_signals(df_ind, strategy_name=strat)
            train = df_sig.iloc[:s_train]
            for (slm, tpm) in SLTP_PAIRS:
                m = metrics(simulate(train, slm, tpm, cfg.cost_pct_per_side, cfg.trail_atr_mult))
                if m["n"] < cfg.min_trades_train or m["expectancy"] <= 0 or m["pf"] < 1.10:
                    continue
                candidates.append(
                    (_score(m), dict(strategy=strat, ema_fast=ef, ema_slow=es, sl_atr_mult=slm, tp_atr_mult=tpm), m)
                )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)

    # Validación: elegir un único candidato. Indicadores calculados sobre toda la serie (EMAs "calientes"),
    # pero cada tramo se simula por separado.
    chosen = None
    for _, params, train_m in candidates[: max(1, cfg.top_k_candidates)]:
        df_ind = compute_indicators(df_raw, ema_fast=params["ema_fast"], ema_slow=params["ema_slow"])
        df_sig = generate_signals(df_ind, strategy_name=params["strategy"])
        sim = lambda part: simulate(part, params["sl_atr_mult"], params["tp_atr_mult"],  # noqa: E731
                                    cfg.cost_pct_per_side, cfg.trail_atr_mult)
        val_rs = sim(df_sig.iloc[s_train:s_val])
        val_m = metrics(val_rs)
        if val_m["n"] < 3 or val_m["expectancy"] <= 0:
            continue
        if chosen is None or _score(val_m) > _score(chosen[2]):
            chosen = (params, train_m, val_m, val_rs, df_sig)
    if chosen is None:
        return None

    params, train_m, val_m, val_rs, df_sig = chosen
    test_rs = simulate(df_sig.iloc[s_val:], params["sl_atr_mult"], params["tp_atr_mult"],
                       cfg.cost_pct_per_side, cfg.trail_atr_mult)
    test_m = metrics(test_rs)
    oos_m = metrics(val_rs + test_rs)  # todo lo que no es train

    if test_m["n"] < 2 or test_m["expectancy"] <= 0:
        return None
    if oos_m["n"] < cfg.min_trades_oos or oos_m["expectancy"] <= 0 or oos_m["pf"] < cfg.min_profit_factor_oos:
        return None
    return {**params, "train": train_m, "val": val_m, "test": test_m, "oos": oos_m, "schema": WF_SCHEMA_VERSION}


# ---------------------------------------------------------------- cache semanal
def _load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_validated_params(symbol: str, df_long: pd.DataFrame, force: bool = False,
                         cfg: WFConfig | None = None) -> dict | None:
    """Devuelve parámetros validados OOS, o None si el símbolo no tiene edge validado."""
    cfg = cfg or WFConfig.from_adaptive_config()
    cache = _load()
    entry = cache.get(symbol)
    fresh = entry and (time.time() - entry["ts"]) < cfg.max_age_days * 86400
    if fresh and not force and entry.get("schema") == WF_SCHEMA_VERSION:
        return entry["params"]  # puede ser None (rechazado): no se recalcula por 7 días

    params = walk_forward_select(df_long, cfg)
    cache[symbol] = {"ts": time.time(), "schema": WF_SCHEMA_VERSION, "bars": len(df_long), "params": params}
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    if params is None:
        print(f"  🚫 {symbol}: sin edge validado fuera de muestra, no se opera esta semana")
    else:
        o = params["oos"]
        print(f"  ✅ {symbol}: {params['strategy']} | OOS n={o['n']} exp={o['expectancy']:+.2f}R "
              f"PF={o['pf']:.2f} win={o['win_rate']:.0%}")
    return params
