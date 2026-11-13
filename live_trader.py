"""
Motor Operativo en Tiempo Real (Paper Trading Persistente + Binance Testnet + OANDA Demo).
Integra:
1. `walk_forward.py`: Parámetros validados fuera de muestra (OOS) cacheados por 7 días.
2. `candle_clock.py`: Evalúa señales UNA vez por vela cerrada (`NewCandleGate` + `closed_only`),
   mientras revisa Stop Loss / Breakeven / Trailing en cada pulso.
3. `risk_manager.py`: Sizing dinámico (1%-2% por confianza), límites duros de riesgo total (8%),
   apalancamiento máximo (3x), exposición por moneda y frenos por drawdown / pérdida diaria.
"""

from __future__ import annotations

import argparse
import time
import pandas as pd

from candle_clock import NewCandleGate, closed_only
from exchange_connector import LiveExecutionBroker, PORTFOLIO_FILE
from indicators import compute_indicators
from market_data import DEFAULT_SYMBOLS, get_historical_data
from risk_manager import RiskManager
from strategies import evaluate_latest_candle, generate_signals
from walk_forward import get_validated_params


def execute_live_cycle(
    broker: LiveExecutionBroker,
    symbols: list[str],
    interval: str = "15m",
    rm: RiskManager | None = None,
) -> dict:
    rm = rm or RiskManager()
    print("\n" + "=" * 98)
    print(f" ⚡ CICLO DE EJECUCIÓN EN VIVO ({interval}) — {broker.state.last_updated}")
    print("=" * 98)

    atr_by_symbol: dict[str, float] = {}
    decisions_summary: list[dict] = []
    open_syms = {p["symbol"] for p in broker.state.open_positions}

    for sym in symbols:
        df_raw, source = get_historical_data(sym, interval=interval, limit=1500, use_cache=True)
        df_closed = closed_only(df_raw, interval)
        if df_closed is None or len(df_closed) < 40:
            continue

        p = get_validated_params(sym, df_closed)
        if p is None:
            # Si hay una posición previa abierta en este símbolo, mantenemos su ATR actualizado para trailing
            if sym in open_syms:
                df_tmp = compute_indicators(df_closed)
                atr_by_symbol[sym] = float(df_tmp["atr"].iloc[-1])
            continue

        df_ind = compute_indicators(df_closed, ema_fast=int(p["ema_fast"]), ema_slow=int(p["ema_slow"]))
        df_sig = generate_signals(df_ind, strategy_name=str(p["strategy"]))
        atr_by_symbol[sym] = float(df_sig["atr"].iloc[-1])

        decision = evaluate_latest_candle(
            df_sig,
            sl_atr_mult=float(p["sl_atr_mult"]),
            tp_atr_mult=float(p["tp_atr_mult"]),
            lookback_bars=1,
        )
        decisions_summary.append(
            {
                "symbol": sym,
                "source": source,
                "strategy": str(p["strategy"]),
                "decision": decision,
            }
        )

    # 1. Actualizar posiciones existentes primero (Breakeven +1R / Trailing 1.5 ATR / SL / TP)
    update_events = broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    for ev in update_events:
        print(f"   {ev}")

    # 2. Evaluar entradas nuevas con RiskManager antes de abrir orden
    opened_events: list[str] = []
    for item in decisions_summary:
        sym = item["symbol"]
        dec = item["decision"]
        strat = item["strategy"]
        if not (dec.action.startswith("LONG") or dec.action.startswith("SHORT")):
            continue

        side = "LONG" if dec.action.startswith("LONG") else "SHORT"
        st = broker.state
        ok_rm, risk_rm, why = rm.decide(
            equity=st.equity_usd,
            peak_equity=st.peak_equity_usd,
            day_start_equity=st.day_start_equity_usd,
            open_positions=st.open_positions,
            symbol=sym,
            side=side,
            entry=dec.entry_price,
            sl=dec.stop_loss,
            tp=dec.take_profit,
            confidence=dec.confidence,
        )
        if ok_rm:
            ok, msg = broker.open_order(
                symbol=sym,
                side=side,
                strategy=strat,
                entry_price=dec.entry_price,
                stop_loss=dec.stop_loss,
                take_profit=dec.take_profit,
                risk_pct=risk_rm,
                reason=dec.reasons[0] if dec.reasons else "",
                confidence=dec.confidence,
            )
            if ok:
                opened_events.append(msg)
                print(f"   {msg}")
        else:
            print(f"   ⛔ {sym} ({side}): {why}")

    # 3. Actualizar valoración final de la cartera
    broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    st = broker.state

    print("-" * 98)
    print(
        f" 💼 ESTADO DE CUENTA DEMO / TESTNET:\n"
        f"    • Saldo Inicial:   ${st.initial_balance_usd:,.2f} USD\n"
        f"    • Saldo Cerrado:   ${st.cash_balance_usd:,.2f} USD (PnL Realizado: ${st.realized_pnl_usd:+,.2f} USD)\n"
        f"    • Equity en Vivo:  ${st.equity_usd:,.2f} USD (Pico: ${st.peak_equity_usd:,.2f} | Inicio Día: ${st.day_start_equity_usd:,.2f})\n"
        f"    • Posiciones Abiertas: {len(st.open_positions)} | Trades Cerrados: {st.total_trades}"
    )
    print("-" * 98)

    if st.open_positions:
        print("\n📌 POSICIONES ABIERTAS EN TIEMPO REAL:")
        pos_df = pd.DataFrame(
            [
                {
                    "ID": p["id"],
                    "Par": p["symbol"],
                    "Mercado": p["market"],
                    "Lado": p["side"],
                    "Estrategia": p["strategy"],
                    "Entrada": p["entry_price"],
                    "Precio Actual": p["current_price"],
                    "Stop Loss": p["stop_loss"],
                    "Take Profit": p["take_profit"],
                    "Exposición ($)": f"${p['notional_usd']:,.2f}",
                    "PnL ($)": f"${p['unrealized_pnl_usd']:+.2f}",
                    "PnL (%)": f"{p['unrealized_pnl_pct']:+.2f}%",
                    "Modo": p["broker_mode"],
                }
                for p in st.open_positions
            ]
        )
        print(pos_df.to_string(index=False))
    else:
        print("\n📌 No hay posiciones abiertas en este instante.")

    print(f"\n💾 Cartera en vivo guardada en: {PORTFOLIO_FILE.name}\n")
    return {
        "events": update_events + opened_events,
        "state": st,
        "atr_by_symbol": atr_by_symbol,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ejecutor en Tiempo Real del Agente de Trading (Demo / Testnet)")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS.keys()),
        help="Pares a operar en tiempo real",
    )
    parser.add_argument("--interval", default="15m", choices=["15m", "1h", "4h"], help="Temporalidad operativa")
    parser.add_argument("--balance", type=float, default=10000.0, help="Saldo inicial de la cuenta Demo ($ USD)")
    parser.add_argument("--reset", action="store_true", help="Reiniciar la cuenta Demo a su saldo inicial")
    parser.add_argument("--loop", action="store_true", help="Mantener al agente operando en bucle continuo")
    parser.add_argument("--sleep", type=int, default=60, help="Segundos entre cada escaneo cuando --loop está activo")
    args = parser.parse_args()

    broker = LiveExecutionBroker(initial_balance_usd=args.balance)
    if args.reset:
        broker.reset_portfolio(initial_balance_usd=args.balance)
        print(f"🔄 Cuenta Demo reiniciada a ${args.balance:,.2f} USD.")

    rm = RiskManager()

    if args.loop:
        gate = NewCandleGate(args.interval)
        print(
            f"🟢 Iniciando bucle autónomo ({args.interval}): revisión de stops cada {args.sleep}s "
            f"y nuevas señales solo al cierre de vela (próximo cierre en {gate.seconds_to_next_close():.0f}s)..."
        )
        last_res = execute_live_cycle(broker, symbols=args.symbols, interval=args.interval, rm=rm)
        atr_map = last_res.get("atr_by_symbol", {})
        try:
            while True:
                time.sleep(args.sleep)
                # 1) Siempre: gestionar posiciones abiertas con precio fresco
                events = broker.update_open_positions(atr_by_symbol=atr_map)
                for ev in events:
                    print(f"   {ev}")
                # 2) Solo si cerró una vela nueva: buscar entradas
                if gate.is_new_closed_candle():
                    last_res = execute_live_cycle(broker, symbols=args.symbols, interval=args.interval, rm=rm)
                    atr_map = last_res.get("atr_by_symbol", {})
        except KeyboardInterrupt:
            print("\n🛑 Bucle en vivo detenido por el usuario. Tus posiciones quedan guardadas en live_portfolio.json.")
    else:
        execute_live_cycle(broker, symbols=args.symbols, interval=args.interval, rm=rm)


if __name__ == "__main__":
    main()
