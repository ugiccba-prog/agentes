"""
Agente de Trading Cuantitativo Multi-Mercado (Cripto + Forex).
Ejecuta:
1. Descarga de datos reales (Binance API para Cripto + Yahoo Finance API para Forex)
2. Auto-Optimización y Backtesting con Gestión de Riesgo Dinámica (ATR Stop Loss, Take Profit, Trailing Stop)
3. Escáner de Señales en Tiempo Real con niveles exactos de Entrada, Stop Loss y Take Profit
4. Exportación de historial de operaciones y reporte en JSON/CSV
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

from backtester import BacktestResult, optimize_for_symbol, run_backtest
from indicators import compute_indicators
from market_data import DEFAULT_SYMBOLS, get_historical_data, get_market_type
from strategies import evaluate_latest_candle, generate_signals


def format_price(price: float, symbol: str) -> str:
    market = get_market_type(symbol)
    if market == "forex":
        return f"{price:.3f}" if "JPY" in symbol else f"{price:.5f}"
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.3f}"
    return f"${price:.5f}"


def run_multi_market_suite(
    symbols: list[str],
    interval: str = "1h",
    limit: int = 750,
    initial_capital_per_pair: float = 1000.0,
    risk_pct: float = 2.0,
) -> dict[str, object]:
    print("=" * 98)
    print(" 🤖 AGENTE DE TRADING CUANTITATIVO (CRIPTO + FOREX) — BACKTEST & ESCÁNER DE SEÑALES")
    print("=" * 98)
    print(
        f" Temporalidad: {interval} | Velas por par: {limit} | "
        f"Capital por mercado: ${initial_capital_per_pair:,.2f} USD | Riesgo/Trade: {risk_pct:.1f}%"
    )
    print("-" * 98)

    summary_rows: list[dict[str, object]] = []
    all_trades_rows: list[dict[str, object]] = []
    live_signals: list[dict[str, object]] = []

    total_initial = 0.0
    total_final = 0.0

    for sym in symbols:
        df_raw, source = get_historical_data(sym, interval=interval, limit=limit)
        best_res, _ = optimize_for_symbol(
            df_raw=df_raw,
            symbol=sym,
            initial_capital=initial_capital_per_pair,
            risk_per_trade_pct=risk_pct,
        )

        total_initial += best_res.initial_capital
        total_final += best_res.final_capital

        # Calcular señal actual en la última vela usando la mejor configuración de ese activo
        p = best_res.params
        df_ind = compute_indicators(
            df_raw,
            ema_fast=int(p["ema_fast"]),
            ema_slow=int(p["ema_slow"]),
        )
        df_sig = generate_signals(df_ind, strategy_name=str(p["strategy"]))
        decision = evaluate_latest_candle(
            df_sig,
            sl_atr_mult=float(p["sl_atr_mult"]),
            tp_atr_mult=float(p["tp_atr_mult"]),
        )

        summary_rows.append(
            {
                "Par": sym,
                "Mercado": get_market_type(sym).upper(),
                "Fuente": source,
                "Mejor Estrategia": best_res.strategy,
                "Capital Inicial": f"${best_res.initial_capital:,.2f}",
                "Capital Final": f"${best_res.final_capital:,.2f}",
                "Ganancia ($)": f"${best_res.net_profit_usd:+,.2f}",
                "Retorno (%)": f"{best_res.total_return_pct:+.2f}%",
                "Trades": best_res.total_trades,
                "Win Rate": f"{best_res.win_rate_pct:.1f}%",
                "Profit Factor": f"{best_res.profit_factor:.2f}",
                "Max Drawdown": f"-{best_res.max_drawdown_pct:.2f}%",
            }
        )

        live_signals.append(
            {
                "symbol": sym,
                "market": get_market_type(sym).upper(),
                "strategy": best_res.strategy,
                "action": decision.action,
                "confidence": round(decision.confidence, 1),
                "entry": format_price(decision.entry_price, sym),
                "stop_loss": format_price(decision.stop_loss, sym),
                "take_profit": format_price(decision.take_profit, sym),
                "reason": decision.reasons[0] if decision.reasons else "",
            }
        )

        for t in best_res.trades:
            all_trades_rows.append(
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "side": t.side,
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "entry_price": round(t.entry_price, 6),
                    "exit_price": round(t.exit_price, 6),
                    "pnl_usd": round(t.pnl_usd, 2),
                    "pnl_pct_account": round(t.pnl_pct_account, 2),
                    "exit_reason": t.exit_reason,
                    "signal_reason": t.signal_reason,
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    print("\n📊 RESULTADOS DEL BACKTEST AUTOMATIZADO POR MONEDA:")
    print(summary_df.to_string(index=False))

    portfolio_profit = total_final - total_initial
    portfolio_ret_pct = ((total_final - total_initial) / total_initial * 100.0) if total_initial > 0 else 0.0

    print("\n" + "=" * 98)
    print(
        f" 💰 RESUMEN GLOBAL DE LA CARTERA: Capital Inicial: ${total_initial:,.2f} USD  -->  "
        f"Capital Final: ${total_final:,.2f} USD  ({portfolio_ret_pct:+.2f}% | ${portfolio_profit:+,.2f} USD)"
    )
    print("=" * 98)

    print("\n🎯 SEÑALES OPERATIVAS EN VIVO (ÚLTIMA VELA DEL MERCADO):")
    for sig in live_signals:
        print(
            f" • [{sig['market']}] {sig['symbol']:<9} | {sig['action']:<16} | "
            f"Entrada: {sig['entry']:<11} | SL: {sig['stop_loss']:<11} | TP: {sig['take_profit']:<11} | "
            f"Estrategia: {sig['strategy']}"
        )
        print(f"   └─ Motivo: {sig['reason']}")

    # Guardar archivos de auditoría
    trades_df = pd.DataFrame(all_trades_rows)
    trades_path = Path(__file__).parent / "trades_history.csv"
    trades_df.to_csv(trades_path, index=False)

    report_payload = {
        "interval": interval,
        "total_initial_usd": round(total_initial, 2),
        "total_final_usd": round(total_final, 2),
        "net_profit_usd": round(portfolio_profit, 2),
        "portfolio_return_pct": round(portfolio_ret_pct, 2),
        "summary": summary_rows,
        "live_signals": live_signals,
    }
    report_path = Path(__file__).parent / "agent_report.json"
    report_path.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n✅ Historial detallado de {len(all_trades_rows)} operaciones guardado en: {trades_path.name}")
    print(f"✅ Reporte completo del agente guardado en: {report_path.name}\n")
    return report_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Agente de Trading Cuantitativo (Cripto + Forex)")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC/USDT", "ETH/USDT", "SOL/USDT", "EUR/USD", "GBP/USD", "USD/JPY"],
        help="Lista de pares a analizar y operar (ej. BTC/USDT EUR/USD)",
    )
    parser.add_argument("--interval", default="1h", choices=["15m", "1h", "4h", "1d"], help="Temporalidad de las velas")
    parser.add_argument("--limit", type=int, default=750, help="Cantidad de velas históricas para el backtest")
    parser.add_argument("--capital", type=float, default=1000.0, help="Capital inicial en USD por par")
    parser.add_argument("--risk", type=float, default=2.0, help="Porcentaje de riesgo por operación (ej. 2.0)")
    args = parser.parse_args()

    run_multi_market_suite(
        symbols=args.symbols,
        interval=args.interval,
        limit=args.limit,
        initial_capital_per_pair=args.capital,
        risk_pct=args.risk,
    )


if __name__ == "__main__":
    main()
