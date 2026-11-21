"""Descarga optativa de velas públicas Binance (sin credenciales ni órdenes).

Descarta la vela en formación. No escribe datos de carteras.
Uso: python -m research.fetch_candles --symbol BTCUSDT --interval 15m --output research_data/BTCUSDT.csv
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import requests

URL = "https://api.binance.com/api/v3/klines"
INTERVALS = {"5m", "15m", "1h", "4h", "1d"}


def fetch(symbol: str, interval: str, limit: int = 1000) -> list[dict]:
    if not symbol.isascii() or not symbol.isalnum() or len(symbol) > 20:
        raise ValueError("Símbolo inválido")
    if interval not in INTERVALS or not 800 <= limit <= 1000:
        raise ValueError("Intervalo o cantidad inválida (800-1000)")
    response = requests.get(URL, params={
        "symbol": symbol.upper(), "interval": interval, "limit": limit,
    }, timeout=20, verify=True)
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, list):
        raise ValueError("Respuesta inesperada de Binance")
    # Tiempo del servidor: no confiar en el reloj local para determinar cierre.
    server = requests.get("https://api.binance.com/api/v3/time", timeout=10, verify=True)
    server.raise_for_status()
    now_ms = int(server.json()["serverTime"])
    rows = []
    for bar in raw:
        if not isinstance(bar, list) or len(bar) < 7:
            raise ValueError("Vela incompleta")
        if int(bar[6]) >= now_ms:
            continue
        rows.append({
            "timestamp": datetime.fromtimestamp(int(bar[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": bar[1], "high": bar[2], "low": bar[3],
            "close": bar[4], "volume": bar[5],
        })
    if len(rows) < 800:
        raise ValueError("Menos de 800 velas cerradas; no se guarda archivo")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Descargar solo velas públicas cerradas")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m", choices=sorted(INTERVALS))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = fetch(args.symbol, args.interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=("timestamp", "open", "high", "low", "close", "volume"))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Guardadas {len(rows)} velas cerradas en {args.output}")


if __name__ == "__main__":
    main()
