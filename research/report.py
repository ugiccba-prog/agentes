"""Resumen en castellano de un escaneo experimental. No recomienda operar."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def render(report: dict) -> str:
    if report.get("mode") != "offline_research_only":
        raise ValueError("Solo se aceptan informes de investigación offline")
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("Lista de candidatos inválida")
    lines = [
        "# Tiburón | Informe de laboratorio (NO operativo)",
        "",
        f"Fuente: `{report.get('source_csv', 'sin especificar')}`",
        f"Fecha UTC: {report.get('created_at_utc', 'sin especificar')}",
        "",
        "Este informe NO acredita rentabilidad futura ni autoriza operar.",
        "Las carteras existentes no participan de esta investigación.",
        "",
        "| Estrategia | Operaciones test | PF test | Expectativa (R) | Costos x2 | Estado |",
        "|---|---:|---:|---:|---|---|",
    ]
    for item in candidates:
        name = str(item.get("strategy", "desconocida")).replace("|", "/")
        if item.get("status") == "error":
            lines.append(f"| {name} | — | — | — | — | Error de datos/cálculo |")
            continue
        test = item.get("test", {})
        normal = test.get("normal", {})
        doubled = test.get("double_cost", {})
        checks = item.get("checks", {})
        ok = item.get("preliminary_pass") is True
        status = "Filtros preliminares cumplidos; SIN validar" if ok else "Filtros preliminares incompletos"
        def number(value):
            try:
                return f"{float(value):.3f}"
            except (TypeError, ValueError, OverflowError):
                return "—"
        lines.append(
            f"| {name} | {normal.get('n', '—')} | {number(normal.get('pf'))} | "
            f"{number(normal.get('expectancy'))} | "
            f"{'positiva' if checks.get('test_survives_double_cost') is True else 'no confirmada'} | {status} |"
        )
    lines += [
        "",
        "## Cómo leerlo",
        "- **PF**: ganancias acumuladas divididas por pérdidas acumuladas; no es porcentaje de ganancia.",
        "- **Expectativa (R)**: resultado medio expresado en unidades del riesgo inicial por operación.",
        "- **Costos x2**: prueba de sensibilidad con comisiones/spread estimados duplicados.",
        "- **Test**: tramo histórico reservado; comparar varias estrategias sobre él puede sesgar la selección.",
        "- **Pendiente**: validación walk-forward rodante, estrés, muestras más amplias y ejecución verificable.",
        "",
        "**Prohibido interpretar estos resultados como señales de compra o venta.**",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Traducir informe offline a Markdown")
    parser.add_argument("json_report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.json_report.resolve() == args.output.resolve():
        parser.error("No sobrescribir el informe JSON original")
    report = json.loads(args.json_report.read_text(encoding="utf-8"))
    result = render(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
