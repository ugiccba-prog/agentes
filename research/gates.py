"""Evaluación reproducible de siete puertas, sin acceso a brokers ni carteras.

Entrada: JSON con candidate_id, evidence y gates (claves reason, costs,
out_of_sample, robustness, stress, baseline, sample).
Cada gate: {"passed": true|false, "evidence": "texto verificable"}.
La evaluación no calcula backtests: valida y registra evidencias externas.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

GATES = ("reason", "costs", "out_of_sample", "robustness", "stress", "baseline", "sample")


def evaluate(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        raise ValueError("El candidato debe ser un objeto JSON")
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id debe ser texto no vacío")
    supplied = candidate.get("gates", {})
    if not isinstance(supplied, dict):
        raise ValueError("gates debe ser un objeto")
    results = {}
    for name in GATES:
        item = supplied.get(name)
        valid = isinstance(item, dict) and type(item.get("passed")) is bool
        evidence = item.get("evidence", "") if valid else ""
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        passed = bool(valid and item["passed"] and evidence)
        results[name] = {
            "passed": passed,
            "evidence": evidence,
            "reason": "ok" if passed else "Falta evidencia verificable o puerta no superada",
        }
    approved = all(item["passed"] for item in results.values())
    return {
        "candidate_id": candidate_id.strip(),
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "documented" if approved else "experimental",
        "all_gates_passed": approved,
        "gates": results,
        "warning": "Documentación completa no implica validación estadística; NO autoriza trading real.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluador documental de siete puertas (solo lectura)")
    parser.add_argument("candidate", type=Path, help="JSON de evidencias del candidato")
    parser.add_argument("--output", type=Path, help="Ruta de reporte JSON (opcional)")
    args = parser.parse_args()
    report = evaluate(json.loads(args.candidate.read_text(encoding="utf-8")))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
