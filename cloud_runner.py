"""
Ejecutor Cloud para GitHub Actions (corre en los servidores de GitHub con tu PC 100% apagada).
Realiza una ventana continua de trading + auto-corrección y guarda el estado de vuelta en el repositorio.
"""

from __future__ import annotations

import argparse
import time
from exchange_connector import LiveExecutionBroker
from overnight_supervisor import (
    load_adaptive_config,
    log_event,
    run_self_correction_phase,
    run_trading_tick,
    save_adaptive_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloud Runner 24/7 para GitHub Actions")
    parser.add_argument("--minutes", type=int, default=12, help="Minutos de ejecución continua por ciclo cloud")
    parser.add_argument("--tick", type=int, default=60, help="Segundos entre pulsos de trading")
    parser.add_argument("--recalibrate", action="store_true", help="Ejecutar fase de auto-corrección al inicio")
    args = parser.parse_args()

    broker = LiveExecutionBroker(initial_balance_usd=10000.0)
    cfg = load_adaptive_config()

    log_event("☁️ [GITHUB CLOUD] Iniciando bloque autónomo en la nube (PC apagada)...")

    # Cada 4 sesiones o si se pide explícitamente, recalibra estrategias
    session_num = int(cfg.get("session_count", 0)) + 1
    cfg["session_count"] = session_num
    if args.recalibrate or (session_num % 4 == 1):
        cfg = run_self_correction_phase(broker, cfg, interval="15m")
    else:
        save_adaptive_config(cfg)

    end_time = time.time() + (args.minutes * 60)
    ticks = 0
    while time.time() < end_time:
        ticks += 1
        log_event(f"☁️ [GITHUB CLOUD] Pulso #{ticks} de la sesión #{session_num}...")
        run_trading_tick(broker, cfg, interval="15m")
        if time.time() + args.tick < end_time:
            time.sleep(args.tick)
        else:
            break

    log_event(f"✅ [GITHUB CLOUD] Bloque #{session_num} completado. Persistiendo cartera en el repositorio.")


if __name__ == "__main__":
    main()
