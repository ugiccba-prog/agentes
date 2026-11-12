"""
Motor de Investigación Profunda de Mercados (Market Alpha & Profitability Scanner).
Escanea los 18 mercados (Cripto Principales, Altcoins Explosivas, Memecoins, Oro XAU/USD y Pares Forex Volátiles)
en modo agresivo (4.0% de riesgo por operación) para descubrir cuáles dan más ganancia real.
"""

from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

from backtester import optimize_for_symbol
from market_data import DEFAULT_SYMBOLS, get_historical_data

RANKING_FILE = Path(__file__).parent / "top_markets_ranking.json"


def run_deep_market_research(
    interval: str = "1h",
    limit: int = 750,
    initial_capital: float = 1000.0,
    aggressive_risk_pct: float = 4.0,
) -> pd.DataFrame:
    print("=" * 112)
    print(
        f" 🦈 INVESTIGACIÓN PROFUNDA DE MERCADOS (MODO AGRESIVO: {aggressive_risk_pct:.1f}% Riesgo/Trade | Temporalidad: {interval})"
    )
    print("=" * 112)

    rows: list[dict] = []
    for sym, meta in DEFAULT_SYMBOLS.items():
        df_raw, source = get_historical_data(sym, interval=interval, limit=limit)
        if df_raw is None or df_raw.empty or len(df_raw) < 60:
            continue

        best_res, _ = optimize_for_symbol(
            df_raw=df_raw,
            symbol=sym,
            initial_capital=initial_capital,
            risk_per_trade_pct=aggressive_risk_pct,
        )

        # Calcular volatilidad media por vela (%)
        avg_candle_move_pct = float(((df_raw["high"] - df_raw["low"]) / df_raw["close"] * 100.0).mean())

        rows.append(
            {
                "symbol": sym,
                "name": meta["name"],
                "market": meta["market"].upper(),
                "source": source,
                "best_strategy": best_res.strategy,
                "initial_usd": initial_capital,
                "final_usd": round(best_res.final_capital, 2),
                "net_profit_usd": round(best_res.net_profit_usd, 2),
                "return_pct": round(best_res.total_return_pct, 2),
                "buy_hold_pct": round(best_res.buy_and_hold_pct, 2),
                "trades": best_res.total_trades,
                "win_rate_pct": round(best_res.win_rate_pct, 1),
                "profit_factor": round(best_res.profit_factor, 2),
                "max_dd_pct": round(best_res.max_drawdown_pct, 2),
                "volatility_pct": round(avg_candle_move_pct, 3),
                "sl_atr": best_res.params["sl_atr_mult"],
                "tp_atr": best_res.params["tp_atr_mult"],
                "ema_fast": best_res.params["ema_fast"],
                "ema_slow": best_res.params["ema_slow"],
            }
        )

    df_rank = pd.DataFrame(rows).sort_values(by="return_pct", ascending=False).reset_index(drop=True)
    df_rank.index = df_rank.index + 1
    df_rank.index.name = "Rank"

    display_df = pd.DataFrame(
        [
            {
                "#": idx,
                "Par": r["symbol"],
                "Activo": r["name"][:26],
                "Mercado": r["market"],
                "Estrategia Ganadora": r["best_strategy"],
                "Ganancia ($1k)": f"${r['net_profit_usd']:+,.2f}",
                "Retorno (%)": f"{r['return_pct']:+.2f}%",
                "Win Rate": f"{r['win_rate_pct']:.1f}%",
                "Profit Factor": f"{r['profit_factor']:.2f}",
                "Max DD": f"-{r['max_dd_pct']:.1f}%",
                "Trades": r["trades"],
                "Volatilidad/Vela": f"{r['volatility_pct']:.2f}%",
            }
            for idx, r in df_rank.iterrows()
        ]
    )

    print("\n🏆 RANKING DE LOS 18 MERCADOS DEL MÁS RENTABLE AL MENOS RENTABLE (DATOS EN VIVO):")
    print(display_df.to_string(index=False))

    top8 = df_rank.head(8)
    top8_initial = float(top8["initial_usd"].sum())
    top8_final = float(top8["final_usd"].sum())
    top8_profit = top8_final - top8_initial
    top8_ret = (top8_profit / top8_initial * 100.0) if top8_initial > 0 else 0.0

    print("\n" + "=" * 112)
    print(
        f" 🔥 CARTERA ÉLITE 'MODO TIBURÓN' (CONCENTRANDO CAPITAL EN EL TOP 8 DE MERCADOS MÁS RENTABLES):\n"
        f"    • Capital Inicial ($1,000 x 8): ${top8_initial:,.2f} USD  -->  Capital Final: ${top8_final:,.2f} USD\n"
        f"    • Ganancia Neta Total:          ${top8_profit:+,.2f} USD ({top8_ret:+.2f}% en ~31 días)"
    )
    print("=" * 112)

    RANKING_FILE.write_text(
        json.dumps(
            {
                "interval": interval,
                "aggressive_risk_pct": aggressive_risk_pct,
                "top8_return_pct": round(top8_ret, 2),
                "top8_profit_usd": round(top8_profit, 2),
                "ranking": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return df_rank


if __name__ == "__main__":
    run_deep_market_research(interval="1h", limit=750, initial_capital=1000.0, aggressive_risk_pct=4.0)
