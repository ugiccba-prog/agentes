"""Pruebas aisladas del laboratorio, sin datos externos ni órdenes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from research.backtest import run


class ResearchBacktestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "candles.csv"

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_missing_columns(self):
        pd.DataFrame({"timestamp": ["2026-01-01"]}).to_csv(self.path, index=False)
        from strategies import STRATEGY_CATALOG
        with self.assertRaisesRegex(ValueError, "Columnas"):
            run(self.path, next(iter(STRATEGY_CATALOG)))

    def test_invalid_strategy_rejected(self):
        with self.assertRaisesRegex(ValueError, "Estrategia desconocida"):
            run(self.path, "not-a-strategy")

    def test_synthetic_end_to_end_never_claims_validation(self):
        from strategies import STRATEGY_CATALOG
        import numpy as np
        n = 850
        x = np.arange(n, dtype=float)
        close = 100 + 0.015 * x + 2.0 * np.sin(x / 13.0)
        df = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        })
        df.to_csv(self.path, index=False)
        result = run(self.path, next(iter(STRATEGY_CATALOG)))
        self.assertEqual(result["status"], "experimental")
        self.assertEqual(set(result["segments"]), {"train", "validation", "test"})
        self.assertEqual(result["bars"], n)

    def test_rejects_duplicate_timestamps_before_simulation(self):
        from strategies import STRATEGY_CATALOG
        strategy = next(iter(STRATEGY_CATALOG))
        df = pd.DataFrame({
            "timestamp": ["2026-01-01T00:00:00Z"] * 800,
            "open": [100.0] * 800, "high": [101.0] * 800,
            "low": [99.0] * 800, "close": [100.0] * 800,
            "volume": [10.0] * 800,
        })
        df.to_csv(self.path, index=False)
        with patch("research.backtest.simulate") as simulate:
            with self.assertRaisesRegex(ValueError, "duplicadas"):
                run(self.path, strategy)
            simulate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
