"""
Panel Visual Interactivo (Streamlit) para el Agente de Trading (Cripto + Forex).
Incluye:
- Pestaña 1: ⚡ Operativa en Vivo (Cuenta Demo / Testnet con precios reales al segundo)
- Pestaña 2: 📊 Laboratorio de Backtesting y Auto-Optimización
Ejecutar con: streamlit run dashboard.py
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from backtester import optimize_for_symbol, run_backtest
from exchange_connector import LiveExecutionBroker
from indicators import compute_indicators
from live_trader import execute_live_cycle
from market_data import DEFAULT_SYMBOLS, get_historical_data
from strategies import STRATEGY_CATALOG, evaluate_latest_candle, generate_signals

st.set_page_config(
    page_title="Agente de Trading Cuantitativo | Cripto & Forex",
    page_icon="📈",
    layout="wide",
)

st.title("🤖 Agente de Trading Cuantitativo (Cripto & Forex)")
st.caption(
    "Ejecución en tiempo real (Cuenta Demo / Binance Testnet / OANDA Demo), "
    "Backtesting institucional y Auto-Optimización multi-indicador (EMA, MACD, RSI, Bandas de Bollinger)."
)

tab_live, tab_ranking, tab_backtest = st.tabs(
    [
        "⚡ Operativa en Vivo (Modo Tiburón Agresivo)",
        "🏆 Ranking de los 18 Mercados Más Rentables",
        "📊 Laboratorio de Backtesting & Optimización",
    ]
)

with tab_ranking:
    from pathlib import Path
    import json
    rank_path = Path(__file__).parent / "top_markets_ranking.json"
    st.subheader("🦈 Investigación Cuantitativa: ¿Qué Mercados Dan Más Ganancia?")
    if rank_path.exists():
        rdata = json.loads(rank_path.read_text(encoding="utf-8"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Retorno Cartera Top 8 (31 días)", f"+{rdata.get('top8_return_pct', 0):.2f}%")
        c2.metric("Ganancia Neta Top 8 ($8k base)", f"+${rdata.get('top8_profit_usd', 0):,.2f} USD")
        c3.metric("Riesgo Modo Tiburón", f"{rdata.get('aggressive_risk_pct', 4.0):.1f}% / trade")
        st.dataframe(pd.DataFrame(rdata.get("ranking", [])), use_container_width=True)
    else:
        st.info("Ejecuta `python market_research.py` para generar el ranking de los 18 mercados.")

with tab_live:
    broker = LiveExecutionBroker(initial_balance_usd=10000.0)

    c_btn1, c_btn2, c_btn3 = st.columns([2, 2, 2])
    with c_btn1:
        if st.button("🚀 Ejecutar Ciclo de Escaneo y Operación en Vivo Ahora", use_container_width=True):
            with st.spinner("Escaneando precios en vivo en Binance y Yahoo Finance y ejecutando órdenes..."):
                execute_live_cycle(broker, symbols=list(DEFAULT_SYMBOLS.keys()), interval="15m", risk_pct=2.0)
            st.success("Ciclo en vivo completado y cartera actualizada.")
    with c_btn2:
        if st.button("🔄 Actualizar Precios y PnL de Posiciones Abiertas", use_container_width=True):
            broker.update_open_positions()
            st.toast("Precios y PnL flotante actualizados en tiempo real.")
    with c_btn3:
        if st.button("🧹 Reiniciar Cuenta Demo ($10,000 USD)", use_container_width=True):
            broker.reset_portfolio(10000.0)
            st.warning("Cuenta Demo reiniciada a $10,000.00 USD.")

    st_state = broker.state
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Saldo Inicial Demo", f"${st_state.initial_balance_usd:,.2f}")
    m2.metric("Saldo en Efectivo", f"${st_state.cash_balance_usd:,.2f}", f"Realizado: ${st_state.realized_pnl_usd:+,.2f}")
    m3.metric("Equity Total en Vivo", f"${st_state.equity_usd:,.2f}", f"Flotante: ${st_state.unrealized_pnl_usd:+,.2f}")
    m4.metric("Posiciones Abiertas", str(len(st_state.open_positions)))
    win_r = (st_state.winning_trades / st_state.total_trades * 100.0) if st_state.total_trades > 0 else 0.0
    m5.metric("Trades Cerrados", str(st_state.total_trades), f"Win Rate: {win_r:.1f}%")

    st.subheader("📌 Posiciones Abiertas en Tiempo Real")
    if st_state.open_positions:
        open_df = pd.DataFrame(st_state.open_positions)
        st.dataframe(
            open_df[
                [
                    "id",
                    "symbol",
                    "market",
                    "side",
                    "strategy",
                    "entry_price",
                    "current_price",
                    "stop_loss",
                    "take_profit",
                    "notional_usd",
                    "unrealized_pnl_usd",
                    "unrealized_pnl_pct",
                    "reason",
                    "broker_mode",
                ]
            ],
            use_container_width=True,
        )
    else:
        st.info("No hay posiciones abiertas en este momento. Pulsa '🚀 Ejecutar Ciclo de Escaneo y Operación en Vivo Ahora'.")

    st.subheader("📜 Historial de Operaciones Cerradas en Vivo")
    if st_state.closed_trades:
        st.dataframe(pd.DataFrame(reversed(st_state.closed_trades)), use_container_width=True)
    else:
        st.caption("Aún no se han cerrado operaciones en la cuenta en vivo.")

    st.subheader("🌙 Supervisor Nocturno Autónomo (Sesiones 1h + 20m de Auto-Corrección)")
    from pathlib import Path
    import json
    cfg_path = Path(__file__).parent / "adaptive_config.json"
    log_path = Path(__file__).parent / "overnight_log.txt"
    if cfg_path.exists():
        try:
            acfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            st.caption(
                f"Sesiones iniciadas: **{acfg.get('session_count', 0)}** | "
                f"Auto-correcciones aplicadas: **{acfg.get('corrections_applied', 0)}** | "
                f"Última calibración: `{acfg.get('last_updated', '')}`"
            )
            sym_rows = [
                {
                    "Par": k,
                    "Estado": "🟢 ACTIVO" if v.get("enabled") else "⏸️ PAUSADO (Protección)",
                    "Estrategia Asignada": v.get("strategy"),
                    "SL / TP (ATR)": f"{v.get('sl_atr_mult')}x / {v.get('tp_atr_mult')}x",
                    "Multiplicador Riesgo": f"{v.get('risk_multiplier', 1.0)}x",
                    "Diagnóstico del Auto-Corrector": v.get("notes", ""),
                }
                for k, v in acfg.get("symbols", {}).items()
            ]
            st.dataframe(pd.DataFrame(sym_rows), use_container_width=True)
        except Exception:
            pass
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").splitlines()[-25:]
        st.code("\n".join(lines), language="text")

with tab_backtest:
    with st.sidebar:
        st.header("⚙️ Configuración de Backtest")
        symbol = st.selectbox("Par de Monedas (Cripto / Forex)", list(DEFAULT_SYMBOLS.keys()), index=0)
        interval = st.selectbox("Temporalidad", ["15m", "1h", "4h", "1d"], index=1)
        limit = st.slider("Velas Históricas (Backtest)", min_value=200, max_value=1000, value=750, step=50)
        initial_capital = st.number_input("Capital Inicial ($ USD)", min_value=100.0, max_value=1000000.0, value=1000.0, step=100.0)
        risk_pct = st.slider("Riesgo por Operación (%)", min_value=0.5, max_value=5.0, value=2.0, step=0.5)

        mode = st.radio(
            "Modo de Estrategia",
            ["Auto-Optimizar (Mejor Estrategia Automática)", "Seleccionar Estrategia Manual"],
            index=0,
        )

        if mode == "Seleccionar Estrategia Manual":
            strat_key = st.selectbox(
                "Estrategia Técnica",
                list(STRATEGY_CATALOG.keys()),
                format_func=lambda k: STRATEGY_CATALOG[k],
            )
            sl_mult = st.slider("Stop Loss (Múltiplo ATR)", 1.0, 3.5, 1.6, 0.1)
            tp_mult = st.slider("Take Profit (Múltiplo ATR)", 1.5, 6.0, 3.2, 0.1)

    df_raw, source = get_historical_data(symbol, interval=interval, limit=limit)

    if mode == "Auto-Optimizar (Mejor Estrategia Automática)":
        best_res, all_results = optimize_for_symbol(
            df_raw=df_raw,
            symbol=symbol,
            initial_capital=float(initial_capital),
            risk_per_trade_pct=float(risk_pct),
        )
        active_res = best_res
    else:
        active_res = run_backtest(
            df_raw=df_raw,
            symbol=symbol,
            strategy_name=strat_key,
            initial_capital=float(initial_capital),
            risk_per_trade_pct=float(risk_pct),
            sl_atr_mult=float(sl_mult),
            tp_atr_mult=float(tp_mult),
        )
        all_results = [active_res]

    p = active_res.params
    df_ind = compute_indicators(df_raw, ema_fast=int(p["ema_fast"]), ema_slow=int(p["ema_slow"]))
    df_sig = generate_signals(df_ind, strategy_name=str(p["strategy"]))
    decision = evaluate_latest_candle(df_sig, sl_atr_mult=float(p["sl_atr_mult"]), tp_atr_mult=float(p["tp_atr_mult"]))

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Capital Final", f"${active_res.final_capital:,.2f}", f"{active_res.total_return_pct:+.2f}%")
    col2.metric("Ganancia Neta", f"${active_res.net_profit_usd:+,.2f}", f"vs Hold: {active_res.buy_and_hold_pct:+.2f}%")
    col3.metric("Win Rate", f"{active_res.win_rate_pct:.1f}%", f"{active_res.total_trades} trades")
    col4.metric("Profit Factor", f"{active_res.profit_factor:.2f}", f"Sharpe: {active_res.sharpe_ratio:.2f}")
    col5.metric("Max Drawdown", f"-{active_res.max_drawdown_pct:.2f}%", f"Riesgo/Trade: {risk_pct}%")
    col6.metric("Señal Actual", decision.action, f"Confianza: {decision.confidence:.0f}%")

    st.info(
        f"📡 **Fuente de Datos:** {source} | **Estrategia Activa:** `{active_res.strategy_label}` | "
        f"**Señal en Vivo ({symbol}):** **{decision.action}** — Entrada: `{decision.entry_price:.5f}` | "
        f"Stop Loss: `{decision.stop_loss:.5f}` | Take Profit: `{decision.take_profit:.5f}` — *{decision.reasons[0]}*"
    )

    st.subheader("📈 Curva de Crecimiento del Capital (Equity Curve)")
    if not active_res.equity_curve.empty:
        eq_chart = (
            alt.Chart(active_res.equity_curve)
            .mark_area(
                line={"color": "#00c853"},
                color=alt.Gradient(
                    gradient="linear",
                    stops=[
                        alt.GradientStop(color="rgba(0, 200, 83, 0.45)", offset=0),
                        alt.GradientStop(color="rgba(0, 200, 83, 0.02)", offset=1),
                    ],
                    x1=1,
                    x2=1,
                    y1=1,
                    y2=0,
                ),
            )
            .encode(
                x=alt.X("timestamp:T", title="Fecha / Hora"),
                y=alt.Y("equity:Q", title="Capital de la Cuenta ($ USD)", scale=alt.Scale(zero=False)),
                tooltip=["timestamp:T", alt.Tooltip("equity:Q", format=",.2f"), alt.Tooltip("drawdown_pct:Q", format=".2f")],
            )
            .properties(height=340)
        )
        st.altair_chart(eq_chart, use_container_width=True)

    if len(all_results) > 1:
        st.subheader("🏆 Ranking de Estrategias Probadas en este Par")
        comp_rows = [
            {
                "Estrategia": r.strategy_label,
                "SL / TP (ATR)": f"{r.params['sl_atr_mult']}x / {r.params['tp_atr_mult']}x",
                "EMAs": f"{r.params['ema_fast']}/{r.params['ema_slow']}",
                "Ganancia ($)": round(r.net_profit_usd, 2),
                "Retorno (%)": round(r.total_return_pct, 2),
                "Operaciones": r.total_trades,
                "Win Rate (%)": round(r.win_rate_pct, 1),
                "Profit Factor": round(r.profit_factor, 2),
                "Max Drawdown (%)": round(r.max_drawdown_pct, 2),
            }
            for r in all_results
        ]
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    st.subheader("📋 Historial Detallado de Operaciones (Backtest)")
    if active_res.trades:
        t_df = pd.DataFrame(
            [
                {
                    "Dirección": t.side,
                    "Entrada (Fecha)": t.entry_time,
                    "Salida (Fecha)": t.exit_time,
                    "Precio Entrada": t.entry_price,
                    "Precio Salida": t.exit_price,
                    "PnL ($ USD)": round(t.pnl_usd, 2),
                    "PnL Cuenta (%)": round(t.pnl_pct_account, 2),
                    "Motivo Salida": t.exit_reason,
                    "Señal Técnica": t.signal_reason,
                }
                for t in reversed(active_res.trades)
            ]
        )
        st.dataframe(t_df, use_container_width=True)
