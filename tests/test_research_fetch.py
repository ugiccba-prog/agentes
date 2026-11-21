"""Pruebas de descarga pública con HTTP completamente simulado."""
import unittest
from unittest.mock import patch, Mock
from research.fetch_candles import fetch


class PublicCandleTests(unittest.TestCase):
    def test_invalid_symbol_rejected_without_network(self):
        with patch("research.fetch_candles.requests.get") as get:
            with self.assertRaises(ValueError):
                fetch("../SECRET", "15m")
            get.assert_not_called()

    @patch("research.fetch_candles.requests.get")
    def test_excludes_open_candle_and_verifies_tls(self, get):
        bars = []
        for i in range(801):
            opened = i * 900000
            bars.append([opened, "100", "101", "99", "100", "1000", opened + 899999])
        response = Mock()
        response.json.return_value = bars
        clock = Mock()
        clock.json.return_value = {"serverTime": bars[-1][0] + 100}
        get.side_effect = [response, clock]
        result = fetch("BTCUSDT", "15m", 1000)
        self.assertEqual(len(result), 800)
        self.assertTrue(all(call.kwargs.get("verify") is True for call in get.call_args_list))

    @patch("research.fetch_candles.requests.get")
    def test_too_few_closed_candles_rejected(self, get):
        response = Mock()
        response.json.return_value = [[0, "100", "101", "99", "100", "10", 100]]
        clock = Mock()
        clock.json.return_value = {"serverTime": 1000}
        get.side_effect = [response, clock]
        with self.assertRaisesRegex(ValueError, "Menos de 800"):
            fetch("BTCUSDT", "15m")


if __name__ == "__main__":
    unittest.main()
