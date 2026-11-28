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
from functools import lru_cache
import json
from pathlib import Path
import time
import traceback

from candle_clock import NewCandleGate, closed_only
from exchange_connector import LiveExecutionBroker, PORTFOLIO_FILE, shared_path_provider
from indicators import compute_indicators
from market_data import DEFAULT_SYMBOLS, get_historical_data
from risk_manager import RiskConfig, RiskManager, estimate_price_tick
from strategies import evaluate_latest_candle, generate_signals
from walk_forward import get_validated_params

LOG_FILE = Path(__file__).parent / "overnight_log.txt"
ADAPTIVE_CONFIG_FILE = Path(__file__).parent / "adaptive_config.json"

CORE_SHARED_BASKET: tuple[str, ...] = (
    "BTC/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "AVAX/USDT",
    "DOGE/USDT",
    "SUI/USDT",
    "PEPE/USDT",
)

CORE_DEFAULT_STRATEGIES: dict[str, str] = {
    "BTC/USDT": "chimichurri_alpha",
    "SOL/USDT": "chimichurri_alpha",
    "BNB/USDT": "chimichurri_alpha",
    "AVAX/USDT": "bollinger_reversion",
    "DOGE/USDT": "chimichurri_alpha",
    "SUI/USDT": "chimichurri_alpha",
    "PEPE/USDT": "volatility_breakout",
}

OPTION_SPECS: list[dict] = [
    {
        "key": "base",
        "name": "1. WF Estricta v2 (Base)",
        "short_name": "1. WF Base",
        "rule_summary": "Solo mercados aprobados WF OOS (sin forzados) · Riesgo 1.5–2.5% · 4x · R:R >= 2.2",
        "file": "paper_base.json",
        "strict_wf_only": True,
        "extra_entry": False,
        "strategy_override": None,
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": None,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.5,
            max_risk_pct=2.5,
            max_leverage=4.0,
            max_total_open_risk_pct=7.5,
            min_rr=2.2,
            min_confidence=72.0,
            trail_atr_mult=1.5,
            max_net_crypto=2,
            max_open_positions=6,
            max_cluster_risk_pct=3.5,
        ),
    },
    {
        "key": "momentum",
        "name": "2. Régimen Híbrido (ADX)",
        "short_name": "2. Híbrido ADX",
        "rule_summary": "ADX >= 22 Chimichurri + ADX < 18 Resorte · Riesgo 1.5–2.5% · 4x · R:R >= 2.2",
        "file": "paper_momentum.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": "regime_hybrid",
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": None,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.5,
            max_risk_pct=2.5,
            max_leverage=4.0,
            max_total_open_risk_pct=7.5,
            min_rr=2.2,
            min_confidence=70.0,
            trail_atr_mult=1.5,
            max_net_crypto=2,
            max_open_positions=6,
            max_cluster_risk_pct=3.5,
        ),
    },
    {
        "key": "chimichurri",
        "name": "3. Chimichurri v2 (Tendencia)",
        "short_name": "3. Chimichurri",
        "rule_summary": "chimichurri_alpha (ADX >= 22) · TP >= 2.8R · Trail 1.8 ATR · Riesgo 1.5–2.5% · 4x",
        "file": "paper_chimichurri.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": "chimichurri_alpha",
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": 2.8,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.5,
            max_risk_pct=2.5,
            max_leverage=4.0,
            max_total_open_risk_pct=7.5,
            min_rr=2.4,
            min_confidence=72.0,
            trail_atr_mult=1.8,
            max_net_crypto=2,
            max_open_positions=6,
            max_cluster_risk_pct=3.5,
        ),
    },
    {
        "key": "sniper",
        "name": "4. Francotirador v2 (Macro 1H)",
        "short_name": "4. Francotirador",
        "rule_summary": "Conf >= 85 · Exige Macro 1H · TP >= 2.6R · Riesgo 1.0–1.8% · 3x · Bloque 3.0%",
        "file": "paper_sniper.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": None,
        "fallback_strategy": "chimichurri_alpha",
        "require_macro_align": True,
        "tp_rr_override": 2.6,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.0,
            max_risk_pct=1.8,
            max_leverage=3.0,
            max_leverage_forex=15.0,
            max_total_open_risk_pct=6.0,
            min_rr=2.4,
            min_confidence=85.0,
            trail_atr_mult=1.5,
            max_net_crypto=2,
            max_open_positions=5,
            max_cluster_risk_pct=3.0,
        ),
    },
    {
        "key": "berserker",
        "name": "5. Berserker v2 (Sin Impulso)",
        "short_name": "5. Berserker",
        "rule_summary": "Régimen Híbrido ADX · Riesgo 2.0–3.5% · Apal. 6x · Conf >= 70 · Trail 1.4 ATR",
        "file": "paper_berserker.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": "regime_hybrid",
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": None,
        "risk_cfg": RiskConfig(
            base_risk_pct=2.0,
            max_risk_pct=3.5,
            max_leverage=6.0,
            max_leverage_forex=25.0,
            max_total_open_risk_pct=10.5,
            min_rr=2.0,
            min_confidence=70.0,
            trail_atr_mult=1.4,
            max_net_crypto=3,
            max_open_positions=6,
            max_cluster_risk_pct=5.5,
        ),
    },
    {
        "key": "rebote",
        "name": "6. Resorte v2 (Rango ADX<20)",
        "short_name": "6. Resorte",
        "rule_summary": "bollinger_reversion filtrada por ADX < 20 (solo rango) · R:R >= 2.0 · 4x",
        "file": "paper_rebote.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": "bollinger_reversion",
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": None,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.5,
            max_risk_pct=2.5,
            max_leverage=4.0,
            max_total_open_risk_pct=7.5,
            min_rr=2.0,
            min_confidence=70.0,
            trail_atr_mult=1.5,
            max_net_crypto=2,
            max_open_positions=6,
            max_cluster_risk_pct=3.5,
        ),
    },
    {
        "key": "ruptura",
        "name": "7. Escape v2 (Ruptura+ADX)",
        "short_name": "7. Escape",
        "rule_summary": "volatility_breakout (ADX >= 20 + Volumen >= 1.1x) · TP >= 2.6R · 4x",
        "file": "paper_ruptura.json",
        "strict_wf_only": False,
        "extra_entry": False,
        "strategy_override": "volatility_breakout",
        "fallback_strategy": None,
        "require_macro_align": False,
        "tp_rr_override": 2.6,
        "risk_cfg": RiskConfig(
            base_risk_pct=1.5,
            max_risk_pct=2.5,
            max_leverage=4.0,
            max_total_open_risk_pct=7.5,
            min_rr=2.2,
            min_confidence=70.0,
            trail_atr_mult=1.5,
            max_net_crypto=2,
            max_open_positions=6,
            max_cluster_risk_pct=3.5,
        ),
    },
]

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
            "wf_approved": False,
            "strategy": "chimichurri_alpha",
            "sl_atr_mult": 1.6,
            "tp_atr_mult": 4.0,
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


def run_self_correction_phase(
    broker: LiveExecutionBroker,
    cfg: dict,
    interval: str = "15m",
    force: bool = False,
) -> dict:
    """
    Fase de auto-evaluación y validación Walk-Forward semanal (3 etapas: Train/Val/Test):
    1. Descarga hasta 2500 velas cerradas por mercado.
    2. Selecciona candidatos en Train (50%), elige uno en Validación (25%) y confirma en Test intocable (25%).
    3. Cachea resultados y rechazos por 7 días en wf_params.json.
    """
    log_event("🛠️ INICIANDO FASE DE VALIDACIÓN WALK-FORWARD (50% Train / 25% Val / 25% Test)...")
    corrections_made = 0

    for sym in DEFAULT_SYMBOLS:
        try:
            df_raw, source = get_historical_data(sym, interval=interval, limit=2500, use_cache=True, live_only=True)
            df_closed = closed_only(df_raw, interval)
            if df_closed is None or len(df_closed) < 800:
                if sym in CORE_SHARED_BASKET:
                    existing_sym = cfg["symbols"].get(sym, {})
                    kept_strat = str(existing_sym.get("strategy") or CORE_DEFAULT_STRATEGIES.get(sym, "chimichurri_alpha"))
                    cfg["symbols"][sym] = {
                        "enabled": True,
                        "wf_approved": False,
                        "strategy": kept_strat,
                        "sl_atr_mult": float(existing_sym.get("sl_atr_mult", 1.6)),
                        "tp_atr_mult": float(existing_sym.get("tp_atr_mult", 4.0)),
                        "ema_fast": int(existing_sym.get("ema_fast", 12)),
                        "ema_slow": int(existing_sym.get("ema_slow", 26)),
                        "data_source": source,
                        "notes": f"Canasta Compartida 7 Opciones ({kept_strat})",
                    }
                    log_event(f"   🔎 [{sym}] -> ACTIVO (Canasta Compartida, historial temporal {0 if df_closed is None else len(df_closed)})")
                    continue
                log_event(
                    f"   🔎 [{sym}] -> PAUSADO (Historial insuficiente: {0 if df_closed is None else len(df_closed)} velas cerradas)"
                )
                cfg["symbols"][sym] = {
                    "enabled": False,
                    "wf_approved": False,
                    "strategy": "chimichurri_alpha",
                    "sl_atr_mult": 1.6,
                    "tp_atr_mult": 4.0,
                    "ema_fast": 12,
                    "ema_slow": 26,
                    "data_source": source,
                    "notes": "Historial insuficiente para Walk-Forward",
                }
                continue

            wf_params = get_validated_params(sym, df_closed, force=force)
            prev_enabled = cfg["symbols"].get(sym, {}).get("enabled")
            prev_strat = cfg["symbols"].get(sym, {}).get("strategy", "chimichurri_alpha")

            if wf_params is None:
                if sym in CORE_SHARED_BASKET:
                    kept_strat = CORE_DEFAULT_STRATEGIES.get(sym, prev_strat or "chimichurri_alpha")
                    existing_sym = cfg["symbols"].get(sym, {})
                    cfg["symbols"][sym] = {
                        "enabled": True,
                        "wf_approved": False,
                        "strategy": kept_strat,
                        "sl_atr_mult": float(existing_sym.get("sl_atr_mult", 1.6)),
                        "tp_atr_mult": float(existing_sym.get("tp_atr_mult", 4.0)),
                        "ema_fast": int(existing_sym.get("ema_fast", 12)),
                        "ema_slow": int(existing_sym.get("ema_slow", 26)),
                        "data_source": source,
                        "notes": f"Canasta Compartida sin WF ({kept_strat}; excluido de 1. WF Base)",
                    }
                    if prev_enabled is not True:
                        corrections_made += 1
                    log_event(f"   🔎 [{sym}] -> ACTIVO (Canasta sin WF) | {kept_strat}")
                    continue
                if prev_enabled is not False:
                    corrections_made += 1
                cfg["symbols"][sym] = {
                    "enabled": False,
                    "wf_approved": False,
                    "strategy": prev_strat,
                    "sl_atr_mult": 1.6,
                    "tp_atr_mult": 4.0,
                    "ema_fast": 12,
                    "ema_slow": 26,
                    "data_source": source,
                    "notes": "RECHAZADO por Walk-Forward OOS (sin edge estadístico validado)",
                }
                log_event(f"   🔎 [{sym}] -> PAUSADO | Walk-Forward OOS rechazado")
                continue

            oos = wf_params.get("oos", {})
            new_strat = str(wf_params["strategy"])
            sl_mult = float(wf_params["sl_atr_mult"])
            tp_mult = float(wf_params["tp_atr_mult"])
            ema_f = int(wf_params["ema_fast"])
            ema_s = int(wf_params["ema_slow"])

            if prev_strat != new_strat or prev_enabled is not True:
                corrections_made += 1

            note = (
                f"WF OOS APROBADO ({new_strat}) | PF={oos.get('pf', 0):.2f}, "
                f"Exp={oos.get('expectancy', 0):+.2f}R, "
                f"WR={oos.get('win_rate', 0) * 100:.1f}%, Trades={oos.get('n', 0)}"
            )
            cfg["symbols"][sym] = {
                "enabled": True,
                "wf_approved": True,
                "strategy": new_strat,
                "sl_atr_mult": sl_mult,
                "tp_atr_mult": tp_mult,
                "ema_fast": ema_f,
                "ema_slow": ema_s,
                "backtest_return_pct": round(float(oos.get("expectancy", 0.0)) * float(oos.get("n", 0)), 2),
                "backtest_profit_factor": round(float(oos.get("pf", 0.0)), 2),
                "backtest_win_rate": round(float(oos.get("win_rate", 0.0)) * 100.0, 1),
                "data_source": source,
                "notes": note,
            }
            log_event(f"   🔎 [{sym}] -> ACTIVO | {note}")
        except Exception as exc:
            log_event(f"   ⚠️ Error calibrando {sym}: {exc}")

    cfg["corrections_applied"] = int(cfg.get("corrections_applied", 0)) + corrections_made
    save_adaptive_config(cfg)
    log_event(f"✅ FASE WALK-FORWARD COMPLETADA ({corrections_made} ajustes aplicados).")
    return cfg


def _get_macro_bias_1h(symbol: str) -> int:
    """Calcula el sesgo macro en velas cerradas de 1 hora: 1 = Alcista, -1 = Bajista, 0 = Neutro."""
    try:
        df_1h, _ = get_historical_data(symbol, interval="1h", limit=120, use_cache=False, live_only=True)
        df_1h = closed_only(df_1h, "1h")
        if df_1h is None or len(df_1h) < 50:
            return 0
        ind_1h = compute_indicators(df_1h, ema_fast=12, ema_slow=26)
        last = ind_1h.iloc[-1]
        close_1h = float(last["close"])
        ema_f = float(last["ema_fast"])
        ema_s = float(last["ema_slow"])
        macd_h = float(last["macd_hist"])
        if close_1h > ema_s and ema_f > ema_s and macd_h >= 0:
            return 1
        if close_1h < ema_s and ema_f < ema_s and macd_h <= 0:
            return -1
    except Exception:
        pass
    return 0


def run_trading_tick(
    broker: LiveExecutionBroker,
    cfg: dict,
    rm: RiskManager | None = None,
    interval: str = "15m",
    *,
    strict_wf_only: bool = False,
    extra_entry: bool = False,
    strategy_override: str | None = None,
    fallback_strategy: str | None = None,
    require_macro_align: bool = False,
    tp_rr_override: float | None = None,
    lock_rm_config: bool = False,
    history=get_historical_data,
    macro=_get_macro_bias_1h,
    report=log_event,
) -> dict[str, float]:
    """Evalúa señales al cierre de vela usando parámetros Walk-Forward y RiskManager."""
    rm = rm or RiskManager()
    if not lock_rm_config:
        rm.cfg = RiskConfig.from_adaptive_config()
    cfg = load_adaptive_config()
    atr_by_symbol: dict[str, float] = {}

    for sym, sym_cfg in cfg.get("symbols", {}).items():
        try:
            has_open = any(p["symbol"] == sym for p in broker.state.open_positions)
            is_wf_approved = bool(sym_cfg.get("wf_approved", False))
            if not sym_cfg.get("enabled", True) and not has_open:
                continue
            if strict_wf_only and not is_wf_approved and not has_open:
                continue

            df_raw, _ = history(sym, interval=interval, limit=300, use_cache=False, live_only=True)
            df_closed = closed_only(df_raw, interval)
            if df_closed is None or len(df_closed) < 60:
                continue

            df_ind = compute_indicators(
                df_closed,
                ema_fast=int(sym_cfg.get("ema_fast", 12)),
                ema_slow=int(sym_cfg.get("ema_slow", 26)),
            )
            if strategy_override:
                strat_name = strategy_override
            elif is_wf_approved:
                strat_name = str(sym_cfg.get("strategy", "chimichurri_alpha"))
            else:
                strat_name = fallback_strategy or str(sym_cfg.get("strategy", "chimichurri_alpha"))
            df_sig = generate_signals(df_ind, strategy_name=strat_name)
            atr_val = float(df_sig.iloc[-1]["atr"])
            atr_by_symbol[sym] = atr_val

            if not sym_cfg.get("enabled", True):
                continue
            if strict_wf_only and not is_wf_approved:
                continue

            macro_bias = macro(sym)
            sl_mult = float(sym_cfg.get("sl_atr_mult", 1.6))
            base_tp_mult = float(sym_cfg.get("tp_atr_mult", 4.0))
            target_rr = max(rm.cfg.min_rr, tp_rr_override or 2.0)
            tp_mult = max(base_tp_mult, round(sl_mult * target_rr * 1.04, 3))
            decision = evaluate_latest_candle(
                df_sig,
                sl_atr_mult=sl_mult,
                tp_atr_mult=tp_mult,
                lookback_bars=1,
                macro_bias=macro_bias,
            )

            if (
                extra_entry
                and not strategy_override
                and decision.action.startswith("HOLD")
                and not has_open
            ):
                strat_name = "momentum_extra"
                decision = evaluate_latest_candle(
                    generate_signals(df_ind, strategy_name="momentum_shark"),
                    sl_atr_mult=sl_mult,
                    tp_atr_mult=tp_mult,
                    lookback_bars=1,
                    macro_bias=macro_bias,
                )

            side = None
            if decision.action.startswith("LONG"):
                side = "LONG"
            elif decision.action.startswith("SHORT"):
                side = "SHORT"

            if require_macro_align and side is not None:
                macro_aligned = (side == "LONG" and macro_bias == 1) or (side == "SHORT" and macro_bias == -1)
                if not macro_aligned:
                    report(f"   🎯 [Francotirador] {sym} {side} descartado: sin alineación Macro 1H ({macro_bias})")
                    side = None

            if side is None:
                report(f"   {sym} | Esperando señal de {strat_name}")
            if side is not None:
                existing = next((p for p in broker.state.open_positions if p["symbol"] == sym), None)
                if (
                    existing
                    and existing["side"] != side
                    and decision.confidence >= 86
                    and existing.get("broker_mode", "").startswith("Paper Trading")
                ):
                    report(broker.close_position_by_symbol(sym, reason="Señal contraria confirmada"))
                    continue
                st = broker.state
                ok_rm, risk_rm, reason_rm = rm.decide(
                    equity=st.equity_usd,
                    peak_equity=st.peak_equity_usd,
                    day_start_equity=st.day_start_equity_usd,
                    open_positions=st.open_positions,
                    symbol=sym,
                    side=side,
                    entry=decision.entry_price,
                    sl=decision.stop_loss,
                    tp=decision.take_profit,
                    confidence=decision.confidence,
                    realized_peak_equity=st.realized_peak_equity_usd,
                    dd_halt_until_utc=st.dd_halt_until_utc,
                    dd_recovery_mode=st.dd_recovery_mode,
                    price_tick=estimate_price_tick(df_closed[["open", "high", "low", "close"]].to_numpy()),
                )
                if not ok_rm:
                    if reason_rm.startswith("FRENO_COOLDOWN"):
                        until = broker.activate_dd_cooldown(rm.cfg.dd_cooldown_hours)
                        report(f"   🛡️ [RiskManager] {sym} {side} bloqueado: {reason_rm} (hasta {until})")
                    else:
                        report(f"   🛡️ [RiskManager] {sym} {side} bloqueado: {reason_rm}")
                    continue

                ok, msg = broker.open_order(
                    symbol=sym,
                    side=side,
                    strategy=("momentum_base" if strat_name == "momentum_shark" else strat_name),
                    entry_price=decision.entry_price,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    risk_pct=risk_rm,
                    reason=decision.reasons[0] if decision.reasons else reason_rm,
                    confidence=decision.confidence,
                )
                report(f"{sym} | {msg} | {reason_rm}")
        except Exception as exc:
            report(f"   ⚠️ Error en tick de {sym}: {exc}")

    events = broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    for ev in events:
        report(ev)

    st = broker.state
    report(
        f"💓 Cierre de Vela | Equity: ${st.equity_usd:,.2f} USD "
        f"(Efectivo: ${st.cash_balance_usd:,.2f} | Flotante: ${st.unrealized_pnl_usd:+,.2f} | "
        f"Peak: ${st.peak_equity_usd:,.2f}) | Abiertas: {len(st.open_positions)} | Cerradas: {st.total_trades}"
    )
    return atr_by_symbol


def build_option_runners(
    base_dir: Path | None = None,
    shared_quote=None,
) -> tuple[list[tuple[dict, LiveExecutionBroker, RiskManager]], object]:
    """Construye los 7 brokers y sus RiskManagers compartiendo un proveedor de precios al segundo."""
    root = base_dir or Path(__file__).parent
    runners: list[tuple[dict, LiveExecutionBroker, RiskManager]] = []
    quote_fn = shared_quote
    path_fn = None
    for spec in OPTION_SPECS:
        broker = LiveExecutionBroker(
            initial_balance_usd=10000.0,
            portfolio_file=root / spec["file"],
            paper_only=True,
            risk_config=spec["risk_cfg"],
        )
        if quote_fn is None:
            quote_fn = lru_cache(maxsize=None)(broker.get_live_price)
        if path_fn is None:
            path_fn = shared_path_provider(broker.get_price_path)
        broker.get_live_price = quote_fn
        broker.get_price_path = path_fn
        rm = RiskManager(spec["risk_cfg"])
        runners.append((spec, broker, rm))
    return runners, quote_fn


def sync_base_to_live_portfolio(base_broker: LiveExecutionBroker, live_file: Path = PORTFOLIO_FILE) -> None:
    """Mantiene live_portfolio.json sincronizado como espejo de la Opción 1 (Base)."""
    from dataclasses import asdict

    live_file.write_text(json.dumps(asdict(base_broker.state), indent=2, ensure_ascii=False), encoding="utf-8")


def reset_all_portfolios(initial_balance_usd: float = 10000.0, base_dir: Path | None = None) -> None:
    """Reinicia las 7 carteras, live_portfolio.json e impulse_study.json a cero ($10,000 USD)."""
    root = base_dir or Path(__file__).parent
    for spec in OPTION_SPECS:
        b = LiveExecutionBroker(
            initial_balance_usd=initial_balance_usd,
            portfolio_file=root / spec["file"],
            paper_only=True,
            risk_config=spec["risk_cfg"],
        )
        b.reset_portfolio(initial_balance_usd=initial_balance_usd)
    live_b = LiveExecutionBroker(initial_balance_usd=initial_balance_usd, portfolio_file=root / "live_portfolio.json")
    live_b.reset_portfolio(initial_balance_usd=initial_balance_usd)
    study_path = root / "impulse_study.json"
    study_path.write_text(json.dumps({"controls": {}}, indent=2, ensure_ascii=False), encoding="utf-8")


def run_all_options_tick(
    runners: list[tuple[dict, LiveExecutionBroker, RiskManager]],
    cfg: dict,
    interval: str = "15m",
    *,
    history=get_historical_data,
    macro=_get_macro_bias_1h,
    report=log_event,
) -> dict[str, float]:
    """Ejecuta un cierre de vela sincronizado para las 7 opciones sobre los mismos datos."""
    cached_hist = lru_cache(maxsize=None)(history)
    cached_macro = lru_cache(maxsize=None)(macro)
    atr_map: dict[str, float] = {}

    for idx, (spec, broker, rm) in enumerate(runners):
        prefix = spec["short_name"]

        def opt_report(message: str, _idx: int = idx, _prefix: str = prefix) -> None:
            if _idx == 0:
                report(message)
            elif "Esperando señal de" not in message and not message.startswith("💓"):
                report(f"[{_prefix}] {message.strip()}")

        tick_atr = run_trading_tick(
            broker,
            cfg,
            rm=rm,
            interval=interval,
            strict_wf_only=bool(spec.get("strict_wf_only", False)),
            extra_entry=bool(spec.get("extra_entry", False)),
            strategy_override=spec.get("strategy_override"),
            fallback_strategy=spec.get("fallback_strategy"),
            require_macro_align=bool(spec.get("require_macro_align", False)),
            tp_rr_override=spec.get("tp_rr_override"),
            lock_rm_config=True,
            history=cached_hist,
            macro=cached_macro,
            report=opt_report,
        )
        # El ATR depende solo del mercado: juntar el de todas las opciones. Antes se usaba solo el mapa
        # de la primera opción con datos (la Base, que salta los mercados sin WF), y el trailing de las
        # demás opciones en esos mercados caía a un ATR estimado viejo entre cierres de vela.
        atr_map.update(tick_atr or {})

    if runners:
        sync_base_to_live_portfolio(runners[0][1])

    board = " | ".join(
        f"{spec['short_name']}: ${b.state.equity_usd:,.2f} ({len(b.state.open_positions)}A/{b.state.total_trades}C)"
        for spec, b, _ in runners
    )
    report(f"📊 Marcador 7 Opciones | {board}")
    return atr_map


def run_overnight_daemon(
    trading_minutes: int = 60,
    correction_minutes: int = 20,
    tick_seconds: int = 60,
) -> None:
    runners, quote_fn = build_option_runners()
    base_broker = runners[0][1]
    gate = NewCandleGate("15m")
    cfg = load_adaptive_config()
    atr_map: dict[str, float] = {}

    log_event("=" * 92)
    log_event(
        f"🌙 INICIANDO SUPERVISOR 7 OPCIONES EN PARALELO (Fase 2 + WF v3 + RiskManager): "
        f"{trading_minutes} min Trading + {correction_minutes} min Auditoría | Tick SL/Trail: {tick_seconds}s"
    )
    log_event("=" * 92)

    cfg = run_self_correction_phase(base_broker, cfg, interval="15m")

    while True:
        try:
            cfg["session_count"] = int(cfg.get("session_count", 0)) + 1
            save_adaptive_config(cfg)
            session_num = cfg["session_count"]

            log_event(f"🟢 SESIÓN OPERATIVA #{session_num} INICIADA (7 Opciones sobre la misma canasta, {trading_minutes} min)...")
            session_end = time.time() + (trading_minutes * 60)
            first_tick = True

            while time.time() < session_end:
                if hasattr(quote_fn, "cache_clear"):
                    quote_fn.cache_clear()
                if first_tick or gate.is_new_closed_candle():
                    atr_map = run_all_options_tick(runners, cfg, interval="15m")
                    first_tick = False
                else:
                    for spec, b, _ in runners:
                        events = b.update_open_positions(atr_by_symbol=atr_map)
                        for ev in events:
                            log_event(ev if spec["key"] == "base" else f"[{spec['short_name']}] {ev}")
                    sync_base_to_live_portfolio(base_broker)
                time.sleep(tick_seconds)

            log_event(
                f"🔧 ENTRANDO EN VENTANA DE {correction_minutes} MINUTOS DE AUDITORÍA, CORRECCIÓN Y RECALIBRACIÓN..."
            )
            correction_end = time.time() + (correction_minutes * 60)
            cfg = run_self_correction_phase(base_broker, cfg, interval="15m")

            while time.time() < correction_end:
                if hasattr(quote_fn, "cache_clear"):
                    quote_fn.cache_clear()
                for spec, b, _ in runners:
                    events = b.update_open_positions(atr_by_symbol=atr_map)
                    for ev in events:
                        log_event(ev if spec["key"] == "base" else f"[{spec['short_name']}] {ev}")
                sync_base_to_live_portfolio(base_broker)
                time.sleep(tick_seconds)

        except Exception as exc:
            log_event(f"❌ Error recuperable en bucle nocturno: {exc}\n{traceback.format_exc()}")
            time.sleep(30)


if __name__ == "__main__":
    run_overnight_daemon(trading_minutes=60, correction_minutes=20, tick_seconds=60)
