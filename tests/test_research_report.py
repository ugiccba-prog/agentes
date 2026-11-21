"""El informe nunca se presenta como recomendación operativa."""
import unittest
from research.report import render


class ReportTests(unittest.TestCase):
    def test_rejects_operational_report(self):
        with self.assertRaises(ValueError):
            render({"mode": "live_trading", "candidates": []})

    def test_positive_candidate_remains_unvalidated(self):
        sample = {
            "mode": "offline_research_only",
            "source_csv": "synthetic.csv",
            "candidates": [{
                "strategy": "trend_macd",
                "status": "experimental",
                "preliminary_pass": True,
                "test": {"normal": {"n": 12, "pf": 1.2, "expectancy": 0.1}},
                "checks": {"test_survives_double_cost": True},
            }],
        }
        text = render(sample)
        self.assertIn("SIN validar", text)
        self.assertIn("NO operativo", text)
        self.assertIn("trend_macd", text)

    def test_error_candidate_is_visible(self):
        report = {"mode": "offline_research_only", "candidates": [
            {"strategy": "demo", "status": "error", "error": "invalid candles"}]}
        self.assertIn("Error de datos/cálculo", render(report))


if __name__ == "__main__":
    unittest.main()
