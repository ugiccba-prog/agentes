"""Pruebas deterministas de stops sin mirar el futuro."""
import unittest
import numpy as np
import pandas as pd
from research.simulator import simulate


def candles(*, signal=1):
    n = 206
    df = pd.DataFrame({
        "open": np.full(n, 100.0),
        "high": np.full(n, 100.5),
        "low": np.full(n, 99.5),
        "close": np.full(n, 100.0),
        "atr": np.full(n, 1.0),
        "signal": np.zeros(n, dtype=int),
    })
    df.loc[200, "signal"] = signal
    return df


class ConservativeSimulatorTests(unittest.TestCase):
    def test_stop_first_when_both_touched(self):
        df = candles()
        df.loc[201, "high"] = 105.0
        df.loc[201, "low"] = 98.0
        rs = simulate(df, 1.0, 2.0, 0)
        self.assertEqual(len(rs), 1)
        self.assertAlmostEqual(rs[0], -1.0)

    def test_trailing_not_applied_intrabar(self):
        df = candles()
        df.loc[201, ["high", "low", "close"]] = [103.0, 99.5, 102.5]
        df.loc[202, ["open", "high", "low", "close"]] = [102.0, 102.1, 100.5, 101.0]
        rs = simulate(df, 1.0, 5.0, 0)
        self.assertEqual(len(rs), 1)
        # Trail activado después del cierre 201: stop en 101.0, fill 101.0.
        self.assertAlmostEqual(rs[0], 1.0)

    def test_adverse_gap_fills_at_open(self):
        df = candles()
        df.loc[201, ["open", "high", "low", "close"]] = [98.0, 99.0, 97.0, 98.0]
        rs = simulate(df, 1.0, 2.0, 0)
        self.assertEqual(len(rs), 1)
        self.assertAlmostEqual(rs[0], -2.0)


if __name__ == "__main__":
    unittest.main()
