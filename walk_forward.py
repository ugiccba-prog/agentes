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
from market_data import get_market_type
from research.simulator import simulate as _conservative_simulate
from strategies import STRATEGY_CATALOG, generate_signals

CACHE_FILE = Path(__file__).with_name("wf_params.json")
ADAPTIVE_CONFIG_FILE = Path(__file__).with_name("adaptive_config.json")

EMA_PAIRS = [(9, 21), (12, 26), (20, 50)]
SLTP_PAIRS = [(1.4, 3.5), (1.6, 4.0), (2.0, 5.0)]
WF_STRATEGIES = [s for s in STRATEGY_CATALOG if s != "momentum_shark"]


@dataclass
class WFConfig:
    train_frac: float = 0.50
    val_frac: float = 0.25
    cost_pct_per_side: float = 0.06         # Cripto: comisión real (0.05%) + slippage aprox por lado
    cost_pct_per_side_forex: float = 0.012  # Forex/Oro: 0.01% fee + 0.002% slippage por lado
    min_trades_train: int = 12
    min_trades_oos: int = 8
    min_profit_factor_oos: float = 1.15
    max_age_days: float = 7.0
    top_k_candidates: int = 5
    trail_atr_mult: float = 1.5
    require_double_cost_positive: bool = True

    def cost_for_symbol(self, symbol: str | None) -> float:
        """Devuelve el costo realista por lado según el mercado (Cripto vs Forex/Oro)."""
        if symbol and get_market_type(symbol) == "forex":
            return self.cost_pct_per_side_forex
        return self.cost_pct_per_side

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
                if isinstance(default_val, bool):
                    kwargs[k] = bool(v)
                elif isinstance(default_val, int):
                    kwargs[k] = int(v)
                else:
                    kwargs[k] = float(v)
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
    """Delega al simulador conservador sin look-ahead (stops al cierre, fill al open en gaps)."""
    return _conservative_simulate(
        df,
        sl_mult,
        tp_mult,
        cost_pct,
        trail_atr_mult=trail_atr_mult,
    )


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
WF_SCHEMA_VERSION = 4  # v4: costos diferenciados Cripto (0.06%) vs Forex/Oro (0.012%) + doble costo OOS


def _score(m: dict) -> float:
    return m["expectancy"] * math.sqrt(m["n"])


def walk_forward_select(
    df_raw: pd.DataFrame,
    cfg: WFConfig | None = None,
    symbol: str | None = None,
) -> dict | None:
    """
    Tres tramos cronológicos:
      TRAIN (50%)  -> se generan candidatos y se quedan los top-k.
      VALID (25%)  -> entre esos top-k se elige UNO (acá sí hay selección).
      TEST  (25%)  -> el elegido se confirma en datos que nunca participaron de ninguna elección.
    Se acepta solo si: el test no pierde (exp > 0), el tramo fuera de muestra completo
    (validación + test) supera los mínimos de trades, expectancy y profit factor,
    y (si está activo) resiste doble costo OOS.
    """
    cfg = cfg or WFConfig.from_adaptive_config()
    cost_pct = cfg.cost_for_symbol(symbol)
    n = len(df_raw)
    if n < 800:
        return None
    s_train = int(n * cfg.train_frac)
    s_val = int(n * (cfg.train_frac + cfg.val_frac))

    candidates: list[tuple[float, dict, dict]] = []
    for (ef, es) in EMA_PAIRS:
        df_ind = compute_indicators(df_raw, ema_fast=ef, ema_slow=es)
        for strat in WF_STRATEGIES:
            df_sig = generate_signals(df_ind, strategy_name=strat)
            df_sig.loc[df_sig.index[:200], "signal"] = 0
            train = df_sig.iloc[:s_train]
            for (slm, tpm) in SLTP_PAIRS:
                m = metrics(simulate(train, slm, tpm, cost_pct, cfg.trail_atr_mult))
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
        df_sig.loc[df_sig.index[:200], "signal"] = 0
        sim = lambda part: simulate(part, params["sl_atr_mult"], params["tp_atr_mult"],  # noqa: E731
                                    cost_pct, cfg.trail_atr_mult)
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
                       cost_pct, cfg.trail_atr_mult)
    test_m = metrics(test_rs)
    oos_m = metrics(val_rs + test_rs)  # todo lo que no es train

    if test_m["n"] < 2 or test_m["expectancy"] <= 0:
        return None
    if oos_m["n"] < cfg.min_trades_oos or oos_m["expectancy"] <= 0 or oos_m["pf"] < cfg.min_profit_factor_oos:
        return None

    val_rs_2x = simulate(df_sig.iloc[s_train:s_val], params["sl_atr_mult"], params["tp_atr_mult"],
                         cost_pct * 2, cfg.trail_atr_mult)
    test_rs_2x = simulate(df_sig.iloc[s_val:], params["sl_atr_mult"], params["tp_atr_mult"],
                          cost_pct * 2, cfg.trail_atr_mult)
    oos_double_m = metrics(val_rs_2x + test_rs_2x)
    if cfg.require_double_cost_positive and oos_double_m["expectancy"] <= 0:
        return None

    return {
        **params,
        "cost_pct_per_side": cost_pct,
        "train": train_m,
        "val": val_m,
        "test": test_m,
        "oos": oos_m,
        "oos_double_cost": oos_double_m,
        "schema": WF_SCHEMA_VERSION,
    }


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

    params = walk_forward_select(df_long, cfg, symbol=symbol)
    cache[symbol] = {"ts": time.time(), "schema": WF_SCHEMA_VERSION, "bars": len(df_long), "params": params}
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    if params is None:
        print(f"  🚫 {symbol}: sin edge validado fuera de muestra, no se opera esta semana")
    else:
        o = params["oos"]
        print(f"  ✅ {symbol}: {params['strategy']} | OOS n={o['n']} exp={o['expectancy']:+.2f}R "
              f"PF={o['pf']:.2f} win={o['win_rate']:.0%}")
    return params
