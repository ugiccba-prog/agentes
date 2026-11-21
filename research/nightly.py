"""Investigador por lotes: CSV local -> informe; sin red, broker ni carteras.

Ejemplo: python -m research.nightly --csv datos/BTCUSDT.csv --output research_reports/scan.json
Elige candidatos por criterios prefijados; no optimiza sobre el tramo test.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from strategies import STRATEGY_CATALOG
from research.backtest import run


def scan(csv_path: Path, *, max_candidates: int = 12) -> dict:
    if max_candidates < 1 or max_candidates > 30:
        raise ValueError("max_candidates debe estar entre 1 y 30")
    strategies = list(STRATEGY_CATALOG)[:max_candidates]
    candidates = []
    for strategy in strategies:
        try:
            result = run(csv_path, strategy)
            checks = result["preliminary_checks"]
            candidates.append({
                "strategy": strategy,
                "status": "experimental",
                "preliminary_pass": all(checks.values()),
                "test": result["segments"]["test"],
                "checks": checks,
                "parameters": result["parameters"],
                "limitations": result["limitations"],
            })
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            candidates.append({"strategy": strategy, "status": "error", "error": str(exc)})
    # Orden solo para lectura: NO promocionar ni seleccionar para operar por test.
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(csv_path),
        "mode": "offline_research_only",
        "candidates": candidates,
        "note": "Exploración descriptiva: comparar muchas estrategias sobre un mismo test "
                "introduce sesgo de selección. Resultados NO validados ni operables.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigador offline sin ejecución")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=12)
    args = parser.parse_args()
    report = scan(args.csv, max_candidates=args.max_candidates)
    if args.output and args.output.resolve() == args.csv.resolve():
        parser.error("La salida no puede sobrescribir el CSV de entrada")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Informe experimental guardado en {args.output}")


if __name__ == "__main__":
    main()
