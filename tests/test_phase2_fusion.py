"""Pruebas de la fusión Fase 2: anti-lookahead, regímenes ADX, gestión de riesgo sin deadlock, WF v3 y compare_trial."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from exchange_connector import LiveExecutionBroker
from indicators import compute_indicators
from overnight_supervisor import load_adaptive_config, run_trading_tick
from research.compare_trial import compare_directory, render_markdown
from risk_manager import RiskConfig, RiskManager
from strategies import STRATEGY_CATALOG, generate_signals
import walk_forward


def _make_ohlcv(n: int = 250, *, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rets = rng.normal(0.0005, 0.005, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    open_p = np.roll(close, 1)
    open_p[0] = 100.0
    high = np.maximum(open_p, close) * 1.003
    low = np.minimum(open_p, close) * 0.997
    vol = rng.uniform(500.0, 2000.0, size=n)
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })


class IndicatorNoLookaheadTests(unittest.TestCase):
    def test_no_bfill_and_strict_causality(self):
        df = _make_ohlcv(260)
        ind = compute_indicators(df)
        self.assertTrue(pd.isna(ind["atr"].iloc[0]))
        self.assertTrue(ind["donchian_high"].iloc[:20].isna().all())
        self.assertTrue(ind["donchian_low"].iloc[:20].isna().all())
        self.assertTrue(np.isfinite(ind["atr"].iloc[-1]))

        # Mutar el futuro (vela 240) no puede alterar indicadores en la vela 120
        df_mut = df.copy()
        df_mut.loc[240:, ["open", "high", "low", "close"]] *= 3.0
        ind_mut = compute_indicators(df_mut)
        for col in ("ema_fast", "ema_slow", "rsi", "macd_hist", "atr", "adx", "donchian_high"):
            self.assertAlmostEqual(float(ind[col].iloc[120]), float(ind_mut[col].iloc[120]), places=10)


class RegimeStrategiesTests(unittest.TestCase):
    def _base_ind(self, n: int = 60) -> pd.DataFrame:
        close = np.full(n, 100.0)
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": close,
            "volume": np.full(n, 1000.0),
            "ema_fast": np.full(n, 101.0),
            "ema_slow": np.full(n, 100.0),
            "ema_trend": np.full(n, 99.0),
            "rsi": np.full(n, 50.0),
            "macd": np.full(n, 0.2),
            "macd_signal": np.full(n, 0.1),
            "macd_hist": np.full(n, 0.1),
            "bb_pct_b": np.full(n, 0.5),
            "bb_width": np.full(n, 0.04),
            "bb_upper": np.full(n, 102.0),
            "bb_lower": np.full(n, 98.0),
            "atr": np.full(n, 1.0),
            "adx": np.full(n, 25.0),
            "plus_di": np.full(n, 28.0),
            "minus_di": np.full(n, 15.0),
            "vol_ratio": np.full(n, 1.5),
            "donchian_high": np.full(n, 101.5),
            "donchian_low": np.full(n, 98.5),
        })

    def test_regime_hybrid_in_catalog_and_branches(self):
        self.assertIn("regime_hybrid", STRATEGY_CATALOG)
        df = self._base_ind()
        i = 40

        # 1) Tendencia (ADX >= 22): dispara ruptura Donchian
        df_trend = df.copy()
        df_trend.loc[i, "adx"] = 26.0
        df_trend.loc[i, "close"] = 102.0
        out_t = generate_signals(df_trend, strategy_name="regime_hybrid")
        self.assertEqual(int(out_t.loc[i, "signal"]), 1)
        self.assertIn("Tendencia", str(out_t.loc[i, "signal_reason"]))

        # 2) Rango (ADX < 18): dispara rebote Bollinger
        df_range = df.copy()
        df_range.loc[i, "adx"] = 14.0
        df_range.loc[i - 1, ["close", "bb_pct_b", "rsi"]] = [97.5, 0.05, 32.0]
        df_range.loc[i, ["open", "close", "rsi"]] = [98.0, 99.2, 38.0]
        out_r = generate_signals(df_range, strategy_name="regime_hybrid")
        self.assertEqual(int(out_r.loc[i, "signal"]), 1)
        self.assertIn("Rango", str(out_r.loc[i, "signal_reason"]))

        # 3) Zona muerta (18 <= ADX < 22): aunque haya ruptura o rebote, no dispara
        df_dead = df_trend.copy()
        df_dead.loc[i, "adx"] = 20.0
        out_d = generate_signals(df_dead, strategy_name="regime_hybrid")
        self.assertEqual(int(out_d.loc[i, "signal"]), 0)

    def test_bollinger_blocked_in_trend_and_breakout_blocked_in_range(self):
        df = self._base_ind()
        i = 40
        # Bollinger setup con ADX >= 20 -> bloqueado
        df.loc[i, "adx"] = 24.0
        df.loc[i - 1, ["close", "bb_pct_b", "rsi"]] = [97.5, 0.05, 32.0]
        df.loc[i, ["open", "close", "rsi"]] = [98.0, 99.2, 38.0]
        out_rev = generate_signals(df, strategy_name="bollinger_reversion")
        self.assertEqual(int(out_rev.loc[i, "signal"]), 0)

        # Breakout setup con ADX < 20 -> bloqueado
        df_bo = self._base_ind()
        df_bo.loc[i, ["adx", "close", "bb_width", "macd_hist"]] = [15.0, 103.0, 0.06, 0.5]
        df_bo.loc[i - 1, "macd_hist"] = 0.2
        out_bo = generate_signals(df_bo, strategy_name="volatility_breakout")
        self.assertEqual(int(out_bo.loc[i, "signal"]), 0)


class RiskManagerAndBrokerTests(unittest.TestCase):
    def test_ghost_peak_does_not_halt_and_cooldown_recovers(self):
        rm = RiskManager(RiskConfig())
        # Pico flotante fantasma de 13k pero pico realizado de 10k -> equity 9800 tiene DD realizado 2%, no 24.6%
        ok, risk, why = rm.decide(
            equity=9800.0,
            peak_equity=13000.0,
            realized_peak_equity=10000.0,
            day_start_equity=9800.0,
            open_positions=[],
            symbol="BTC/USDT",
            side="LONG",
            entry=100.0,
            sl=98.0,
            tp=105.0,
            confidence=80.0,
        )
        self.assertTrue(ok, why)
        self.assertGreater(risk, 0.0)

        # DD realizado >= 15% dispara FRENO_COOLDOWN (no deadlock)
        with tempfile.TemporaryDirectory() as tmp:
            broker = LiveExecutionBroker(initial_balance_usd=10000.0, portfolio_file=Path(tmp) / "p.json", paper_only=True)
            broker.state.cash_balance_usd = 8400.0
            broker.state.equity_usd = 8400.0
            broker.state.realized_peak_equity_usd = 10000.0
            broker.save()

            t0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
            ok2, _, why2 = rm.decide(
                equity=8400.0,
                peak_equity=10000.0,
                realized_peak_equity=10000.0,
                day_start_equity=8600.0,
                open_positions=[],
                symbol="BTC/USDT",
                side="LONG",
                entry=100.0,
                sl=98.0,
                tp=105.0,
                confidence=80.0,
                dd_halt_until_utc=broker.state.dd_halt_until_utc,
                dd_recovery_mode=broker.state.dd_recovery_mode,
                now_utc=t0,
            )
            self.assertFalse(ok2)
            self.assertTrue(why2.startswith("FRENO_COOLDOWN"))

            broker.activate_dd_cooldown(24.0, now_utc=t0)
            # Durante las 24h permanece bloqueado
            ok_during, _, why_during = rm.decide(
                equity=8400.0,
                peak_equity=10000.0,
                realized_peak_equity=10000.0,
                day_start_equity=8400.0,
                open_positions=[],
                symbol="BTC/USDT",
                side="LONG",
                entry=100.0,
                sl=98.0,
                tp=105.0,
                confidence=80.0,
                dd_halt_until_utc=broker.state.dd_halt_until_utc,
                dd_recovery_mode=broker.state.dd_recovery_mode,
                now_utc=t0 + timedelta(hours=12),
            )
            self.assertFalse(ok_during)
            self.assertIn("pausa defensiva", why_during)

            # Pasadas 24h reanuda con riesgo mínimo (0.5%) en modo recuperación
            ok_after, risk_after, why_after = rm.decide(
                equity=8400.0,
                peak_equity=10000.0,
                realized_peak_equity=10000.0,
                day_start_equity=8400.0,
                open_positions=[],
                symbol="BTC/USDT",
                side="LONG",
                entry=100.0,
                sl=98.0,
                tp=105.0,
                confidence=90.0,
                dd_halt_until_utc=broker.state.dd_halt_until_utc,
                dd_recovery_mode=broker.state.dd_recovery_mode,
                now_utc=t0 + timedelta(hours=25),
            )
            self.assertTrue(ok_after, why_after)
            self.assertAlmostEqual(risk_after, rm.cfg.min_risk_pct)

            # Al recuperar >= 95% del pico realizado, sale del modo recuperación
            broker.state.cash_balance_usd = 9600.0
            broker.state.equity_usd = 9600.0
            broker.save()
            self.assertFalse(broker.state.dd_recovery_mode)
            self.assertEqual(broker.state.dd_halt_until_utc, "")

    def test_breakeven_buffer_and_trailing_triggers(self):
        # En +1.0R no mueve todavía (trigger es +1.2R)
        sl_1r = RiskManager.manage_stop("LONG", entry=100.0, price=102.0, sl=98.0, atr=1.0, initial_risk=2.0)
        self.assertAlmostEqual(sl_1r, 98.0)

        # En +1.2R mueve a entry * 1.0015 (cubre fee ida/vuelta + slippage)
        sl_be = RiskManager.manage_stop("LONG", entry=100.0, price=102.4, sl=98.0, atr=1.0, initial_risk=2.0)
        self.assertAlmostEqual(sl_be, 100.15)

        # En +1.5R (price=103 -> moved=3.0) activa trailing a price - 1.5*atr = 101.5
        sl_tr = RiskManager.manage_stop("LONG", entry=100.0, price=103.0, sl=100.15, atr=1.0, initial_risk=2.0, trail_atr_mult=1.5)
        self.assertAlmostEqual(sl_tr, 101.5)

        # SHORT BE+buffer en entry * (1 - 0.0015)
        sl_short_be = RiskManager.manage_stop("SHORT", entry=100.0, price=97.6, sl=102.0, atr=1.0, initial_risk=2.0)
        self.assertAlmostEqual(sl_short_be, 99.85)

    def test_crypto_cluster_limit_blocks_third_same_direction(self):
        rm = RiskManager(RiskConfig())
        open_pos = [
            {"symbol": "BTC/USDT", "side": "SHORT", "entry_price": 100.0, "stop_loss": 102.0, "notional_usd": 7500.0},
            {"symbol": "SOL/USDT", "side": "SHORT", "entry_price": 100.0, "stop_loss": 102.0, "notional_usd": 7500.0},
        ]
        ok, _, why = rm.decide(
            equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            open_positions=open_pos,
            symbol="DOGE/USDT",
            side="SHORT",
            entry=100.0,
            sl=102.0,
            tp=95.0,
            confidence=80.0,
        )
        self.assertFalse(ok)
        self.assertIn("CRYPTO", why)

    def test_forex_vs_crypto_min_sl_leverage_and_be_clamping(self):
        rm = RiskManager(RiskConfig())
        # GBP/USD en 15m: SL típico 0.064% (> 0.025% Forex) y nocional > 18k USD (usa palanca Forex 20x)
        ok_fx, risk_fx, why_fx = rm.decide(
            equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            open_positions=[],
            symbol="GBP/USD",
            side="LONG",
            entry=1.25000,
            sl=1.24920,  # 0.064%
            tp=1.25200,  # R:R = 2.5
            confidence=75.0,
        )
        self.assertTrue(ok_fx, why_fx)
        # Con SL 0.064%, tope 10x por posición (50% de 20x Forex) ajusta riesgo a 0.64% (>= min_risk_pct 0.5%)
        # en vez de rechazarlo como haría el tope cripto 4x (0.115% < 0.5%).
        self.assertAlmostEqual(risk_fx, 0.64, places=2)

        ok_fx_full, risk_fx_full, why_fx_full = rm.decide(
            equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            open_positions=[],
            symbol="GBP/USD",
            side="LONG",
            entry=1.25000,
            sl=1.24800,  # 0.16% -> 1.5% riesgo requiere ~9.38x (< 10x cap)
            tp=1.25500,  # R:R = 2.5
            confidence=75.0,
        )
        self.assertTrue(ok_fx_full, why_fx_full)
        self.assertGreaterEqual(risk_fx_full, 1.5)

        # BTC/USDT con SL 0.05% (< 0.08% Cripto) debe seguir bloqueado
        ok_btc, _, why_btc = rm.decide(
            equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            open_positions=[],
            symbol="BTC/USDT",
            side="LONG",
            entry=100.0,
            sl=99.95,  # 0.05%
            tp=100.20,
            confidence=85.0,
        )
        self.assertFalse(ok_btc)
        self.assertIn("demasiado corto", why_btc)

        # Breakeven Forex 0.03% y clamping si buffer exagerado superara price
        sl_fx_be = RiskManager.manage_stop(
            "LONG", entry=1.25000, price=1.25100, sl=1.24920, atr=0.0005,
            initial_risk=0.0008, be_buffer_pct=0.03,
        )
        self.assertAlmostEqual(sl_fx_be, 1.25000 * 1.0003, places=6)

        sl_clamped = RiskManager.manage_stop(
            "LONG", entry=1.25000, price=1.25100, sl=1.24920, atr=0.0005,
            initial_risk=0.0008, be_buffer_pct=0.15,  # 1.251875 > price (1.25100)
        )
        self.assertAlmostEqual(sl_clamped, 1.25000, places=6)

        # LiveExecutionBroker.open_order respeta tope de palanca Forex (20x) sin recortar riesgo
        with tempfile.TemporaryDirectory() as tmp:
            broker = LiveExecutionBroker(
                initial_balance_usd=10000.0,
                portfolio_file=Path(tmp) / "fx.json",
                paper_only=True,
                risk_config=RiskConfig(),
            )
            broker.get_live_price = lambda sym: 1.25000
            ok_ord, msg_ord = broker.open_order(
                symbol="GBP/USD",
                side="LONG",
                strategy="chimichurri_alpha",
                entry_price=1.25000,
                stop_loss=1.24920,
                take_profit=1.25200,
                risk_pct=1.5,
                reason="Test Forex leverage",
                confidence=78.0,
            )
            self.assertTrue(ok_ord, msg_ord)
            pos = broker.state.open_positions[0]
            # 1.5% de $10,000 = $150 riesgo; con SL 0.064% -> nocional esperado ~$234,375 o cap 100k (20x * 0.5 * 10k)
            # Lo clave: NO queda cappeado al tope cripto ($18,000 = 4x * 0.45 * 10k)
            self.assertGreater(float(pos["notional_usd"]), 50000.0)


class WalkForwardAndStrictBasketTests(unittest.TestCase):
    def test_wf_v4_differentiated_costs_and_double_cost_gate(self):
        self.assertEqual(walk_forward.WF_SCHEMA_VERSION, 4)
        wf_cfg = walk_forward.WFConfig()
        self.assertAlmostEqual(wf_cfg.cost_for_symbol("BTC/USDT"), 0.06)
        self.assertAlmostEqual(wf_cfg.cost_for_symbol("GBP/JPY"), 0.012)
        self.assertAlmostEqual(wf_cfg.cost_for_symbol("XAU/USD"), 0.012)
        self.assertNotIn("momentum_shark", walk_forward.WF_STRATEGIES)

        df = _make_ohlcv(900)
        cfg = walk_forward.WFConfig(min_trades_train=5, min_trades_oos=5, min_profit_factor_oos=1.15)

        def fake_sim(part, sl_mult, tp_mult, cost_pct, trail_atr_mult=1.5):
            # Pasa costo normal (0.06), pero falla costo x2 (0.12)
            if cost_pct > 0.08:
                return [-0.2] * 10
            return [1.2, -0.5, 1.5, -0.5, 1.1, 0.8]

        with patch("walk_forward.simulate", side_effect=fake_sim):
            self.assertIsNone(walk_forward.walk_forward_select(df, cfg, symbol="BTC/USDT"))

        def fake_sim_pass(part, sl_mult, tp_mult, cost_pct, trail_atr_mult=1.5):
            return [1.2, -0.5, 1.5, -0.5, 1.1, 0.8] if cost_pct <= 0.08 else [0.4, -0.3, 0.6, -0.2, 0.5, 0.4]

        with patch("walk_forward.simulate", side_effect=fake_sim_pass):
            res = walk_forward.walk_forward_select(df, cfg, symbol="GBP/JPY")
            self.assertIsNotNone(res)
            self.assertEqual(res["schema"], 4)
            self.assertAlmostEqual(res["cost_pct_per_side"], 0.012)
            self.assertIn("oos_double_cost", res)
            self.assertGreater(res["oos_double_cost"]["expectancy"], 0.0)

    def test_strict_wf_only_skips_unapproved_symbols(self):
        df = _make_ohlcv(260)
        # Fuerza una señal en la última vela cerrada de chimichurri_alpha
        fake_cfg = {
            "symbols": {
                "BTC/USDT": {
                    "enabled": True,
                    "wf_approved": False,
                    "strategy": "chimichurri_alpha",
                    "sl_atr_mult": 1.6,
                    "tp_atr_mult": 4.0,
                    "ema_fast": 12,
                    "ema_slow": 26,
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            broker = LiveExecutionBroker(initial_balance_usd=10000.0, portfolio_file=Path(tmp) / "base.json", paper_only=True)
            with patch("overnight_supervisor.load_adaptive_config", return_value=fake_cfg):
                with patch("overnight_supervisor.evaluate_latest_candle") as eval_mock:
                    run_trading_tick(
                        broker,
                        fake_cfg,
                        rm=RiskManager(RiskConfig()),
                        strict_wf_only=True,
                        lock_rm_config=True,
                        history=lambda *a, **kw: (df, "test"),
                        macro=lambda sym: -1,
                        report=lambda msg: None,
                    )
                    eval_mock.assert_not_called()
            self.assertEqual(len(broker.state.open_positions), 0)


class CompareTrialReadOnlyTests(unittest.TestCase):
    def test_compare_archive_trial_7d_readonly(self):
        arc = Path("archive/trial_7d_20261121")
        if not arc.exists():
            self.skipTest("archive/trial_7d_20261121 no presente")
        before = {p.name: p.stat().st_mtime_ns for p in arc.glob("paper_*.json")}
        rep = compare_directory(arc)
        self.assertEqual(len(rep["portfolios"]), 7)
        md = render_markdown(rep)
        self.assertIn("3. Chimichurri", md)
        self.assertIn("Brecha Flotante", md)
        after = {p.name: p.stat().st_mtime_ns for p in arc.glob("paper_*.json")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
