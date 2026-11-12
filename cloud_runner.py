"""
Ejecutor Cloud 24/7 para GitHub Actions (corre en los servidores de GitHub con tu PC 100% apagada).
- Ejecuta ciclos continuos de Trading + Auto-Corrección.
- Actualiza `README.md` con un Dashboard en Vivo visible directamente al abrir el repositorio en GitHub.
- Hace commit + push periódico cada pocos minutos para que siempre veas el estado actualizado en GitHub.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

from exchange_connector import LiveExecutionBroker
from overnight_supervisor import (
    load_adaptive_config,
    log_event,
    run_self_correction_phase,
    run_trading_tick,
    save_adaptive_config,
)

README_FILE = Path(__file__).parent / "README.md"
LOG_FILE = Path(__file__).parent / "overnight_log.txt"


def update_live_readme(broker: LiveExecutionBroker, cfg: dict) -> None:
    """Genera un panel en vivo directamente en el README.md de GitHub."""
    st = broker.state
    now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pnl_total = st.equity_usd - st.initial_balance_usd
    pnl_pct = (pnl_total / st.initial_balance_usd) * 100.0 if st.initial_balance_usd else 0.0

    open_rows = []
    for p in st.open_positions:
        open_rows.append(
            f"| `{p['symbol']}` | **{p['side']}** | `{p['strategy']}` | "
            f"`{p['entry_price']}` | `{p['current_price']}` | `{p['stop_loss']}` | `{p['take_profit']}` | "
            f"**${p['unrealized_pnl_usd']:+.2f}** ({p['unrealized_pnl_pct']:+.2f}%) |"
        )
    open_table = (
        "| Par | Lado | Estrategia | Entrada | Actual | Stop Loss | Take Profit | PnL Flotante |\n"
        "|---|---|---|---|---|---|---|---|"
        + ("\n" + "\n".join(open_rows) if open_rows else "\n| *Sin posiciones abiertas* | - | - | - | - | - | - | - |")
    )

    closed_rows = []
    for t in list(reversed(st.closed_trades))[:10]:
        closed_rows.append(
            f"| `{t.get('closed_at', '')}` | `{t.get('symbol', '')}` | **{t.get('side', '')}** | "
            f"`{t.get('strategy', '')}` | **${float(t.get('pnl_usd', 0.0)):+.2f}** | `{t.get('exit_reason', '')}` |"
        )
    closed_table = (
        "| Fecha Cierre | Par | Lado | Estrategia | PnL Realizado | Motivo |\n"
        "|---|---|---|---|---|---|"
        + ("\n" + "\n".join(closed_rows) if closed_rows else "\n| *Sin operaciones cerradas aún* | - | - | - | - | - |")
    )

    recent_logs = []
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        recent_logs = lines[-12:]
    log_block = "\n".join(recent_logs) if recent_logs else "Iniciando bitácora..."

    md = f"""# 🦈 Agente de Trading Cuantitativo Multi-Mercado (EN VIVO 24/7 EN LA NUBE)

> 🟢 **ESTADO DEL AGENTE:** OPERANDO EN GITHUB CLOUD (Sin necesidad de PC encendida)  
> 🕒 **Última actualización en vivo:** `{now_utc}` | **Sesión #:** `{cfg.get('session_count', 1)}` | **Auto-correcciones:** `{cfg.get('corrections_applied', 0)}`

---

## 💰 Resumen de Cuenta en Tiempo Real

| Métrica | Valor Actual |
|---|---|
| **Capital Inicial** | `${st.initial_balance_usd:,.2f} USD` |
| **Equity Total en Vivo** | **`${st.equity_usd:,.2f} USD`** (`{pnl_pct:+.2f}%` / `${pnl_total:+,.2f} USD`) |
| **Efectivo Cerrado (Cash)** | `${st.cash_balance_usd:,.2f} USD` (PnL Realizado: `${st.realized_pnl_usd:+,.2f} USD`) |
| **PnL Flotante Actual** | `${st.unrealized_pnl_usd:+,.2f} USD` |
| **Posiciones Abiertas / Cerradas** | `{len(st.open_positions)}` abiertas / `{st.total_trades}` cerradas (Ganadoras: `{st.winning_trades}` / Perdedoras: `{st.losing_trades}`) |

---

## 📌 Posiciones Abiertas Ahora Mismo ({len(st.open_positions)})

{open_table}

---

## 🏁 Últimas 10 Operaciones Cerradas

{closed_table}

---

## 📜 Últimos Eventos de la Bitácora (`overnight_log.txt`)

```text
{log_block}
```

---

## 💻 Cómo correrlo también en otra PC local (Opcional)

```powershell
git clone https://github.com/ugiccba-prog/agentes.git
cd agentes
python -m pip install -r requirements.txt
.\\INICIAR_AGENTE_24_7.bat
```
"""
    README_FILE.write_text(md, encoding="utf-8")


def sync_to_github() -> None:
    """Guarda y sube el estado actual al repositorio para que se vea en tiempo real."""
    try:
        subprocess.run(["git", "config", "user.name", "Agente Trading Cloud 24/7"], check=False)
        subprocess.run(["git", "config", "user.email", "ugiccba@gmail.com"], check=False)
        subprocess.run(
            ["git", "add", "live_portfolio.json", "adaptive_config.json", "overnight_log.txt", "README.md"],
            check=False,
        )
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "🤖 Auto-Update Cloud: Cartera y Dashboard en Vivo [skip ci]"],
                check=False,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push", "origin", "main"], check=False)
    except Exception as exc:
        log_event(f"⚠️ Aviso en sync_to_github: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloud Runner 24/7 para GitHub Actions")
    parser.add_argument("--minutes", type=int, default=240, help="Minutos de ejecución continua por bloque cloud")
    parser.add_argument("--tick", type=int, default=60, help="Segundos entre pulsos de trading")
    parser.add_argument("--sync-every", type=int, default=3, help="Hacer git push cada N pulsos")
    args = parser.parse_args()

    broker = LiveExecutionBroker(initial_balance_usd=10000.0)
    cfg = load_adaptive_config()

    session_num = int(cfg.get("session_count", 0)) + 1
    cfg["session_count"] = session_num
    log_event(f"☁️ [GITHUB CLOUD] Iniciando bloque continuo #{session_num} ({args.minutes} min)...")

    # Calibración inicial al arrancar el bloque
    cfg = run_self_correction_phase(broker, cfg, interval="15m")
    update_live_readme(broker, cfg)
    sync_to_github()

    end_time = time.time() + (args.minutes * 60)
    ticks = 0
    last_recal = time.time()

    while time.time() < end_time:
        ticks += 1
        log_event(f"☁️ [GITHUB CLOUD] Pulso #{ticks} de la sesión #{session_num}...")
        run_trading_tick(broker, cfg, interval="15m")
        update_live_readme(broker, cfg)

        # Auto-corrección cada 60 minutos dentro del mismo bloque de 4 horas
        if time.time() - last_recal >= 3600:
            cfg = run_self_correction_phase(broker, cfg, interval="15m")
            last_recal = time.time()

        if ticks % args.sync_every == 0:
            sync_to_github()

        if time.time() + args.tick < end_time:
            time.sleep(args.tick)
        else:
            break

    update_live_readme(broker, cfg)
    sync_to_github()
    log_event(f"✅ [GITHUB CLOUD] Bloque #{session_num} completado.")


if __name__ == "__main__":
    main()
