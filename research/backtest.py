"""Backtest aislado: usa indicadores y simulador existentes, sin broker ni cache WF.

CSV: timestamp,open,high,low,close,volume. Velas cerradas y orden cronológico.
Salida: evidencia cuantitativa para costs y out_of_sample; las demás puertas
permanecen pendientes. NO promueve automáticamente una estrategia.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from indicators import compute_indicators
from strategies import STRATEGY_CATALOG, generate_signals
from research.simulator import simulate
from walk_forward import metrics

REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def run(csv_path: Path, strategy: str, *, ema_fast: int = 12,
        ema_slow: int = 26, sl_mult: float = 1.6, tp_mult: float = 3.2,
        cost_pct: float = 0.03) -> dict:
    if strategy not in STRATEGY_CATALOG:
        raise ValueError(f"Estrategia desconocida: {strategy}")
    if not (0 < ema_fast < ema_slow and sl_mult > 0 and tp_mult > 0 and cost_pct >= 0):
        raise ValueError("Parámetros inválidos")
    df = pd.read_csv(csv_path)
    if any(col not in df.columns for col in REQUIRED):
        raise ValueError(f"Columnas requeridas: {REQUIRED}")
    if len(df) < 800:
        raise ValueError("Se requieren al menos 800 velas")
    dates = pd.to_datetime(df["timestamp"], utc=True, errors="raise")
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():
        raise ValueError("Fechas inválidas, desordenadas o duplicadas")
    for col in REQUIRED[1:]:
        df[col] = pd.to_numeric(df[col], errors="raise")
        if not np.isfinite(df[col]).all():
            raise ValueError(f"Valores no finitos en {col}")
    if (df[["open", "high", "low", "close"]] <= 0).any().any() or (df["volume"] < 0).any():
        raise ValueError("Precio o volumen inválido")
    if ((df["high"] < df[["open", "close", "low"]].max(axis=1)) |
        (df["low"] > df[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC inconsistente")
    # Indicadores calculados cronológicamente; parámetros FIJOS, sin selección sobre test.
    df = compute_indicators(df, ema_fast=ema_fast, ema_slow=ema_slow)
    df = generate_signals(df, strategy_name=strategy)
    # Evitar que indicadores de calentamiento o rellenos retrospectivos generen operaciones.
    # EMA200 necesita ~200 velas de historia antes de evaluar señales.
    df.loc[df.index[:200], "signal"] = 0
    n = len(df)
    train_end, valid_end = int(n * 0.5), int(n * 0.75)
    parts = {"train": df.iloc[:train_end], "validation": df.iloc[train_end:valid_end],
             "test": df.iloc[valid_end:]}
    stats = {}
    for label, part in parts.items():
        rs = simulate(part, sl_mult, tp_mult, cost_pct)
        doubled = simulate(part, sl_mult, tp_mult, cost_pct * 2)
        stats[label] = {"normal": metrics(rs), "double_cost": metrics(doubled)}
    test = stats["test"]
    # Puerta cuantitativa preliminar, no equivale a Walk-Forward repetido.
    enough = test["normal"]["n"] >= 8
    positive = test["normal"]["expectancy"] > 0 and test["normal"]["pf"] > 1.15
    resilient = test["double_cost"]["expectancy"] > 0
    return {
        "strategy": strategy, "source_file": str(csv_path), "bars": n,
        "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "first_timestamp": dates.iloc[0].isoformat(),
        "last_timestamp": dates.iloc[-1].isoformat(),
        "parameters": {"ema_fast": ema_fast, "ema_slow": ema_slow,
                       "sl_mult": sl_mult, "tp_mult": tp_mult,
                       "cost_pct_per_side": cost_pct},
        "segments": stats,
        "preliminary_checks": {"test_sample_ge_8": enough,
                               "test_profitable": positive,
                               "test_survives_double_cost": resilient},
        "status": "experimental",
        "limitations": [
            "Parámetros fijos; no realiza búsqueda de hiperparámetros ni walk-forward rodante",
            "Las posiciones abiertas al final de cada tramo no se contabilizan como cerradas",
            "El simulador conservador usa stops al siguiente cierre y costos estimados, no fills reales",
            "No acredita robustez, estrés, baseline ni tamaño de muestra suficiente",
            "No apto para habilitar ejecución real",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest experimental solo lectura")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.csv, args.strategy)
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        if args.output.resolve() == args.csv.resolve():
            parser.error("La salida no puede sobrescribir el CSV de entrada")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)


if __name__ == "__main__":
    main()
