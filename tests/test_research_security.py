"""Pruebas sin conexión a Internet ni intercambio de órdenes."""
import unittest
from unittest.mock import patch, Mock
from research.gates import evaluate
from exchange_connector import BinanceTestnetConnector, OandaForexDemoConnector


class GateTests(unittest.TestCase):
    def test_missing_evidence_never_approves(self):
        result = evaluate({"candidate_id": "demo", "gates": {}})
        self.assertFalse(result["all_gates_passed"])
        self.assertEqual(result["status"], "experimental")

    def test_all_seven_require_explicit_evidence(self):
        names = ("reason", "costs", "out_of_sample", "robustness", "stress", "baseline", "sample")
        gates = {n: {"passed": True, "evidence": "documentado"} for n in names}
        self.assertTrue(evaluate({"candidate_id": "demo", "gates": gates})["all_gates_passed"])
        gates["costs"]["evidence"] = ""
        self.assertFalse(evaluate({"candidate_id": "demo", "gates": gates})["all_gates_passed"])

    def test_nonboolean_pass_is_rejected(self):
        names = ("reason", "costs", "out_of_sample", "robustness", "stress", "baseline", "sample")
        gates = {n: {"passed": "true", "evidence": "texto"} for n in names}
        self.assertFalse(evaluate({"candidate_id": "demo", "gates": gates})["all_gates_passed"])


class TransportTests(unittest.TestCase):
    @patch("exchange_connector.requests.post")
    def test_binance_testnet_verifies_tls(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {}
        BinanceTestnetConnector("fake", "fake").place_order("BTC/USDT", "LONG", 1)
        self.assertIs(post.call_args.kwargs["verify"], True)

    @patch("exchange_connector.requests.post")
    def test_oanda_demo_verifies_tls(self, post):
        post.return_value = Mock(status_code=201)
        post.return_value.json.return_value = {}
        OandaForexDemoConnector("fake", "fake").place_order("EUR/USD", "LONG", 100, 1.0, 1.2)
        self.assertIs(post.call_args.kwargs["verify"], True)



class PaperIsolationTests(unittest.TestCase):
    @patch.dict("os.environ", {
        "BINANCE_TESTNET_API_KEY": "dummy",
        "BINANCE_TESTNET_API_SECRET": "dummy",
        "OANDA_DEMO_API_TOKEN": "dummy",
        "OANDA_DEMO_ACCOUNT_ID": "dummy",
    })
    def test_paper_only_never_initializes_external_connectors(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from exchange_connector import LiveExecutionBroker
        with TemporaryDirectory() as folder:
            broker = LiveExecutionBroker(portfolio_file=Path(folder) / "paper.json", paper_only=True)
            self.assertIsNone(broker.binance_client)
            self.assertIsNone(broker.oanda_client)


if __name__ == "__main__":
    unittest.main()
