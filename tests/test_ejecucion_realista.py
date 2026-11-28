"""Pruebas: SL/TP como órdenes en reposo, filtro de costos en R, granularidad de precio,
ATR compartido entre opciones y lectura estadística del README."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from cloud_runner import _r_stats
from exchange_connector import LiveExecutionBroker, resolve_resting_exit, shared_path_provider
import overnight_supervisor
from risk_manager import RiskConfig, RiskManager, estimate_price_tick

FMT = "%Y-%m-%d %H:%M:%S UTC"


def _bars(start: datetime, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    ts = [start + timedelta(minutes=i) for i in range(len(rows))]
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts, utc=True),
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "volume": [1.0] * len(rows),
    })


T0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)


class RestingExitTests(unittest.TestCase):
    def test_wick_touch_fills_at_stop_with_slippage(self):
        bars = _bars(T0, [(100.0, 100.5, 99.5, 100.2), (100.2, 100.4, 97.9, 99.8)])
        price, why = resolve_resting_exit("LONG", 98.0, 105.0, bars, T0, slippage_pct=0.01)
        self.assertAlmostEqual(price, 98.0 * (1 - 0.0001))
        self.assertIn("Stop Loss", why)

    def test_gap_fills_at_open(self):
        bars = _bars(T0, [(97.0, 97.5, 96.5, 97.2)])
        price, why = resolve_resting_exit("LONG", 98.0, 105.0, bars, T0, slippage_pct=0.01)
        self.assertEqual(price, 97.0)
        self.assertIn("gap", why)

    def test_stop_first_when_both_in_same_bar(self):
        bars = _bars(T0, [(100.0, 106.0, 97.0, 101.0)])
        _, why = resolve_resting_exit("LONG", 98.0, 105.0, bars, T0)
        self.assertIn("Stop Loss", why)

    def test_take_profit_fills_exactly_at_target(self):
        bars = _bars(T0, [(100.0, 103.0, 99.5, 102.0), (102.0, 107.0, 101.8, 106.5)])
        price, why = resolve_resting_exit("LONG", 98.0, 105.0, bars, T0)
        self.assertEqual(price, 105.0)
        self.assertEqual(why, "Take Profit Alcanzado")

    def test_bars_before_since_are_ignored(self):
        # La mecha a 97 ocurrió antes de la entrada / del último ajuste: no puede disparar.
        bars = _bars(T0, [(100.0, 100.5, 97.0, 100.2), (100.2, 100.6, 99.8, 100.4)])
        self.assertIsNone(resolve_resting_exit("LONG", 98.0, 105.0, bars, T0 + timedelta(seconds=30)))

    def test_short_side_symmetry(self):
        bars = _bars(T0, [(100.0, 102.1, 99.6, 100.5)])
        price, why = resolve_resting_exit("SHORT", 102.0, 95.0, bars, T0, slippage_pct=0.01)
        self.assertAlmostEqual(price, 102.0 * 1.0001)
        self.assertIn("Stop Loss", why)
        bars_tp = _bars(T0, [(100.0, 100.2, 94.0, 94.5)])
        self.assertEqual(resolve_resting_exit("SHORT", 102.0, 95.0, bars_tp, T0)[0], 95.0)

    def test_no_bars_returns_none(self):
        self.assertIsNone(resolve_resting_exit("LONG", 98.0, 105.0, None, T0))
        self.assertIsNone(resolve_resting_exit("LONG", 98.0, 105.0, pd.DataFrame(), T0))

    def test_shared_path_provider_downloads_once_per_bucket(self):
        calls = []
        provider = shared_path_provider(lambda s: calls.append(s) or s, ttl_seconds=3600)
        provider("BTC/USDT")
        provider("BTC/USDT")
        provider("SOL/USDT")
        self.assertEqual(calls, ["BTC/USDT", "SOL/USDT"])


class BrokerFillTests(unittest.TestCase):
    def _broker(self, tmp: str) -> LiveExecutionBroker:
        return LiveExecutionBroker(
            initial_balance_usd=10000.0, portfolio_file=Path(tmp) / "p.json",
            paper_only=True, risk_config=RiskConfig(),
        )

    @staticmethod
    def _position(stop_since: str | None, side: str = "LONG") -> dict:
        now = datetime.now(tz=timezone.utc)
        pos = {
            "id": "ORD-1", "symbol": "BTC/USDT", "market": "CRYPTO", "strategy": "chimichurri_alpha",
            "side": side, "entry_time": (now - timedelta(minutes=30)).strftime(FMT),
            "entry_price": 100.0, "current_price": 100.0, "size": 10.0, "notional_usd": 1000.0,
            "stop_loss": 98.0 if side == "LONG" else 102.0,
            "take_profit": 105.0 if side == "LONG" else 95.0,
            "initial_risk_dist": 2.0, "unrealized_pnl_usd": 0.0, "unrealized_pnl_pct": 0.0,
            "reason": "test", "broker_mode": "Paper Trading En Vivo (Datos Reales)",
        }
        if stop_since is not None:
            pos["stop_since_utc"] = stop_since
        return pos

    def test_wick_between_pulses_closes_at_stop_not_at_late_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
            b.state.open_positions = [self._position((now - timedelta(minutes=3)).strftime(FMT))]
            # Mecha a 97.5 hace 2 minutos; el precio actual ya volvió a 99.
            b.get_price_path = lambda s: _bars(now - timedelta(minutes=2), [(99.5, 99.8, 97.5, 98.9), (98.9, 99.2, 98.7, 99.0)])
            b.get_live_price = lambda s: 99.0
            b.update_open_positions()
            self.assertEqual(b.state.open_positions, [])
            t = b.state.closed_trades[-1]
            self.assertAlmostEqual(t["exit_price"], 98.0 * (1 - 0.0001), places=6)
            self.assertIn("intravela", t["exit_reason"])
            # Pérdida ~ -1R más comisiones, no la pérdida inflada de llenar tarde.
            self.assertGreater(t["r_multiple"], -1.2)
            self.assertLess(t["r_multiple"], -1.0)

    def test_legacy_position_is_not_closed_retroactively(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
            b.state.open_positions = [self._position(None)]
            b.get_price_path = lambda s: _bars(now - timedelta(minutes=5), [(99.0, 99.5, 97.0, 99.2)])
            b.get_live_price = lambda s: 99.2
            b.update_open_positions()
            self.assertEqual(len(b.state.open_positions), 1)
            self.assertTrue(b.state.open_positions[0]["stop_since_utc"])

    def test_take_profit_without_path_never_fills_better_than_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            b.state.open_positions = [self._position(datetime.now(tz=timezone.utc).strftime(FMT))]
            b.get_price_path = lambda s: None
            b.get_live_price = lambda s: 106.3
            b.update_open_positions()
            self.assertEqual(b.state.closed_trades[-1]["exit_price"], 105.0)

    def test_stop_without_path_keeps_conservative_late_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            b.state.open_positions = [self._position(datetime.now(tz=timezone.utc).strftime(FMT), side="SHORT")]
            b.get_price_path = lambda s: None
            b.get_live_price = lambda s: 102.6
            b.update_open_positions()
            self.assertEqual(b.state.closed_trades[-1]["exit_price"], 102.6)

    def test_new_orders_carry_stop_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            b.get_live_price = lambda s: 100.0
            ok, msg = b.open_order("BTC/USDT", "LONG", "chimichurri_alpha", 100.0, 99.0, 103.0, risk_pct=1.0)
            self.assertTrue(ok, msg)
            self.assertTrue(b.state.open_positions[0]["stop_since_utc"])

    def test_bars_seen_before_with_same_stop_are_rechecked(self):
        # Sin huecos: la vela que estaba en curso en el pulso anterior se vuelve a revisar completa.
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
            b.state.open_positions = [self._position((now - timedelta(minutes=10)).strftime(FMT))]
            b.get_price_path = lambda s: _bars(now - timedelta(minutes=10), [(99.5, 99.9, 99.1, 99.4)] * 9 + [(99.4, 99.6, 97.9, 99.3)])
            b.get_live_price = lambda s: 99.3
            b.update_open_positions()
            self.assertEqual(b.state.open_positions, [])

    def test_moving_the_stop_resets_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._broker(tmp)
            now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
            b.state.open_positions = [self._position((now - timedelta(minutes=20)).strftime(FMT))]
            # +1.6R: trailing sube el stop a 103.2 - 1.5 * ATR(1.0) = 101.7
            b.get_price_path = lambda s: None
            b.get_live_price = lambda s: 103.2
            b.update_open_positions(atr_by_symbol={"BTC/USDT": 1.0})
            pos = b.state.open_positions[0]
            self.assertAlmostEqual(pos["stop_loss"], 101.7)
            # Una vela que empezó antes del ajuste bajó a 101.0: comparada contra el stop viejo (98)
            # no tocó nada, así que no puede cerrar contra el stop nuevo.
            b.get_price_path = lambda s: _bars(now - timedelta(minutes=1), [(102.0, 103.3, 101.0, 102.6)])
            b.get_live_price = lambda s: 102.6
            b.update_open_positions(atr_by_symbol={"BTC/USDT": 1.0})
            self.assertEqual(len(b.state.open_positions), 1)


class CostAndGranularityGateTests(unittest.TestCase):
    def _decide(self, symbol: str, entry: float, sl: float, tp: float, price_tick=None):
        return RiskManager(RiskConfig()).decide(
            equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0, open_positions=[],
            symbol=symbol, side="LONG", entry=entry, sl=sl, tp=tp, confidence=80.0, price_tick=price_tick,
        )

    def test_cost_gate_blocks_stops_that_fees_eat(self):
        # Stop 0.25% en cripto: 0.11% ida y vuelta = 0.44R de costo -> rechazar.
        ok, _, why = self._decide("BTC/USDT", 100.0, 99.75, 101.0)
        self.assertFalse(ok)
        self.assertIn("costos", why)
        # Stop 0.6%: 0.18R -> pasa.
        ok2, _, why2 = self._decide("BTC/USDT", 100.0, 99.4, 101.5)
        self.assertTrue(ok2, why2)

    def test_granularity_blocks_pepe_like_prices(self):
        closes = [3.40e-6, 3.41e-6, 3.39e-6, 3.42e-6, 3.40e-6, 3.43e-6]
        tick = estimate_price_tick(closes)
        self.assertAlmostEqual(tick, 1e-8, delta=1e-12)
        # Stop de 1.4% en un precio con tick de 0.29%: 1 tick = 21% del stop.
        ok, _, why = self._decide("PEPE/USDT", 3.40e-6, 3.40e-6 * 0.986, 3.40e-6 * 1.04, price_tick=tick)
        self.assertFalse(ok)
        self.assertIn("granular", why)

    def test_granularity_allows_liquid_markets(self):
        tick = estimate_price_tick([90000.00, 90000.01, 90000.03, 89999.99])
        self.assertAlmostEqual(tick, 0.01, places=6)
        ok, _, why = self._decide("BTC/USDT", 90000.0, 89400.0, 91500.0, price_tick=tick)
        self.assertTrue(ok, why)

    def test_estimate_tick_handles_bad_input(self):
        self.assertIsNone(estimate_price_tick([]))
        self.assertIsNone(estimate_price_tick([1.0, 1.0, float("nan")]))


class SharedAtrTests(unittest.TestCase):
    def test_atr_map_merges_all_options(self):
        maps = iter([{"SUI/USDT": 0.01}, {"BTC/USDT": 300.0, "SUI/USDT": 0.01}, {"PEPE/USDT": 1e-8}])

        class _B:
            class state:
                equity_usd = 10000.0
                open_positions: list = []
                total_trades = 0

        runners = [({"short_name": f"o{i}", "key": f"k{i}"}, _B(), None) for i in range(3)]
        with patch.object(overnight_supervisor, "run_trading_tick", side_effect=lambda *a, **k: next(maps)), \
                patch.object(overnight_supervisor, "sync_base_to_live_portfolio"):
            out = overnight_supervisor.run_all_options_tick(runners, {}, report=lambda m: None)
        self.assertEqual(set(out), {"SUI/USDT", "BTC/USDT", "PEPE/USDT"})


class StatsTests(unittest.TestCase):
    def test_r_stats_splits_gross_costs_and_interval(self):
        trades = []
        for i in range(20):
            win = i % 2 == 0
            trades.append({
                "market": "CRYPTO", "size": 10.0, "entry_price": 100.0,
                "exit_price": 104.0 if win else 98.0, "risk_usd": 20.0,
                "r_multiple": 1.9 if win else -1.1,
            })
        s = _r_stats(trades)
        self.assertEqual(s["n"], 20)
        self.assertAlmostEqual(s["net_r"], 10 * 1.9 - 10 * 1.1)
        # comisión por trade = (100 + salida) * 10 * 0.0005 / 20 R
        expected_cost = sum((100.0 + t["exit_price"]) * 10 * 0.0005 / 20.0 for t in trades)
        self.assertAlmostEqual(s["cost_r"], expected_cost)
        self.assertAlmostEqual(s["gross_r"], s["net_r"] + s["cost_r"])
        self.assertLess(s["lo"], s["exp_r"])
        self.assertGreater(s["hi"], s["exp_r"])

    def test_small_sample_is_flagged(self):
        s = _r_stats([{"market": "FOREX", "size": 1.0, "entry_price": 1.0, "exit_price": 1.0, "risk_usd": 1.0, "r_multiple": 2.0}])
        self.assertEqual(s["verdict"], "⏳ Muestra chica")


if __name__ == "__main__":
    unittest.main()
