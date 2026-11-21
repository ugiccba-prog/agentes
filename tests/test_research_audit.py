"""Pruebas de aislamiento de cambios del PR."""
import unittest
from research.audit_changes import audit, permitted


class ChangeAuditTests(unittest.TestCase):
    def test_allows_research_and_tests(self):
        self.assertTrue(permitted("research/backtest.py"))
        self.assertTrue(permitted("tests/test_research_backtest.py"))
        self.assertTrue(permitted("market_data.py"))
        self.assertTrue(permitted(".github/workflows/research-lab.yml"))

    def test_blocks_portfolios_and_production(self):
        forbidden = ["live_portfolio.json", "portfolios/berserker.json",
                     "live_trader.py", ".env", "adaptive_config.json",
                     "tests/test_other.py", "../live_trader.py"]
        self.assertEqual(audit(forbidden), forbidden)

    def test_empty_diff_allowed(self):
        self.assertEqual(audit([]), [])


if __name__ == "__main__":
    unittest.main()
