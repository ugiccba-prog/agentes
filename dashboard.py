"""
Panel Visual Interactivo (Streamlit) para el Agente de Trading Cuantitativo 24/7.
Incluye:
- Pestaña 1: ⚡ Operativa en Vivo & Resultados (KPIs, Posiciones Abiertas, Historial Claro y Gráficos)
- Pestaña 2: 🎛️ Parámetros & Agresividad en Vivo (Presets, Riesgo, Walk-Forward y Mercados)
- Pestaña 3: 📱 Link Público para el Celular 24/7 (Sin necesidad de PC encendida + Túnel local)
- Pestaña 4: 📊 Laboratorio de Backtesting
Ejecutar con: python -m streamlit run dashboard.py --server.port 8501
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

from backtester import optimize_for_symbol, run_backtest
from cloud_runner import sync_to_github, update_live_readme
from exchange_connector import LiveExecutionBroker
from indicators import compute_indicators
from live_trader import execute_live_cycle
from market_data import DEFAULT_SYMBOLS, get_historical_data
from overnight_supervisor import (
    OPTION_SPECS,
    build_option_runners,
    load_adaptive_config,
    reset_all_portfolios,
    run_all_options_tick,
    run_self_correction_phase,
    save_adaptive_config,
    sync_base_to_live_portfolio,
)
from risk_manager import RiskConfig
from strategies import STRATEGY_CATALOG, evaluate_latest_candle, generate_signals
from walk_forward import WFConfig

ROOT_DIR = Path(__file__).parent
PUBLIC_URL_FILE = ROOT_DIR / "public_url.txt"
GITHUB_PAGES_URL = "https://ugiccba-prog.github.io/agentes/"
GITHUB_REPO_URL = "https://github.com/ugiccba-prog/agentes"

PRESETS = {
    "🦈 Violenta & Sagaz (Recomendado)": {
        "base_risk_pct": 1.5,
        "max_risk_pct": 3.0,
        "max_leverage": 5.0,
        "max_total_open_risk_pct": 10.0,
        "daily_loss_limit_pct": 5.0,
        "min_rr": 2.0,
        "min_confidence": 70.0,
        "trail_atr_mult": 1.5,
        "min_profit_factor_oos": 1.15,
        "min_trades_train": 12,
        "min_trades_oos": 8,
    },
    "🔥 Berserker / Full Tiburón": {
        "base_risk_pct": 2.5,
        "max_risk_pct": 4.5,
        "max_leverage": 8.0,
        "max_total_open_risk_pct": 14.0,
        "daily_loss_limit_pct": 7.0,
        "min_rr": 1.8,
        "min_confidence": 65.0,
        "trail_atr_mult": 1.3,
        "min_profit_factor_oos": 1.10,
        "min_trades_train": 10,
        "min_trades_oos": 6,
    },
    "🛡️ Conservador Institucional": {
        "base_risk_pct": 1.0,
        "max_risk_pct": 2.0,
        "max_leverage": 3.0,
        "max_total_open_risk_pct": 8.0,
        "daily_loss_limit_pct": 4.0,
        "min_rr": 2.0,
        "min_confidence": 70.0,
        "trail_atr_mult": 1.5,
        "min_profit_factor_oos": 1.20,
        "min_trades_train": 15,
        "min_trades_oos": 10,
    },
}

st.set_page_config(
    page_title="Agente Tiburón 24/7 | Cripto & Forex",
    page_icon="🦈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .stApp { background: #0b1120; color: #f3f4f6; }
      div[data-testid="stMetric"] {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 12px;
        padding: 12px 14px;
      }
      .shark-banner {
        background: linear-gradient(135deg, #111827 0%, #172554 100%);
        border: 1px solid #1e3a8a;
        border-radius: 14px;
        padding: 16px 20px;
        margin-bottom: 14px;
      }
      .shark-card {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 12px;
        padding: 14px 16px;
        margin-bottom: 10px;
      }
      .shark-card-green { border-left: 4px solid #10b981; }
      .shark-card-red { border-left: 4px solid #ef4444; }
      .shark-card-blue { border-left: 4px solid #3b82f6; }
    </style>
    """,
    unsafe_allow_html=True,
)

acfg = load_adaptive_config()
preset_label = acfg.get("preset_name", "🦈 Violenta & Sagaz (Recomendado)")
rcfg = RiskConfig.from_adaptive_config()
wcfg = WFConfig.from_adaptive_config()

st.markdown(
    f"""
    <div class="shark-banner">
      <div style="font-size:1.45rem; font-weight:800;">🦈 Agente Cuantitativo Multi-Mercado 24/7</div>
      <div style="color:#cbd5e1; font-size:0.92rem; margin-top:4px;">
        Perfil activo: <strong>{preset_label}</strong> · Walk-Forward OOS semanal · Velas cerradas ·
        RiskManager (Breakeven +1R & Trailing {rcfg.trail_atr_mult:.1f} ATR +2R) ·
        Link móvil 24/7: <a href="{GITHUB_PAGES_URL}" target="_blank" style="color:#60a5fa;">{GITHUB_PAGES_URL}</a>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_live, tab_params, tab_mobile, tab_backtest = st.tabs(
    [
        "⚡ Operativa en Vivo & Resultados",
        "🎛️ Parámetros & Agresividad (En Vivo)",
        "📱 Link Público para el Celular (24/7)",
        "📊 Laboratorio de Backtesting",
    ]
)

# ==============================================================================
# PESTAÑA 1: OPERATIVA EN VIVO & RESULTADOS
# ==============================================================================
with tab_live:
    runners, _ = build_option_runners(ROOT_DIR)
    latest_acfg = load_adaptive_config()

    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if st.button("🚀 Ejecutar Ciclo 5 Opciones Ahora", use_container_width=True):
            with st.spinner("Evaluando las 5 opciones sobre los mismos mercados..."):
                run_all_options_tick(runners, latest_acfg, interval="15m")
            st.success("Ciclo sincronizado de las 5 opciones ejecutado.")
            st.rerun()
    with b2:
        if st.button("🔄 Actualizar Precios y Stops (5 Opciones)", use_container_width=True):
            for spec, b_opt, _ in runners:
                events = b_opt.update_open_positions()
                for ev in events:
                    st.toast(f"[{spec['short_name']}] {ev}")
            sync_base_to_live_portfolio(runners[0][1])
            st.success("Precios, stops y PnL actualizados en las 5 opciones.")
            st.rerun()
    with b3:
        if st.button("☁️ Sincronizar 5 Opciones al Link Móvil", use_container_width=True):
            with st.spinner("Actualizando README, matrices y subiendo a GitHub..."):
                update_live_readme(runners, latest_acfg)
                sync_to_github()
            st.success("Estado de las 5 opciones sincronizado con GitHub Pages.")
    with b4:
        if st.button("🧹 Reiniciar 5 Carteras a $10,000 USD", use_container_width=True):
            reset_all_portfolios(10000.0, ROOT_DIR)
            st.success("Las 5 carteras fueron reiniciadas a $10,000.00 USD desde cero.")
            st.rerun()

    st.subheader("🏆 Comparativa en Vivo: 5 Opciones sobre los Mismos Mercados ($10,000 USD c/u)")
    comp5_rows = []
    for spec, b_opt, _ in runners:
        bs = b_opt.state
        ct = bs.closed_trades or []
        op = bs.open_positions or []
        gw = sum(float(t.get("pnl_usd", 0.0)) for t in ct if float(t.get("pnl_usd", 0.0)) > 0)
        gl = abs(sum(float(t.get("pnl_usd", 0.0)) for t in ct if float(t.get("pnl_usd", 0.0)) <= 0))
        pf_o = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
        net_o = bs.equity_usd - bs.initial_balance_usd
        open_desc = (
            " | ".join(
                f"{'🟢' if p.get('side') == 'LONG' else '🔴'} {p.get('symbol')} {p.get('side')} (${float(p.get('unrealized_pnl_usd', 0.0)):+.2f})"
                for p in op
            )
            if op
            else "⚪ Sin abiertas"
        )
        comp5_rows.append(
            {
                "Opción": spec["name"],
                "Reglas": spec["rule_summary"],
                "Equity ($ USD)": float(bs.equity_usd),
                "Resultado Neto ($)": round(net_o, 2),
                "Qué Hay Abierto Ahora": open_desc,
                "Cerradas (W/L)": f"{bs.winning_trades}W / {bs.losing_trades}L ({len(ct)})",
                "PF Real": round(pf_o, 2),
                "Peor Caída (%)": round(float(bs.max_drawdown_pct), 2),
            }
        )
    st.dataframe(
        pd.DataFrame(comp5_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Equity ($ USD)": st.column_config.NumberColumn(format="$%,.2f"),
            "Resultado Neto ($)": st.column_config.NumberColumn(format="$%+,.2f"),
            "PF Real": st.column_config.NumberColumn(format="%.2f"),
            "Peor Caída (%)": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    st.subheader("🎯 Matriz en Vivo: Qué Hay Abierto y Qué No por Mercado")
    enabled_syms = [s for s, sc in latest_acfg.get("symbols", {}).items() if sc.get("enabled")]
    extra_open_syms = [
        p.get("symbol")
        for _, b_opt, _ in runners
        for p in b_opt.state.open_positions
        if p.get("symbol") not in enabled_syms
    ]
    matrix_syms = list(dict.fromkeys([*enabled_syms, *extra_open_syms]))
    matrix_rows = []
    for sym in matrix_syms:
        row_dict = {"Mercado": sym}
        for spec, b_opt, _ in runners:
            pos = next((p for p in b_opt.state.open_positions if p.get("symbol") == sym), None)
            if pos is None:
                row_dict[spec["short_name"]] = "⚪ Sin posición"
            else:
                icon = "🟢" if pos.get("side") == "LONG" else "🔴"
                row_dict[spec["short_name"]] = f"{icon} {pos.get('side')} (${float(pos.get('unrealized_pnl_usd', 0.0)):+.2f})"
        matrix_rows.append(row_dict)
    if matrix_rows:
        st.dataframe(pd.DataFrame(matrix_rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    selected_opt_name = st.selectbox(
        "🔎 Inspeccionar Cartera en Detalle:",
        [spec["name"] for spec, _, _ in runners],
        index=0,
    )
    sel_spec, broker, _ = next(r for r in runners if r[0]["name"] == selected_opt_name)
    st.caption(f"Mostrando **{sel_spec['name']}** (`{sel_spec['file']}`) · {sel_spec['rule_summary']}")

    st_state = broker.state
    closed = st_state.closed_trades or []
    wins_list = [t for t in closed if float(t.get("pnl_usd", 0.0)) > 0]
    losses_list = [t for t in closed if float(t.get("pnl_usd", 0.0)) <= 0]
    gross_win = sum(float(t.get("pnl_usd", 0.0)) for t in wins_list)
    gross_loss = abs(sum(float(t.get("pnl_usd", 0.0)) for t in losses_list))
    real_pf = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    win_r = (len(wins_list) / len(closed) * 100.0) if closed else 0.0

    total_diff = st_state.equity_usd - st_state.initial_balance_usd
    total_pct = (total_diff / st_state.initial_balance_usd * 100.0) if st_state.initial_balance_usd else 0.0
    total_notional = sum(float(p.get("notional_usd", 0.0)) for p in st_state.open_positions)
    lev_now = (total_notional / st_state.equity_usd) if st_state.equity_usd > 0 else 0.0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Capital Inicial", f"${st_state.initial_balance_usd:,.2f}", f"Peak: ${st_state.peak_equity_usd:,.2f}")
    k2.metric("Equity Total en Vivo", f"${st_state.equity_usd:,.2f}", f"${total_diff:+,.2f} ({total_pct:+.2f}%)")
    k3.metric("PnL Realizado (Cerrado)", f"${st_state.realized_pnl_usd:+,.2f}", f"{len(wins_list)}✅ / {len(losses_list)}❌")
    k4.metric("PnL Flotante (Abierto)", f"${st_state.unrealized_pnl_usd:+,.2f}", f"Exp: ${total_notional:,.0f} ({lev_now:.2f}x)")
    k5.metric("Win Rate & PF Real", f"PF {real_pf:.2f}", f"WR: {win_r:.1f}% ({len(closed)} trades)")
    k6.metric("Meta Paper (100 Trades)", f"{st_state.total_trades} / 100", f"Faltan {max(0, 100 - st_state.total_trades)}")
    st.progress(min(1.0, st_state.total_trades / 100.0))

    st.markdown("---")
    st.subheader("📌 Sección A — Posiciones Abiertas en Tiempo Real")
    if st_state.open_positions:
        open_rows = []
        for p in st_state.open_positions:
            side = str(p.get("side", ""))
            entry = float(p.get("entry_price", 0.0))
            sl = float(p.get("stop_loss", 0.0))
            is_protected = (sl >= entry) if side == "LONG" else (sl <= entry)
            open_rows.append(
                {
                    "ID": p.get("id"),
                    "Par": p.get("symbol"),
                    "Mercado": p.get("market"),
                    "Lado": "🟢 LONG" if side == "LONG" else "🔴 SHORT",
                    "Estrategia": p.get("strategy"),
                    "Precio Entrada": entry,
                    "Precio Actual": float(p.get("current_price", 0.0)),
                    "Stop Loss": sl,
                    "Estado Stop": "🔒 Protegido (BE/Trail)" if is_protected else "🛡️ En Riesgo Inicial",
                    "Take Profit": float(p.get("take_profit", 0.0)),
                    "Exposición ($ USD)": float(p.get("notional_usd", 0.0)),
                    "Resultado Flotante ($ USD)": float(p.get("unrealized_pnl_usd", 0.0)),
                    "Resultado (%)": float(p.get("unrealized_pnl_pct", 0.0)),
                }
            )
        open_df = pd.DataFrame(open_rows)
        st.dataframe(
            open_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Precio Entrada": st.column_config.NumberColumn(format="%.6g"),
                "Precio Actual": st.column_config.NumberColumn(format="%.6g"),
                "Stop Loss": st.column_config.NumberColumn(format="%.6g"),
                "Take Profit": st.column_config.NumberColumn(format="%.6g"),
                "Exposición ($ USD)": st.column_config.NumberColumn(format="$%.2f"),
                "Resultado Flotante ($ USD)": st.column_config.NumberColumn(format="$%+.2f"),
                "Resultado (%)": st.column_config.NumberColumn(format="%+.2f%%"),
            },
        )

        mc1, mc2 = st.columns([3, 1])
        with mc1:
            pos_options = [
                f"{p['symbol']} ({p['side']} | Flotante: ${float(p.get('unrealized_pnl_usd', 0.0)):+.2f})"
                for p in st_state.open_positions
            ]
            selected_pos_label = st.selectbox("Cerrar posición manualmente:", pos_options)
        with mc2:
            st.write("")
            st.write("")
            if st.button("✂️ Cerrar Posición Seleccionada", use_container_width=True):
                target_sym = selected_pos_label.split(" ")[0]
                msg_close = broker.close_position_by_symbol(target_sym, reason="Cierre Manual desde Panel")
                if sel_spec["key"] == "base":
                    sync_base_to_live_portfolio(broker)
                if msg_close.startswith("✅"):
                    st.success(msg_close)
                    st.rerun()
                else:
                    st.warning(msg_close)
    else:
        st.info("No hay posiciones abiertas ahora mismo. El agente solo abre operaciones si pasan Walk-Forward OOS y RiskManager.")

    st.markdown("---")
    st.subheader("🏁 Sección B — Operaciones Realizadas y su Resultado")
    if closed:
        pnls = [float(t.get("pnl_usd", 0.0)) for t in closed]
        best_trade = max(pnls)
        worst_trade = min(pnls)

        ck1, ck2, ck3, ck4, ck5, ck6, ck7 = st.columns(7)
        ck1.metric("Total Cerradas", str(len(closed)))
        ck2.metric("✅ Ganadoras", str(len(wins_list)))
        ck3.metric("❌ Perdedoras", str(len(losses_list)))
        ck4.metric("Ganancia Bruta", f"+${gross_win:,.2f}")
        ck5.metric("Pérdida Bruta", f"-${gross_loss:,.2f}")
        ck6.metric("Mejor Trade", f"${best_trade:+,.2f}")
        ck7.metric("Peor Trade", f"${worst_trade:+,.2f}")

        f1, f2, f3 = st.columns(3)
        pairs_avail = ["Todos"] + sorted({str(t.get("symbol", "")) for t in closed})
        strats_avail = ["Todas"] + sorted({str(t.get("strategy", "")) for t in closed})
        with f1:
            flt_pair = st.selectbox("Filtrar por Par", pairs_avail)
        with f2:
            flt_res = st.selectbox("Filtrar por Resultado", ["Todas", "✅ Solo Ganadoras", "❌ Solo Perdedoras"])
        with f3:
            flt_strat = st.selectbox("Filtrar por Estrategia", strats_avail)

        filtered = []
        for t in reversed(closed):
            pnl_u = float(t.get("pnl_usd", 0.0))
            if flt_pair != "Todos" and t.get("symbol") != flt_pair:
                continue
            if flt_res == "✅ Solo Ganadoras" and pnl_u <= 0:
                continue
            if flt_res == "❌ Solo Perdedoras" and pnl_u > 0:
                continue
            if flt_strat != "Todas" and t.get("strategy") != flt_strat:
                continue
            filtered.append(
                {
                    "Resultado": "✅ GANANCIA" if pnl_u > 0 else "❌ PÉRDIDA",
                    "Fecha Cierre": t.get("exit_time", t.get("closed_at", "")),
                    "Par": t.get("symbol", ""),
                    "Lado": t.get("side", ""),
                    "Estrategia": t.get("strategy", ""),
                    "Entrada": float(t.get("entry_price", 0.0)),
                    "Salida": float(t.get("exit_price", 0.0)),
                    "PnL ($ USD)": pnl_u,
                    "PnL (%)": float(t.get("pnl_pct", 0.0)),
                    "R-Múltiplo": float(t.get("r_multiple", 0.0)),
                    "Motivo de Cierre": t.get("exit_reason", ""),
                }
            )

        st.dataframe(
            pd.DataFrame(filtered),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entrada": st.column_config.NumberColumn(format="%.6g"),
                "Salida": st.column_config.NumberColumn(format="%.6g"),
                "PnL ($ USD)": st.column_config.NumberColumn(format="$%+.2f"),
                "PnL (%)": st.column_config.NumberColumn(format="%+.2f%%"),
                "R-Múltiplo": st.column_config.NumberColumn(format="%+.2fR"),
            },
        )

        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**📈 Curva de Capital Cerrado Acumulado**")
            eq_pts = [{"Trade #": 0, "Capital ($ USD)": st_state.initial_balance_usd}]
            running_eq = st_state.initial_balance_usd
            for idx, t in enumerate(closed, start=1):
                running_eq += float(t.get("pnl_usd", 0.0))
                eq_pts.append({"Trade #": idx, "Capital ($ USD)": round(running_eq, 2)})
            eq_df = pd.DataFrame(eq_pts)
            chart_eq = (
                alt.Chart(eq_df)
                .mark_area(
                    line={"color": "#10b981"},
                    color="rgba(16, 185, 129, 0.20)",
                )
                .encode(
                    x=alt.X("Trade #:Q", title="Operación #"),
                    y=alt.Y("Capital ($ USD):Q", scale=alt.Scale(zero=False)),
                    tooltip=["Trade #", alt.Tooltip("Capital ($ USD):Q", format="$,.2f")],
                )
                .properties(height=270)
            )
            st.altair_chart(chart_eq, use_container_width=True)

        with g2:
            st.markdown("**📊 Resultado Neto por Moneda ($ USD)**")
            by_sym: dict[str, float] = {}
            for t in closed:
                s = str(t.get("symbol", ""))
                by_sym[s] = by_sym.get(s, 0.0) + float(t.get("pnl_usd", 0.0))
            sym_df = pd.DataFrame(
                [{"Par": k, "PnL Neto ($ USD)": round(v, 2)} for k, v in sorted(by_sym.items(), key=lambda x: x[1], reverse=True)]
            )
            chart_sym = (
                alt.Chart(sym_df)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("Par:N", sort="-y"),
                    y=alt.Y("PnL Neto ($ USD):Q"),
                    color=alt.condition(alt.datum["PnL Neto ($ USD)"] >= 0, alt.value("#10b981"), alt.value("#ef4444")),
                    tooltip=["Par", alt.Tooltip("PnL Neto ($ USD):Q", format="$+,.2f")],
                )
                .properties(height=270)
            )
            st.altair_chart(chart_sym, use_container_width=True)
    else:
        st.caption("Aún no se han cerrado operaciones en la cuenta en vivo.")

    st.markdown("---")
    st.subheader("🔬 Sección C — Comparativa A/B en Vivo: Base vs Base + Impulso (Monte Carlo)")
    base_file = ROOT_DIR / "paper_base.json"
    mom_file = ROOT_DIR / "paper_momentum.json"
    study_file = ROOT_DIR / "impulse_study.json"
    if base_file.exists() and mom_file.exists():
        p_base = json.loads(base_file.read_text(encoding="utf-8"))
        p_mom = json.loads(mom_file.read_text(encoding="utf-8"))
        comp_rows = []
        for label, pdata in [("Estrategia Base (OOS)", p_base), ("Base + Impulso (Shark)", p_mom)]:
            ct = pdata.get("closed_trades", [])
            op = pdata.get("open_positions", [])
            net_eq = float(pdata.get("equity_usd", 10000.0)) - float(pdata.get("initial_balance_usd", 10000.0))
            comp_rows.append(
                {
                    "Cartera": label,
                    "Equity ($ USD)": float(pdata.get("equity_usd", 10000.0)),
                    "Resultado Neto ($ USD)": round(net_eq, 2),
                    "Cerradas": len(ct),
                    "Abiertas": len(op),
                    "Peor Caída (%)": round(float(pdata.get("max_drawdown_pct", 0.0)), 2),
                    "Último Reporte": str(pdata.get("last_updated", "")),
                }
            )
        st.dataframe(
            pd.DataFrame(comp_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Equity ($ USD)": st.column_config.NumberColumn(format="$%,.2f"),
                "Resultado Neto ($ USD)": st.column_config.NumberColumn(format="$%+,.2f"),
                "Peor Caída (%)": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
        if study_file.exists():
            study_data = json.loads(study_file.read_text(encoding="utf-8")).get("controls", {})
            mom_closed = [
                t for t in p_mom.get("closed_trades", [])
                if t.get("strategy") in ("momentum_extra", "momentum_shark", "momentum_base")
            ]
            matched = [
                study_data.get(f"{t.get('symbol')}|{t.get('entry_time')}|{t.get('id')}")
                for t in mom_closed
            ]
            matched = [m for m in matched if m]
            if matched:
                sims = sorted(sum(m["random_net"][i] for m in matched) for i in range(200))
                impulse_net = sum(float(m["trade_net"]) for m in matched)
                median_rng = (sims[99] + sims[100]) / 2.0
                st.info(
                    f"📊 **Estudio de Timing vs Entradas al Azar ({len(matched)} cierres comparables):** "
                    f"Impulso Neto: **${impulse_net:+,.2f}** · Mediana al Azar: **${median_rng:+,.2f}** · "
                    f"Rango 90% Azar: `${sims[10]:+,.2f}` a `${sims[189]:+,.2f}`"
                )
    else:
        st.caption("Comparativa A/B pendiente de generación (`paper_base.json` / `paper_momentum.json`).")


# ==============================================================================
# PESTAÑA 2: PARÁMETROS & AGRESIVIDAD (EN VIVO)
# ==============================================================================
with tab_params:
    st.subheader("🎛️ Control de Agresividad, Riesgo y Walk-Forward en Vivo")
    st.caption("Cambia la agresividad del agente sin tocar código. Los cambios se aplican en adaptive_config.json al instante.")

    preset_choice = st.selectbox(
        "Elegir Preset de Agresividad",
        list(PRESETS.keys()),
        index=list(PRESETS.keys()).index(preset_label) if preset_label in PRESETS else 0,
    )
    if st.button("⚡ Aplicar Preset Seleccionado Ahora"):
        pvals = PRESETS[preset_choice]
        acfg["preset_name"] = preset_choice
        acfg["risk_config"] = {
            "base_risk_pct": pvals["base_risk_pct"],
            "max_risk_pct": pvals["max_risk_pct"],
            "min_risk_pct": 0.5,
            "max_total_open_risk_pct": pvals["max_total_open_risk_pct"],
            "max_leverage": pvals["max_leverage"],
            "daily_loss_halt_pct": pvals["daily_loss_limit_pct"],
            "min_rr": pvals["min_rr"],
            "min_confidence": pvals["min_confidence"],
            "trail_atr_mult": pvals["trail_atr_mult"],
            "max_net_per_currency": 2,
            "max_net_crypto": 4 if pvals["max_leverage"] >= 5.0 else 3,
            "max_open_positions": 8,
            "dd_half_size_pct": 8.0,
            "dd_halt_pct": 15.0,
            "min_sl_pct": 0.08,
        }
        acfg["wf_config"] = {
            "min_profit_factor_oos": pvals["min_profit_factor_oos"],
            "min_trades_train": pvals["min_trades_train"],
            "min_trades_oos": pvals["min_trades_oos"],
            "top_k_candidates": 5,
            "trail_atr_mult": pvals["trail_atr_mult"],
        }
        save_adaptive_config(acfg)
        st.success(f"Preset '{preset_choice}' guardado en adaptive_config.json.")
        st.rerun()

    st.markdown("---")
    pc1, pc2 = st.columns(2)
    with pc1:
        st.markdown("### 🦈 Gestión de Riesgo & Tamaño")
        in_base_risk = st.slider("Riesgo Base por Trade (%)", 0.5, 5.0, float(rcfg.base_risk_pct), 0.25)
        in_max_risk = st.slider("Riesgo Máximo con Convicción (%)", 1.0, 6.0, float(rcfg.max_risk_pct), 0.25)
        in_max_lev = st.slider("Apalancamiento Máximo Total (x)", 1.0, 10.0, float(rcfg.max_leverage), 0.5)
        in_open_risk = st.slider("Riesgo Total Abierto Máximo (%)", 4.0, 20.0, float(rcfg.max_total_open_risk_pct), 0.5)
        in_daily_loss = st.slider("Freno Pérdida Diaria (%)", 2.0, 12.0, float(rcfg.daily_loss_halt_pct), 0.5)
        in_min_rr = st.slider("Ratio Riesgo/Beneficio Mínimo (R:R)", 1.5, 4.0, float(rcfg.min_rr), 0.1)
        in_min_conf = st.slider("Confianza Mínima de Señal", 55.0, 90.0, float(rcfg.min_confidence), 1.0)

    with pc2:
        st.markdown("### 🔬 Validación Walk-Forward & Salidas")
        in_trail_mult = st.slider("Trailing Stop desde +2R (x ATR)", 0.8, 3.0, float(rcfg.trail_atr_mult), 0.1)
        in_min_pf_oos = st.slider("Profit Factor Mínimo OOS", 1.0, 2.0, float(wcfg.min_profit_factor_oos), 0.05)
        in_min_train = st.number_input("Mínimo Trades en Entrenamiento (60%)", min_value=5, max_value=60, value=int(wcfg.min_trades_train), step=1)
        in_min_oos = st.number_input("Mínimo Trades en Validación OOS (40%)", min_value=3, max_value=40, value=int(wcfg.min_trades_oos), step=1)
        in_top_k = st.number_input("Top Candidatos de Train evaluados en OOS", min_value=1, max_value=15, value=int(wcfg.top_k_candidates), step=1)

    sb1, sb2 = st.columns(2)
    with sb1:
        if st.button("💾 Guardar Parámetros en Vivo", use_container_width=True):
            acfg["preset_name"] = "Personalizado"
            acfg["risk_config"] = {
                "base_risk_pct": float(in_base_risk),
                "max_risk_pct": max(float(in_base_risk), float(in_max_risk)),
                "min_risk_pct": 0.5,
                "max_total_open_risk_pct": float(in_open_risk),
                "max_leverage": float(in_max_lev),
                "daily_loss_halt_pct": float(in_daily_loss),
                "min_rr": float(in_min_rr),
                "min_confidence": float(in_min_conf),
                "trail_atr_mult": float(in_trail_mult),
                "max_net_per_currency": int(rcfg.max_net_per_currency),
                "max_net_crypto": int(rcfg.max_net_crypto),
                "max_open_positions": int(rcfg.max_open_positions),
                "dd_half_size_pct": float(rcfg.dd_half_size_pct),
                "dd_halt_pct": float(rcfg.dd_halt_pct),
                "min_sl_pct": float(rcfg.min_sl_pct),
            }
            acfg["wf_config"] = {
                "min_profit_factor_oos": float(in_min_pf_oos),
                "min_trades_train": int(in_min_train),
                "min_trades_oos": int(in_min_oos),
                "top_k_candidates": int(in_top_k),
                "trail_atr_mult": float(in_trail_mult),
            }
            save_adaptive_config(acfg)
            st.success("Parámetros guardados en adaptive_config.json.")
            st.rerun()

    with sb2:
        if st.button("🔬 Recalibrar Walk-Forward Ahora con Nuevos Parámetros", use_container_width=True):
            with st.spinner("Recalculando Walk-Forward OOS (1500 velas por mercado)..."):
                b_tmp = LiveExecutionBroker(initial_balance_usd=10000.0)
                run_self_correction_phase(b_tmp, load_adaptive_config(), interval="15m", force=True)
            st.success("Recalibración Walk-Forward completada y guardada en wf_params.json y adaptive_config.json.")
            st.rerun()

    st.markdown("---")
    st.subheader("🌐 Control Manual de los 18 Mercados")
    sym_table_rows = []
    for sym in DEFAULT_SYMBOLS:
        sc = acfg.get("symbols", {}).get(sym, {})
        sym_table_rows.append(
            {
                "Par": sym,
                "Activo": bool(sc.get("enabled", False)),
                "Estrategia": str(sc.get("strategy", "confluence")),
                "SL (x ATR)": float(sc.get("sl_atr_mult", 1.6)),
                "TP (x ATR)": float(sc.get("tp_atr_mult", 3.2)),
                "PF OOS": float(sc.get("backtest_profit_factor", 0.0)),
                "Diagnóstico": str(sc.get("notes", "")),
            }
        )
    edited_syms = st.data_editor(
        pd.DataFrame(sym_table_rows),
        use_container_width=True,
        hide_index=True,
        disabled=["Par", "PF OOS", "Diagnóstico"],
        column_config={
            "Activo": st.column_config.CheckboxColumn(),
            "Estrategia": st.column_config.SelectboxColumn(options=list(STRATEGY_CATALOG.keys())),
            "SL (x ATR)": st.column_config.NumberColumn(min_value=0.8, max_value=4.0, step=0.1, format="%.1f"),
            "TP (x ATR)": st.column_config.NumberColumn(min_value=1.5, max_value=8.0, step=0.1, format="%.1f"),
        },
    )
    if st.button("💾 Guardar Tabla de Mercados", use_container_width=True):
        latest_cfg = load_adaptive_config()
        for _, row in edited_syms.iterrows():
            s = row["Par"]
            cur = latest_cfg.setdefault("symbols", {}).setdefault(s, {})
            cur["enabled"] = bool(row["Activo"])
            cur["strategy"] = str(row["Estrategia"])
            cur["sl_atr_mult"] = float(row["SL (x ATR)"])
            cur["tp_atr_mult"] = float(row["TP (x ATR)"])
        save_adaptive_config(latest_cfg)
        st.success("Tabla de mercados guardada.")
        st.rerun()


# ==============================================================================
# PESTAÑA 3: LINK PÚBLICO PARA EL CELULAR (24/7 SIN PC)
# ==============================================================================
with tab_mobile:
    st.subheader("📱 Acceso Público desde el Celular (Funciona 24/7 con tu PC Apagada)")

    col_m1, col_m2 = st.columns([2, 1])
    with col_m1:
        st.markdown(
            f"""
            <div class="shark-card shark-card-green">
              <div style="font-size:1.05rem; font-weight:700;">☁️ Link Público 24/7 (Funciona con esta PC APAGADA)</div>
              <p style="color:#cbd5e1; margin:6px 0;">
                El agente corre en los servidores de GitHub Actions y actualiza tu cartera en vivo cada pocos minutos.
                Abrí esta URL desde Safari o Chrome en tu celular y guardala en tu pantalla de inicio:
              </p>
              <div style="font-size:1.1rem; font-weight:700; margin:8px 0;">
                👉 <a href="{GITHUB_PAGES_URL}" target="_blank" style="color:#34d399;">{GITHUB_PAGES_URL}</a>
              </div>
              <div style="font-size:0.88rem; color:#9ca3af;">
                Respaldo en GitHub: <a href="{GITHUB_REPO_URL}" target="_blank" style="color:#60a5fa;">{GITHUB_REPO_URL}</a>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        tunnel_url = ""
        if PUBLIC_URL_FILE.exists():
            tunnel_url = PUBLIC_URL_FILE.read_text(encoding="utf-8", errors="ignore").strip()

        if tunnel_url:
            st.markdown(
                f"""
                <div class="shark-card shark-card-blue">
                  <div style="font-size:1.05rem; font-weight:700;">🖥️ Link Túnel Interactivo Local (Solo mientras esta PC esté prendida)</div>
                  <p style="color:#cbd5e1; margin:6px 0;">
                    Si querés cambiar sliders desde el celu mientras esta PC está encendida:
                  </p>
                  <div style="font-size:1.05rem; font-weight:700;">
                    👉 <a href="{tunnel_url}" target="_blank" style="color:#60a5fa;">{tunnel_url}</a>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info("El túnel temporal hacia localhost:8501 no tiene URL registrada en `public_url.txt` en este momento.")

    with col_m2:
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={quote(GITHUB_PAGES_URL, safe='')}"
        st.markdown("**Escaneá con la cámara del celu:**")
        st.image(qr_url, caption="Dashboard Móvil 24/7 (Sin PC)", width=220)


# ==============================================================================
# PESTAÑA 4: LABORATORIO DE BACKTESTING
# ==============================================================================
with tab_backtest:
    st.subheader("📊 Laboratorio de Backtesting & Comparación de Estrategias")
    bc1, bc2, bc3, bc4, bc5 = st.columns(5)
    with bc1:
        symbol = st.selectbox("Par de Monedas", list(DEFAULT_SYMBOLS.keys()), index=0)
    with bc2:
        interval = st.selectbox("Temporalidad", ["15m", "1h", "4h", "1d"], index=1)
    with bc3:
        limit = st.slider("Velas Históricas", min_value=200, max_value=1500, value=750, step=50)
    with bc4:
        initial_capital = st.number_input("Capital Inicial ($)", min_value=100.0, max_value=1000000.0, value=1000.0, step=100.0)
    with bc5:
        risk_pct = st.slider("Riesgo/Trade (%)", min_value=0.5, max_value=5.0, value=2.0, step=0.5)

    mode = st.radio(
        "Modo de Estrategia",
        ["Auto-Optimizar (Mejor Estrategia Automática)", "Seleccionar Estrategia Manual"],
        horizontal=True,
    )
    if mode == "Seleccionar Estrategia Manual":
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            strat_key = st.selectbox("Estrategia", list(STRATEGY_CATALOG.keys()), format_func=lambda k: STRATEGY_CATALOG[k])
        with mc2:
            sl_mult = st.slider("Stop Loss (x ATR)", 1.0, 3.5, 1.6, 0.1)
        with mc3:
            tp_mult = st.slider("Take Profit (x ATR)", 1.5, 6.0, 3.2, 0.1)

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
    decision = evaluate_latest_candle(df_sig, sl_atr_mult=float(p["sl_atr_mult"]), tp_atr_mult=float(p["tp_atr_mult"]), lookback_bars=1)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Capital Final", f"${active_res.final_capital:,.2f}", f"{active_res.total_return_pct:+.2f}%")
    col2.metric("Ganancia Neta", f"${active_res.net_profit_usd:+,.2f}", f"vs Hold: {active_res.buy_and_hold_pct:+.2f}%")
    col3.metric("Win Rate", f"{active_res.win_rate_pct:.1f}%", f"{active_res.total_trades} trades")
    col4.metric("Profit Factor", f"{active_res.profit_factor:.2f}", f"Sharpe: {active_res.sharpe_ratio:.2f}")
    col5.metric("Max Drawdown", f"-{active_res.max_drawdown_pct:.2f}%", f"Riesgo/Trade: {risk_pct}%")
    col6.metric("Señal Actual", decision.action, f"Confianza: {decision.confidence:.0f}%")

    if not active_res.equity_curve.empty:
        eq_chart = (
            alt.Chart(active_res.equity_curve)
            .mark_area(line={"color": "#10b981"}, color="rgba(16, 185, 129, 0.25)")
            .encode(
                x=alt.X("timestamp:T", title="Fecha / Hora"),
                y=alt.Y("equity:Q", title="Capital ($ USD)", scale=alt.Scale(zero=False)),
                tooltip=["timestamp:T", alt.Tooltip("equity:Q", format=",.2f"), alt.Tooltip("drawdown_pct:Q", format=".2f")],
            )
            .properties(height=320)
        )
        st.altair_chart(eq_chart, use_container_width=True)

    if len(all_results) > 1:
        st.markdown("**🏆 Ranking de Estrategias Probadas en este Par**")
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
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

    if active_res.trades:
        st.markdown("**📋 Historial de Trades (Backtest)**")
        t_df = pd.DataFrame(
            [
                {
                    "Resultado": "✅" if t.pnl_usd > 0 else "❌",
                    "Dirección": t.side,
                    "Entrada": t.entry_time,
                    "Salida": t.exit_time,
                    "PnL ($ USD)": round(t.pnl_usd, 2),
                    "PnL Cuenta (%)": round(t.pnl_pct_account, 2),
                    "Motivo": t.exit_reason,
                }
                for t in reversed(active_res.trades)
            ]
        )
        st.dataframe(t_df, use_container_width=True, hide_index=True)
