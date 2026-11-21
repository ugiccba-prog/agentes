"""Pruebas del investigador offline: no se ejecutan brokers ni consultas de red."""
import unittest
from pathlib import Path
from unittest.mock import patch

from research.nightly import scan


class NightlyTests(unittest.TestCase):
    def test_rejects_excessive_candidates(self):
        with self.assertRaises(ValueError):
            scan(Path("no-existe.csv"), max_candidates=31)

    @patch("research.nightly.run")
    @patch("research.nightly.STRATEGY_CATALOG", {"demo": object()})
    def test_never_promotes_candidate(self, run):
        run.return_value = {
            "preliminary_checks": {"test_profitable": True},
            "segments": {"test": {"n": 20}},
            "parameters": {},
            "source_sha256": "synthetic-test-hash",
            "limitations": [],
        }
        report = scan(Path("sample.csv"), max_candidates=1)
        self.assertEqual(report["mode"], "offline_research_only")
        self.assertEqual(report["candidates"][0]["status"], "experimental")
        self.assertTrue(report["candidates"][0]["preliminary_pass"])

    @patch("research.nightly.run", side_effect=ValueError("Datos inválidos"))
    @patch("research.nightly.STRATEGY_CATALOG", {"demo": object()})
    def test_reports_invalid_data(self, run):
        report = scan(Path("bad.csv"), max_candidates=1)
        self.assertEqual(report["candidates"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
