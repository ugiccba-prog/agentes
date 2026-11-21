"""Comparador reproducible (solo lectura) de carteras de prueba paper_*.json.

Calcula métricas honestas en USD y múltiplos R, brecha de pico flotante fantasma,
costo acumulado de salidas en breakeven y promedio real de Stop Loss completos.
No modifica ningún archivo de entrada ni conecta con brokers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

PORTFOLIO_LABELS: dict[str, str] = {
    "paper_base.json": "1. WF Base",
    "paper_momentum.json": "2. WF+Impulso / Híbrido",
    "paper_chimichurri.json": "3. Chimichurri",
    "paper_sniper.json": "4. Francotirador",
    "paper_berserker.json": "5. Berserker",
    "paper_rebote.json": "6. Resorte",
    "paper_ruptura.json": "7. Escape",
}


def _compute_realized_watermarks(initial_balance: float, closed_trades: list[dict]) -> tuple[float, float]:
    """Calcula pico realizado y MaxDD realizado (%) solo con cierres reales."""
    eq = float(initial_balance)
    realized_peak = eq
    realized_max_dd = 0.0
    for trade in closed_trades:
        eq += float(trade.get("pnl_usd", 0.0))
        if eq > realized_peak:
            realized_peak = eq
        if realized_peak > 0:
            dd = ((realized_peak - eq) / realized_peak) * 100.0
            if dd > realized_max_dd:
                realized_max_dd = dd
    return round(realized_peak, 2), round(realized_max_dd, 2)


def analyze_portfolio(data: dict, filename: str) -> dict:
    """Extrae métricas comparativas de un diccionario de cartera paper."""
    initial = float(data.get("initial_balance_usd", 10000.0))
    equity = float(data.get("equity_usd", initial))
    recorded_peak = float(data.get("peak_equity_usd", equity))
    recorded_max_dd = float(data.get("max_drawdown_pct", 0.0))
    closed = list(data.get("closed_trades", []))

    pnls = [float(t.get("pnl_usd", 0.0)) for t in closed]
    rs = [float(t.get("r_multiple", 0.0)) for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = len(pnls) - wins
    wr_pct = (wins / len(pnls) * 100.0) if pnls else 0.0

    gw_usd = sum(p for p in pnls if p > 0)
    gl_usd = abs(sum(p for p in pnls if p <= 0))
    pf_usd = (gw_usd / gl_usd) if gl_usd > 0 else (99.0 if gw_usd > 0 else 0.0)

    gw_r = sum(r for r in rs if r > 0)
    gl_r = abs(sum(r for r in rs if r <= 0))
    pf_r = (gw_r / gl_r) if gl_r > 0 else (99.0 if gw_r > 0 else 0.0)

    sum_r = sum(rs)
    exp_r = statistics.mean(rs) if rs else 0.0

    cum_r, peak_r, max_dd_r = 0.0, 0.0, 0.0
    for r in rs:
        cum_r += r
        if cum_r > peak_r:
            peak_r = cum_r
        if peak_r - cum_r > max_dd_r:
            max_dd_r = peak_r - cum_r

    realized_peak, realized_max_dd = _compute_realized_watermarks(initial, closed)
    ghost_peak_gap = round(recorded_peak - realized_peak, 2)

    be_trades = [
        t for t in closed
        if "Stop Loss" in str(t.get("exit_reason", "")) and -0.5 < float(t.get("r_multiple", 0.0)) <= 0.1
    ]
    full_sl_trades = [
        t for t in closed
        if "Stop Loss" in str(t.get("exit_reason", "")) and float(t.get("r_multiple", 0.0)) <= -0.5
    ]

    full_sl_rs = [float(t.get("r_multiple", 0.0)) for t in full_sl_trades]
    return {
        "file": filename,
        "label": PORTFOLIO_LABELS.get(filename, filename),
        "initial_balance_usd": round(initial, 2),
        "equity_usd": round(equity, 2),
        "net_usd": round(equity - initial, 2),
        "trades": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wr_pct, 1),
        "pf_usd": round(pf_usd, 2),
        "pf_r": round(pf_r, 2),
        "sum_r": round(sum_r, 2),
        "exp_r": round(exp_r, 3),
        "max_dd_r": round(max_dd_r, 2),
        "recorded_peak_usd": round(recorded_peak, 2),
        "realized_peak_usd": realized_peak,
        "ghost_peak_gap_usd": ghost_peak_gap,
        "recorded_max_dd_pct": round(recorded_max_dd, 2),
        "realized_max_dd_pct": realized_max_dd,
        "be_exit_count": len(be_trades),
        "be_exit_cost_usd": round(sum(float(t.get("pnl_usd", 0.0)) for t in be_trades), 2),
        "be_exit_sum_r": round(sum(float(t.get("r_multiple", 0.0)) for t in be_trades), 2),
        "full_sl_count": len(full_sl_trades),
        "full_sl_avg_r": round(statistics.mean(full_sl_rs), 3) if full_sl_rs else 0.0,
        "full_sl_worst_r": round(min(full_sl_rs), 3) if full_sl_rs else 0.0,
    }


def _analyze_impulse(study_path: Path) -> list[dict]:
    if not study_path.exists():
        return []
    raw = json.loads(study_path.read_text(encoding="utf-8"))
    controls = raw.get("controls", {})
    out = []
    for key, val in controls.items():
        rn = val.get("random_net", [])
        tn = float(val.get("trade_net", 0.0))
        pct = (sum(1 for x in rn if float(x) <= tn) / len(rn) * 100.0) if rn else 0.0
        out.append({
            "key": key,
            "trade_net_usd": round(tn, 2),
            "random_mean_usd": round(statistics.mean(rn), 2) if rn else 0.0,
            "random_median_usd": round(statistics.median(rn), 2) if rn else 0.0,
            "percentile": round(pct, 1),
            "windows": int(val.get("windows", 0)),
        })
    return out


def compare_directory(trial_dir: Path) -> dict:
    """Analiza todas las carteras paper_*.json en un directorio sin modificar archivos."""
    if not trial_dir.is_dir():
        raise ValueError(f"Directorio inválido: {trial_dir}")
    ordered = list(PORTFOLIO_LABELS.keys())
    found = sorted(p.name for p in trial_dir.glob("paper_*.json"))
    filenames = [f for f in ordered if f in found] + [f for f in found if f not in ordered]
    if not filenames:
        raise ValueError(f"No se encontraron archivos paper_*.json en {trial_dir}")

    portfolios = []
    for fname in filenames:
        payload = json.loads((trial_dir / fname).read_text(encoding="utf-8"))
        portfolios.append(analyze_portfolio(payload, fname))

    return {
        "mode": "offline_trial_comparison",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "source_dir": str(trial_dir),
        "portfolios": portfolios,
        "impulse_study": _analyze_impulse(trial_dir / "impulse_study.json"),
    }


def render_markdown(report: dict) -> str:
    """Renderiza el reporte comparativo de carteras en Markdown claro."""
    lines = [
        "# Comparativa Reproducible de Prueba (Solo Lectura)",
        "",
        f"- **Directorio fuente:** `{report['source_dir']}`",
        f"- **Generado (UTC):** `{report['generated_at_utc']}`",
        "",
        "## 1. Rendimiento en USD, Múltiplos R y Brecha de Pico Flotante",
        "",
        "| Cartera | Equity | Neto USD | Trades (W/L) | WR | PF USD | PF R | Suma R | ExpR | MaxDD R | Pico Reg. | Pico Realizado | Brecha Flotante | DD Reg. | DD Realizado |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p in report["portfolios"]:
        lines.append(
            f"| {p['label']} | `${p['equity_usd']:,.2f}` | `${p['net_usd']:+,.2f}` | "
            f"`{p['trades']}` (`{p['wins']}W/{p['losses']}L`) | `{p['win_rate_pct']:.1f}%` | "
            f"`{p['pf_usd']:.2f}` | `{p['pf_r']:.2f}` | `{p['sum_r']:+.2f}R` | `{p['exp_r']:+.3f}R` | "
            f"`{p['max_dd_r']:.2f}R` | `${p['recorded_peak_usd']:,.2f}` | `${p['realized_peak_usd']:,.2f}` | "
            f"`+${p['ghost_peak_gap_usd']:,.2f}` | `{p['recorded_max_dd_pct']:.2f}%` | `{p['realized_max_dd_pct']:.2f}%` |"
        )

    lines += [
        "",
        "## 2. Fricción de Ejecución: Salidas en Breakeven y Stop Loss Completos",
        "",
        "| Cartera | Salidas BE | Costo BE (USD) | Suma R en BE | Full SL (n) | Promedio Full SL (R) | Peor SL (R) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in report["portfolios"]:
        lines.append(
            f"| {p['label']} | `{p['be_exit_count']}` | `${p['be_exit_cost_usd']:+,.2f}` | "
            f"`{p['be_exit_sum_r']:+.2f}R` | `{p['full_sl_count']}` | "
            f"`{p['full_sl_avg_r']:+.3f}R` | `{p['full_sl_worst_r']:+.3f}R` |"
        )

    impulse = report.get("impulse_study", [])
    if impulse:
        lines += [
            "",
            "## 3. Control de Impulso vs Azar (`impulse_study.json`)",
            "",
            "| Operación | Neto Trade | Media Azar | Mediana Azar | Percentil | Ventanas |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for item in impulse:
            lines.append(
                f"| `{item['key']}` | `${item['trade_net_usd']:+.2f}` | `${item['random_mean_usd']:+.2f}` | "
                f"`${item['random_median_usd']:+.2f}` | `{item['percentile']:.1f}%` | `{item['windows']}` |"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Comparador reproducible de carteras paper (solo lectura)")
    parser.add_argument("trial_dir", type=Path, help="Carpeta con archivos paper_*.json")
    parser.add_argument("--output", type=Path, help="Ruta de salida (.md o .json)")
    args = parser.parse_args()

    report = compare_directory(args.trial_dir)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.suffix.lower() == ".json":
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            args.output.write_text(render_markdown(report), encoding="utf-8")
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    main()
