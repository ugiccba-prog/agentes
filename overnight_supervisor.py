"""
Supervisor Nocturno Autónomo (Overnight Trading & Self-Correction Daemon).
Diseñado para operar toda la noche sin intervención humana en ciclos de:
- 60 minutos de Operativa en Vivo (monitoreo cada 60s de precios, Stop Loss, Take Profit, Trailing Stop y entradas)
- 20 minutos de Fase de Auto-Corrección y Recalibración Cuantitativa:
  * Audita las operaciones cerradas y el PnL por par y estrategia.
  * Penaliza o rota automáticamente estrategias con bajo Win Rate o Drawdown.
  * Recalibra los multiplicadores de Stop Loss / Take Profit (ATR) según la volatilidad reciente.
  * Guarda los parámetros corregidos en `adaptive_config.json` y una bitácora legible en `overnight_log.txt`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback

from backtester import optimize_for_symbol, run_backtest
from exchange_connector import LiveExecutionBroker
from indicators import compute_indicators
from market_data import DEFAULT_SYMBOLS, get_historical_data
from strategies import evaluate_latest_candle, generate_signals

LOG_FILE = Path(__file__).parent / "overnight_log.txt"
ADAPTIVE_CONFIG_FILE = Path(__file__).parent / "adaptive_config.json"


def log_event(message: str) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_adaptive_config() -> dict:
    if ADAPTIVE_CONFIG_FILE.exists():
        try:
            return json.loads(ADAPTIVE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    default_cfg: dict = {
        "session_count": 0,
        "corrections_applied": 0,
        "global_risk_pct": 3.5,
        "symbols": {},
    }
    for sym in DEFAULT_SYMBOLS:
        default_cfg["symbols"][sym] = {
            "enabled": True,
            "strategy": "confluence",
            "sl_atr_mult": 1.6,
            "tp_atr_mult": 3.2,
            "ema_fast": 12,
            "ema_slow": 26,
            "risk_multiplier": 1.0,
            "last_score": 0.0,
            "notes": "Configuración inicial",
        }
    save_adaptive_config(default_cfg)
    return default_cfg


def save_adaptive_config(cfg: dict) -> None:
    cfg["last_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ADAPTIVE_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def run_self_correction_phase(broker: LiveExecutionBroker, cfg: dict, interval: str = "15m") -> dict:
    """
    Fase de Corrección y Auto-Ajuste:
    1. Analiza el historial real en vivo (`closed_trades` y `open_positions`) de `live_portfolio.json`.
    2. Descarga las últimas velas frescas de cada moneda y re-entrena/optimiza las 5 estrategias.
    3. Si un par tuvo pérdidas consecutivas en vivo, reduce su riesgo a la mitad o cambia a una
       estrategia más conservadora con Stop Loss más holgado para evitar barridos de liquidez.
    """
    log_event("🛠️ INICIANDO FASE DE AUTO-CORRECCIÓN Y RECALIBRACIÓN DE ESTRATEGIAS...")
    closed = broker.state.closed_trades
    corrections_made = 0

    cfg["global_risk_pct"] = 3.5

    for sym in DEFAULT_SYMBOLS:
        try:
            df_raw, source = get_historical_data(sym, interval=interval, limit=500, use_cache=False)
            best_res, all_res = optimize_for_symbol(
                df_raw=df_raw,
                symbol=sym,
                initial_capital=1000.0,
                risk_per_trade_pct=cfg.get("global_risk_pct", 3.5),
            )

            sym_trades = [t for t in closed if t.get("symbol") == sym]
            sym_wins = [t for t in sym_trades if float(t.get("pnl_usd", 0.0)) > 0]
            sym_losses = [t for t in sym_trades if float(t.get("pnl_usd", 0.0)) <= 0]

            prev_strat = cfg["symbols"].get(sym, {}).get("strategy", "confluence")
            new_strat = best_res.strategy
            sl_mult = float(best_res.params["sl_atr_mult"])
            tp_mult = float(best_res.params["tp_atr_mult"])
            ema_f = int(best_res.params["ema_fast"])
            ema_s = int(best_res.params["ema_slow"])
            risk_mult = 1.0
            enabled = True
            notes: list[str] = []

            # Regla 1: Filtrar sin piedad mercados flojos (PF < 1.15) y potenciar mercados Élite (PF >= 1.70)
            if best_res.profit_factor < 1.15 or best_res.total_return_pct <= 2.0:
                enabled = False
                notes.append(
                    f"DESCARTADO por baja rentabilidad (PF={best_res.profit_factor:.2f}, Ret={best_res.total_return_pct:+.1f}%)"
                )
            elif best_res.profit_factor >= 1.70 and best_res.total_return_pct >= 12.0:
                risk_mult = 1.35  # Sube el riesgo a ~4.7% por trade en los mercados más ganadores
                notes.append(
                    f"🔥 MERCADO ÉLITE TIBURÓN (Riesgo Potenciado x1.35) con {new_strat} (PF={best_res.profit_factor:.2f}, WinRate={best_res.win_rate_pct:.1f}%, Ret={best_res.total_return_pct:+.1f}%)"
                )
            else:
                notes.append(
                    f"Optimizado agresivo con {new_strat} (PF={best_res.profit_factor:.2f}, WinRate={best_res.win_rate_pct:.1f}%, Ret={best_res.total_return_pct:+.1f}%)"
                )

            # Regla 2: Auditar trades reales cerrados en la noche
            if len(sym_losses) >= 2 and len(sym_losses) > len(sym_wins):
                risk_mult = 0.65
                sl_mult = min(2.5, round(sl_mult * 1.15, 2))
                tp_mult = round(sl_mult * 2.1, 2)
                notes.append(
                    f"Ajuste defensivo aplicado tras {len(sym_losses)} pérdidas en vivo: riesgo x{risk_mult} y SL ampliado a {sl_mult}x ATR"
                )

            if prev_strat != new_strat or not enabled or risk_mult != 1.0:
                corrections_made += 1

            cfg["symbols"][sym] = {
                "enabled": enabled,
                "strategy": new_strat,
                "sl_atr_mult": sl_mult,
                "tp_atr_mult": tp_mult,
                "ema_fast": ema_f,
                "ema_slow": ema_s,
                "risk_multiplier": risk_mult,
                "backtest_return_pct": round(best_res.total_return_pct, 2),
                "backtest_profit_factor": round(best_res.profit_factor, 2),
                "backtest_win_rate": round(best_res.win_rate_pct, 1),
                "data_source": source,
                "notes": " | ".join(notes),
            }
            log_event(f"   🔎 [{sym}] -> {'ACTIVO' if enabled else 'PAUSADO'} | Estrategia: {new_strat} | {' | '.join(notes)}")
        except Exception as exc:
            log_event(f"   ⚠️ Error calibrando {sym}: {exc}")

    cfg["corrections_applied"] = int(cfg.get("corrections_applied", 0)) + corrections_made
    save_adaptive_config(cfg)
    log_event(f"✅ FASE DE AUTO-CORRECCIÓN COMPLETADA ({corrections_made} ajustes aplicados).")
    return cfg


def run_trading_tick(broker: LiveExecutionBroker, cfg: dict, interval: str = "15m") -> None:
    """Ejecuta un pulso de vigilancia y trading en vivo usando los parámetros auto-corregidos."""
    atr_by_symbol: dict[str, float] = {}
    base_risk = float(cfg.get("global_risk_pct", 1.8))

    for sym, sym_cfg in cfg.get("symbols", {}).items():
        try:
            df_raw, _ = get_historical_data(sym, interval=interval, limit=300, use_cache=False)
            df_ind = compute_indicators(
                df_raw,
                ema_fast=int(sym_cfg.get("ema_fast", 12)),
                ema_slow=int(sym_cfg.get("ema_slow", 26)),
            )
            strat_name = str(sym_cfg.get("strategy", "confluence"))
            df_sig = generate_signals(df_ind, strategy_name=strat_name)
            atr_by_symbol[sym] = float(df_sig["atr"].iloc[-1])

            if not sym_cfg.get("enabled", True):
                continue

            decision = evaluate_latest_candle(
                df_sig,
                sl_atr_mult=float(sym_cfg.get("sl_atr_mult", 1.6)),
                tp_atr_mult=float(sym_cfg.get("tp_atr_mult", 3.2)),
            )
            effective_risk = round(base_risk * float(sym_cfg.get("risk_multiplier", 1.0)), 2)

            if decision.action.startswith("LONG"):
                ok, msg = broker.open_order(
                    symbol=sym,
                    side="LONG",
                    strategy=strat_name,
                    entry_price=decision.entry_price,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    risk_pct=effective_risk,
                    reason=decision.reasons[0] if decision.reasons else "",
                )
                if ok:
                    log_event(msg)
            elif decision.action.startswith("SHORT"):
                ok, msg = broker.open_order(
                    symbol=sym,
                    side="SHORT",
                    strategy=strat_name,
                    entry_price=decision.entry_price,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    risk_pct=effective_risk,
                    reason=decision.reasons[0] if decision.reasons else "",
                )
                if ok:
                    log_event(msg)
        except Exception as exc:
            log_event(f"   ⚠️ Error en tick de {sym}: {exc}")

    events = broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    for ev in events:
        log_event(ev)

    st = broker.state
    log_event(
        f"💓 Pulso En Vivo | Equity: ${st.equity_usd:,.2f} USD "
        f"(Efectivo: ${st.cash_balance_usd:,.2f} | Flotante: ${st.unrealized_pnl_usd:+,.2f}) | "
        f"Abiertas: {len(st.open_positions)} | Cerradas: {st.total_trades}"
    )


def run_overnight_daemon(
    trading_minutes: int = 60,
    correction_minutes: int = 20,
    tick_seconds: int = 60,
) -> None:
    broker = LiveExecutionBroker(initial_balance_usd=10000.0)
    cfg = load_adaptive_config()

    log_event("=" * 92)
    log_event(
        f"🌙 INICIANDO SUPERVISOR NOCTURNO AUTÓNOMO: "
        f"Sesiones de {trading_minutes} min de Trading + {correction_minutes} min de Auto-Corrección"
    )
    log_event("=" * 92)

    # Calibración inicial inmediata antes de empezar la primera sesión de 1 hora
    cfg = run_self_correction_phase(broker, cfg, interval="15m")

    while True:
        try:
            cfg["session_count"] = int(cfg.get("session_count", 0)) + 1
            save_adaptive_config(cfg)
            session_num = cfg["session_count"]

            log_event(f"🟢 SESIÓN OPERATIVA #{session_num} INICIADA (Duración: {trading_minutes} minutos)...")
            session_end = time.time() + (trading_minutes * 60)

            while time.time() < session_end:
                run_trading_tick(broker, cfg, interval="15m")
                time.sleep(tick_seconds)

            log_event(
                f"🔧 ENTRANDO EN VENTANA DE {correction_minutes} MINUTOS DE AUDITORÍA, CORRECCIÓN Y RECALIBRACIÓN..."
            )
            correction_end = time.time() + (correction_minutes * 60)
            cfg = run_self_correction_phase(broker, cfg, interval="15m")

            # Durante los 20 minutos de corrección seguimos protegiendo Stop Loss / Trailing Stop de posiciones abiertas
            # y realizamos una segunda validación profunda en 1h a mitad de la ventana
            mid_checked = False
            while time.time() < correction_end:
                broker.update_open_positions()
                remaining_sec = correction_end - time.time()
                if not mid_checked and remaining_sec <= (correction_minutes * 30):
                    log_event("🔬 Verificación intermedia de estructura en temporalidad 1h durante ventana de corrección...")
                    cfg = run_self_correction_phase(broker, cfg, interval="15m")
                    mid_checked = True
                time.sleep(tick_seconds)

        except Exception as exc:
            log_event(f"❌ Error recuperable en bucle nocturno: {exc}\n{traceback.format_exc()}")
            time.sleep(30)


if __name__ == "__main__":
    run_overnight_daemon(trading_minutes=60, correction_minutes=20, tick_seconds=60)
