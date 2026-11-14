"""
Ejecutor Cloud 24/7 para GitHub Actions (corre en los servidores de GitHub con tu PC 100% apagada).
- Usa Walk-Forward OOS semanal, Gate de Velas Cerradas y RiskManager (Violenta pero Sagaz).
- Actualiza `README.md` y la Web App móvil (`docs/index.html` -> `https://ugiccba-prog.github.io/agentes/`).
- Hace commit + push periódico para ver todo desde el celular sin tener la PC prendida.
"""

from __future__ import annotations

from paper_panel import apply_requests, acknowledge
import requests
from impulse_study import update_study, REPORT
from functools import lru_cache
from market_data import get_historical_data
from overnight_supervisor import _get_macro_bias_1h
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

from candle_clock import NewCandleGate
from exchange_connector import LiveExecutionBroker
from overnight_supervisor import (
    OPTION_SPECS,
    build_option_runners,
    load_adaptive_config,
    log_event,
    run_all_options_tick,
    run_self_correction_phase,
    save_adaptive_config,
    sync_base_to_live_portfolio,
)
from risk_manager import RiskManager

README_FILE = Path(__file__).parent / "README.md"
LOG_FILE = Path(__file__).parent / "overnight_log.txt"


def _compute_real_stats(closed_trades: list[dict]) -> dict[str, float]:
    gross_win = sum(float(t.get("pnl_usd", 0.0)) for t in closed_trades if float(t.get("pnl_usd", 0.0)) > 0)
    gross_loss = abs(sum(float(t.get("pnl_usd", 0.0)) for t in closed_trades if float(t.get("pnl_usd", 0.0)) <= 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    return {"gross_win": gross_win, "gross_loss": gross_loss, "pf": pf}


def _normalize_runners(target) -> list[tuple[dict, LiveExecutionBroker]]:
    if isinstance(target, list):
        return [(item[0], item[1]) for item in target]
    out: list[tuple[dict, LiveExecutionBroker]] = []
    root = Path(__file__).parent
    for idx, spec in enumerate(OPTION_SPECS):
        b = target if idx == 0 else LiveExecutionBroker(
            initial_balance_usd=10000.0,
            portfolio_file=root / spec["file"],
            paper_only=True,
            risk_config=spec["risk_cfg"],
        )
        out.append((spec, b))
    return out


def _format_open_summary(positions: list[dict]) -> str:
    if not positions:
        return "⚪ *Sin abiertas*"
    parts = []
    for p in positions:
        icon = "🟢" if p.get("side") == "LONG" else "🔴"
        pnl = float(p.get("unrealized_pnl_usd", 0.0))
        parts.append(f"{icon} `{p.get('symbol')}` {p.get('side')} (`${pnl:+.2f}`)")
    return "<br>".join(parts)


def update_live_readme(runners_or_broker, cfg: dict) -> None:
    """Genera un panel en vivo de las 7 opciones en el README.md de GitHub."""
    runners = _normalize_runners(runners_or_broker)
    broker = runners[0][1]
    st = broker.state
    now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pnl_total = st.equity_usd - st.initial_balance_usd
    pnl_pct = (pnl_total / st.initial_balance_usd) * 100.0 if st.initial_balance_usd else 0.0
    win_rate = (st.winning_trades / st.total_trades * 100.0) if st.total_trades > 0 else 0.0
    stats = _compute_real_stats(st.closed_trades)
    preset = cfg.get("preset_name", "Violenta & Sagaz")

    comp_rows = []
    for spec, b in runners:
        bs = b.state
        b_net = bs.equity_usd - bs.initial_balance_usd
        b_pct = (b_net / bs.initial_balance_usd * 100.0) if bs.initial_balance_usd else 0.0
        b_stats = _compute_real_stats(bs.closed_trades)
        open_txt = _format_open_summary(bs.open_positions)
        comp_rows.append(
            f"| **{spec['name']}** | `{spec['rule_summary']}` | `${bs.equity_usd:,.2f}` | "
            f"**`${b_net:+,.2f}`** (`{b_pct:+.2f}%`) | {open_txt} | "
            f"`{bs.winning_trades}W / {bs.losing_trades}L` (`{bs.total_trades}`) | `{b_stats['pf']:.2f}` | `{bs.max_drawdown_pct:.2f}%` |"
        )
    comp_table = (
        "| Opción | Regla / Perfil | Equity Actual | Resultado Neto | Qué Hay Abierto Ahora | Cerradas (W/L) | PF | Peor Caída |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + "\n".join(comp_rows)
    )

    enabled_syms = [sym for sym, sc in cfg.get("symbols", {}).items() if sc.get("enabled")]
    matrix_header = "| Mercado Activo | " + " | ".join(spec["short_name"] for spec, _ in runners) + " |"
    matrix_sep = "|---|" + "|".join(["---"] * len(runners)) + "|"
    matrix_rows = []
    for sym in enabled_syms:
        cells = []
        for _, b in runners:
            pos = next((p for p in b.state.open_positions if p.get("symbol") == sym), None)
            if pos is None:
                cells.append("⚪ Esperando")
            else:
                icon = "🟢" if pos.get("side") == "LONG" else "🔴"
                pnl_p = float(pos.get("unrealized_pnl_usd", 0.0))
                cells.append(f"{icon} **{pos.get('side')}** (`${pnl_p:+.2f}`)")
        matrix_rows.append(f"| `{sym}` | " + " | ".join(cells) + " |")
    matrix_table = (
        matrix_header + "\n" + matrix_sep + "\n"
        + ("\n".join(matrix_rows) if matrix_rows else "| *Sin mercados activos* | " + " | ".join(["-"] * len(runners)) + " |")
    )

    open_rows = []
    for p in st.open_positions:
        side = str(p.get("side", ""))
        entry = float(p.get("entry_price", 0.0))
        sl = float(p.get("stop_loss", 0.0))
        protected = (sl >= entry) if side == "LONG" else (sl <= entry)
        sl_badge = "🔒 BE/Trail" if protected else "🛡️ Inicial"
        open_rows.append(
            f"| `{p['symbol']}` | **{side}** | `{p['strategy']}` | "
            f"`{entry:.5g}` | `{float(p.get('current_price', 0.0)):.5g}` | `{sl:.5g}` ({sl_badge}) | `{float(p.get('take_profit', 0.0)):.5g}` | "
            f"**${float(p.get('unrealized_pnl_usd', 0.0)):+.2f}** ({float(p.get('unrealized_pnl_pct', 0.0)):+.2f}%) |"
        )
    open_table = (
        "| Par | Lado | Estrategia | Entrada | Actual | Stop Loss | Take Profit | PnL Flotante |\n"
        "|---|---|---|---|---|---|---|---|"
        + ("\n" + "\n".join(open_rows) if open_rows else "\n| *Sin posiciones abiertas* | - | - | - | - | - | - | - |")
    )

    closed_rows = []
    for t in list(reversed(st.closed_trades))[:15]:
        pnl_u = float(t.get("pnl_usd", 0.0))
        res_badge = "✅ GANANCIA" if pnl_u > 0 else "❌ PÉRDIDA"
        closed_rows.append(
            f"| {res_badge} | `{t.get('exit_time', t.get('closed_at', ''))}` | `{t.get('symbol', '')}` | **{t.get('side', '')}** | "
            f"`{t.get('strategy', '')}` | `{float(t.get('entry_price', 0.0)):.5g}` -> `{float(t.get('exit_price', 0.0)):.5g}` | "
            f"**${pnl_u:+.2f}** ({float(t.get('pnl_pct', 0.0)):+.2f}%) | `{t.get('exit_reason', '')}` |"
        )
    closed_table = (
        "| Resultado | Fecha Cierre | Par | Lado | Estrategia | Entrada -> Salida | PnL Realizado | Motivo |\n"
        "|---|---|---|---|---|---|---|---|"
        + ("\n" + "\n".join(closed_rows) if closed_rows else "\n| - | *Sin operaciones cerradas aún* | - | - | - | - | - | - |")
    )

    active_syms = [
        f"`{sym}` (`{sc.get('strategy')}`)"
        for sym, sc in cfg.get("symbols", {}).items()
        if sc.get("enabled")
    ]
    active_line = ", ".join(active_syms) if active_syms else "*Ningún mercado activo esta semana*"

    recent_logs = []
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        recent_logs = lines[-20:]
    log_block = "\n".join(recent_logs) if recent_logs else "Iniciando bitácora..."

    md = f"""# 🦈 Agente de Trading Cuantitativo Multi-Mercado — 7 Opciones en Vivo 24/7

> 📱 **LINK PÚBLICO PARA EL CELULAR (Funciona con tu PC apagada):**  
> 👉 **https://ugiccba-prog.github.io/agentes/**  
> 🟢 **Modo Activo:** `{preset}` | **Sesión #:** `{cfg.get('session_count', 1)}` | **Última actualización:** `{now_utc}`

---

## 🏆 Comparativa en Vivo: 7 Opciones Corriendo sobre los Mismos Mercados ($10,000 USD c/u)

{comp_table}

---

## 🎯 Qué Hay Abierto y Qué No por Mercado en las 7 Opciones

{matrix_table}

---

## 💰 Detalle Opción 1 (`1. WF Estricta (Base)` / `live_portfolio.json`)

| Métrica | Valor Actual |
|---|---|
| **Capital Inicial** | `${st.initial_balance_usd:,.2f} USD` |
| **Equity Total en Vivo** | **`${st.equity_usd:,.2f} USD`** (`{pnl_pct:+.2f}%` / `${pnl_total:+,.2f} USD`) |
| **Pico Máximo (Peak Equity)** | `${st.peak_equity_usd:,.2f} USD` (Inicio Día UTC: `${st.day_start_equity_usd:,.2f} USD`) |
| **Efectivo Cerrado (Cash)** | `${st.cash_balance_usd:,.2f} USD` (PnL Realizado: `${st.realized_pnl_usd:+,.2f} USD`) |
| **PnL Flotante Actual** | `${st.unrealized_pnl_usd:+,.2f} USD` |
| **Win Rate & Profit Factor Real** | `{win_rate:.1f}%` (`{st.winning_trades}W / {st.losing_trades}L` de `{st.total_trades}`) · **PF `{stats['pf']:.2f}`** |
| **Meta Paper Trading (100 Trades)** | `{st.total_trades} / 100` cerrados |

**Canasta Compartida Activa (7 mercados):** {active_line}

---

## 📌 Posiciones Abiertas en Opción 1 Base ({len(st.open_positions)})

{open_table}

---

## 🏁 Últimas 15 Operaciones Realizadas en Opción 1 Base

{closed_table}

---

## 📜 Últimos Eventos de la Bitácora (`overnight_log.txt`)

```text
{log_block}
```
"""
    README_FILE.write_text(md, encoding="utf-8")


def sync_to_github() -> bool:
    """Guarda y sube el estado actual de las 7 opciones al repositorio para verlo en vivo."""
    try:
        subprocess.run(["git", "config", "user.name", "Agente Trading Cloud 24/7"], check=False)
        subprocess.run(["git", "config", "user.email", "ugiccba@gmail.com"], check=False)
        option_files = [spec["file"] for spec in OPTION_SPECS]
        subprocess.run(
            [
                "git",
                "add",
                "live_portfolio.json",
                *option_files,
                *(["impulse_study.json"] if REPORT.exists() else []),
                "adaptive_config.json",
                "wf_params.json",
                "overnight_log.txt",
                "README.md",
                "docs/index.html",
            ],
            check=False,
        )
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "🤖 Auto-Update Cloud: 7 Opciones y Dashboard en Vivo [skip ci]"],
                check=False,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
        return True
    except Exception as exc:
        log_event(f"⚠️ Aviso en sync_to_github: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloud Runner 24/7 para GitHub Actions")
    parser.add_argument("--minutes", type=int, default=240, help="Minutos de ejecución continua por bloque cloud")
    parser.add_argument("--tick", type=int, default=60, help="Segundos entre pulsos de trading")
    parser.add_argument("--sync-every", type=int, default=3, help="Hacer git push cada N pulsos")
    args = parser.parse_args()

    runners, quote = build_option_runners()
    base_broker = runners[0][1]
    mom_broker = next(b for spec, b, _ in runners if spec["key"] == "momentum")
    gate = NewCandleGate("15m")
    cfg = load_adaptive_config()

    session_num = int(cfg.get("session_count", 0)) + 1
    cfg["session_count"] = session_num
    save_adaptive_config(cfg)
    log_event(f"☁️ [GITHUB CLOUD] Iniciando bloque continuo 7 Opciones #{session_num} ({args.minutes} min)...")

    # Calibración Walk-Forward al arrancar el bloque (mantiene canasta compartida y caché de 7 días)
    cfg = run_self_correction_phase(base_broker, cfg, interval="15m")
    sync_base_to_live_portfolio(base_broker)
    update_live_readme(runners, cfg)
    sync_to_github()

    end_time = time.time() + (args.minutes * 60)
    ticks = 0
    last_recal = time.time()
    atr_map: dict[str, float] = {}
    first_tick = True

    while time.time() < end_time:
        ticks += 1
        done = []
        try:
            done = apply_requests(base_broker, cfg, save_adaptive_config, run_self_correction_phase, log_event)
            if done:
                sync_base_to_live_portfolio(base_broker)
        except requests.RequestException as exc:
            log_event(f"Panel temporalmente inaccesible: {type(exc).__name__}")
        if hasattr(quote, "cache_clear"):
            quote.cache_clear()
        if first_tick or gate.is_new_closed_candle():
            log_event(f"☁️ [GITHUB CLOUD] Cierre de vela #{ticks} (7 Opciones) de la sesión #{session_num}...")
            history = lru_cache(maxsize=None)(get_historical_data)
            macro = lru_cache(maxsize=None)(_get_macro_bias_1h)
            atr_map = run_all_options_tick(runners, cfg, history=history, macro=macro, report=log_event)
            try:
                update_study(mom_broker.state, history)
            except Exception as exc:
                log_event(f"Referencia de impulso pendiente: {exc}")
            first_tick = False
        else:
            for spec, account, _ in runners:
                events = account.update_open_positions(atr_by_symbol=atr_map)
                for ev in events:
                    log_event(ev if spec["key"] == "base" else f"[{spec['short_name']}] {ev}")
            sync_base_to_live_portfolio(base_broker)

        update_live_readme(runners, cfg)

        if time.time() - last_recal >= 3600:
            cfg = run_self_correction_phase(base_broker, cfg, interval="15m")
            last_recal = time.time()

        if done or ticks % args.sync_every == 0:
            if sync_to_github() and done:
                try:
                    acknowledge(done)
                except requests.RequestException:
                    log_event("Confirmación del panel pendiente")

        if time.time() + args.tick < end_time:
            time.sleep(args.tick)
        else:
            break

    update_live_readme(runners, cfg)
    sync_to_github()
    log_event(f"✅ [GITHUB CLOUD] Bloque #{session_num} completado.")


if __name__ == "__main__":
    main()
