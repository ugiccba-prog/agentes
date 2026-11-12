"""
Motor Operativo en Tiempo Real (Paper Trading Persistente + Binance Testnet + OANDA Demo).
Ejecuta ciclos en vivo:
1. Actualiza precios en tiempo real (Binance API y Yahoo Finance API)
2. Gestiona posiciones abiertas (ejecuta Stop Loss, Take Profit y sube/baja el Trailing Stop)
3. Optimiza y evalúa nuevas señales en vivo y abre órdenes automáticamente con gestión de riesgo
"""

from __future__ import annotations

import argparse
import time
import pandas as pd

from backtester import optimize_for_symbol
from exchange_connector import LiveExecutionBroker, PORTFOLIO_FILE
from indicators import compute_indicators
from market_data import DEFAULT_SYMBOLS, get_historical_data
from strategies import evaluate_latest_candle, generate_signals


def execute_live_cycle(
    broker: LiveExecutionBroker,
    symbols: list[str],
    interval: str = "15m",
    risk_pct: float = 2.0,
) -> dict:
    print("\n" + "=" * 98)
    print(f" ⚡ CICLO DE EJECUCIÓN EN VIVO ({interval}) — {broker.state.last_updated}")
    print("=" * 98)

    atr_by_symbol: dict[str, float] = {}
    decisions_summary: list[dict] = []

    for sym in symbols:
        df_raw, source = get_historical_data(sym, interval=interval, limit=400, use_cache=False)
        best_res, _ = optimize_for_symbol(
            df_raw=df_raw,
            symbol=sym,
            initial_capital=1000.0,
            risk_per_trade_pct=risk_pct,
        )
        p = best_res.params
        df_ind = compute_indicators(df_raw, ema_fast=int(p["ema_fast"]), ema_slow=int(p["ema_slow"]))
        df_sig = generate_signals(df_ind, strategy_name=str(p["strategy"]))
        atr_by_symbol[sym] = float(df_sig["atr"].iloc[-1])

        decision = evaluate_latest_candle(
            df_sig,
            sl_atr_mult=float(p["sl_atr_mult"]),
            tp_atr_mult=float(p["tp_atr_mult"]),
        )
        decisions_summary.append(
            {
                "symbol": sym,
                "source": source,
                "strategy": best_res.strategy,
                "decision": decision,
            }
        )

    # 1. Actualizar posiciones existentes primero (Stop Loss / Take Profit / Trailing Stop)
    update_events = broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    for ev in update_events:
        print(f"   {ev}")

    # 2. Abrir nuevas órdenes en vivo donde haya señal activa
    opened_events: list[str] = []
    for item in decisions_summary:
        sym = item["symbol"]
        dec = item["decision"]
        strat = item["strategy"]
        if dec.action.startswith("LONG"):
            ok, msg = broker.open_order(
                symbol=sym,
                side="LONG",
                strategy=strat,
                entry_price=dec.entry_price,
                stop_loss=dec.stop_loss,
                take_profit=dec.take_profit,
                risk_pct=risk_pct,
                reason=dec.reasons[0] if dec.reasons else "",
            )
            if ok:
                opened_events.append(msg)
                print(f"   {msg}")
        elif dec.action.startswith("SHORT"):
            ok, msg = broker.open_order(
                symbol=sym,
                side="SHORT",
                strategy=strat,
                entry_price=dec.entry_price,
                stop_loss=dec.stop_loss,
                take_profit=dec.take_profit,
                risk_pct=risk_pct,
                reason=dec.reasons[0] if dec.reasons else "",
            )
            if ok:
                opened_events.append(msg)
                print(f"   {msg}")

    # 3. Actualizar valoración de la cartera tras posibles aperturas
    broker.update_open_positions(atr_by_symbol=atr_by_symbol)
    st = broker.state

    print("-" * 98)
    print(
        f" 💼 ESTADO DE CUENTA DEMO / TESTNET:\n"
        f"    • Saldo Inicial:   ${st.initial_balance_usd:,.2f} USD\n"
        f"    • Saldo Cerrado:   ${st.cash_balance_usd:,.2f} USD (PnL Realizado: ${st.realized_pnl_usd:+,.2f} USD)\n"
        f"    • Equity en Vivo:  ${st.equity_usd:,.2f} USD (PnL Flotante: ${st.unrealized_pnl_usd:+,.2f} USD)\n"
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
    parser.add_argument("--risk", type=float, default=2.0, help="Porcentaje de riesgo por operacion (ej. 2.0)")
    parser.add_argument("--reset", action="store_true", help="Reiniciar la cuenta Demo a su saldo inicial")
    parser.add_argument("--loop", action="store_true", help="Mantener al agente operando en bucle continuo")
    parser.add_argument("--sleep", type=int, default=60, help="Segundos entre cada escaneo cuando --loop está activo")
    args = parser.parse_args()

    broker = LiveExecutionBroker(initial_balance_usd=args.balance)
    if args.reset:
        broker.reset_portfolio(initial_balance_usd=args.balance)
        print(f"🔄 Cuenta Demo reiniciada a ${args.balance:,.2f} USD.")

    if args.loop:
        print(f"🟢 Iniciando bucle autónomo cada {args.sleep} segundos (Presiona Ctrl+C para detener)...")
        try:
            while True:
                execute_live_cycle(broker, symbols=args.symbols, interval=args.interval, risk_pct=args.risk)
                time.sleep(args.sleep)
        except KeyboardInterrupt:
            print("\n🛑 Bucle en vivo detenido por el usuario. Tus posiciones quedan guardadas en live_portfolio.json.")
    else:
        execute_live_cycle(broker, symbols=args.symbols, interval=args.interval, risk_pct=args.risk)


if __name__ == "__main__":
    main()
