"""
Módulo de Datos de Mercado (Cripto y Forex).
Obtiene velas históricas (OHLCV) reales desde Binance API (Cripto) y Yahoo Finance API (Forex/Cripto),
con sistema de caché local para velocidad y respaldo offline.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal
import numpy as np
import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _http_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 10) -> requests.Response:
    """Realiza petición HTTP GET con fallback automático de certificado SSL en Windows/Python 3.15."""
    try:
        return requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError:
        return requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)

CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

MarketType = Literal["crypto", "forex"]

# Catálogo ampliado de pares (incluye Cripto de Alta Volatilidad, Memecoins, Oro y Pares Forex Explosivos)
DEFAULT_SYMBOLS: dict[str, dict[str, str]] = {
    # Criptomonedas Principales y Alta Volatilidad (High-Beta)
    "BTC/USDT": {"market": "crypto", "binance": "BTCUSDT", "yahoo": "BTC-USD", "name": "Bitcoin / USDT"},
    "ETH/USDT": {"market": "crypto", "binance": "ETHUSDT", "yahoo": "ETH-USD", "name": "Ethereum / USDT"},
    "SOL/USDT": {"market": "crypto", "binance": "SOLUSDT", "yahoo": "SOL-USD", "name": "Solana / USDT"},
    "BNB/USDT": {"market": "crypto", "binance": "BNBUSDT", "yahoo": "BNB-USD", "name": "BNB / USDT"},
    "XRP/USDT": {"market": "crypto", "binance": "XRPUSDT", "yahoo": "XRP-USD", "name": "XRP / USDT"},
    "DOGE/USDT": {"market": "crypto", "binance": "DOGEUSDT", "yahoo": "DOGE-USD", "name": "Dogecoin / USDT (Alta Volatilidad)"},
    "SUI/USDT": {"market": "crypto", "binance": "SUIUSDT", "yahoo": "SUI20947-USD", "name": "Sui / USDT (Explosiva)"},
    "AVAX/USDT": {"market": "crypto", "binance": "AVAXUSDT", "yahoo": "AVAX-USD", "name": "Avalanche / USDT"},
    "LINK/USDT": {"market": "crypto", "binance": "LINKUSDT", "yahoo": "LINK-USD", "name": "Chainlink / USDT"},
    "PEPE/USDT": {"market": "crypto", "binance": "PEPEUSDT", "yahoo": "PEPE24478-USD", "name": "Pepe / USDT (Memecoin Volátil)"},
    "ADA/USDT": {"market": "crypto", "binance": "ADAUSDT", "yahoo": "ADA-USD", "name": "Cardano / USDT"},
    # Oro y Forex de Alto Rango Diario
    "XAU/USD": {"market": "forex", "binance": "", "yahoo": "GC=F", "name": "Oro / Dólar (Gold Futures)"},
    "GBP/JPY": {"market": "forex", "binance": "", "yahoo": "GBPJPY=X", "name": "Libra / Yen ('La Bestia' Forex)"},
    "EUR/JPY": {"market": "forex", "binance": "", "yahoo": "EURJPY=X", "name": "Euro / Yen"},
    "EUR/USD": {"market": "forex", "binance": "", "yahoo": "EURUSD=X", "name": "Euro / Dólar (EUR/USD)"},
    "GBP/USD": {"market": "forex", "binance": "", "yahoo": "GBPUSD=X", "name": "Libra / Dólar (GBP/USD)"},
    "USD/JPY": {"market": "forex", "binance": "", "yahoo": "JPY=X", "name": "Dólar / Yen (USD/JPY)"},
    "AUD/USD": {"market": "forex", "binance": "", "yahoo": "AUDUSD=X", "name": "Dólar Australiano / USD"},
}


def get_market_type(symbol: str) -> MarketType:
    if symbol in DEFAULT_SYMBOLS:
        return DEFAULT_SYMBOLS[symbol]["market"]  # type: ignore[return-value]
    if "USDT" in symbol or "BTC" in symbol or "ETH" in symbol or "SOL" in symbol:
        return "crypto"
    return "forex"


def fetch_binance_klines(symbol: str, interval: str = "1h", limit: int = 1000) -> pd.DataFrame | None:
    """Descarga datos OHLCV históricos reales desde el endpoint público de Binance."""
    binance_sym = DEFAULT_SYMBOLS.get(symbol, {}).get("binance", symbol.replace("/", ""))
    if not binance_sym:
        return None

    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://data-api.binance.vision/api/v3/klines",
    ]
    params = {"symbol": binance_sym, "interval": interval, "limit": min(limit, 1000)}

    for url in endpoints:
        try:
            resp = _http_get(url, params=params, timeout=10)
            if resp.status_code == 200:
                raw = resp.json()
                if not raw or not isinstance(raw, list):
                    continue
                df = pd.DataFrame(
                    raw,
                    columns=[
                        "open_time", "open", "high", "low", "close", "volume",
                        "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore",
                    ],
                )
                df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                df = df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values("timestamp").reset_index(drop=True)
                return df
        except Exception:
            continue
    return None


def fetch_yahoo_chart(symbol: str, interval: str = "1h", limit: int = 1000) -> pd.DataFrame | None:
    """Descarga datos OHLCV reales desde la API pública v8 de Yahoo Finance (ideal para Forex y Cripto)."""
    yahoo_sym = DEFAULT_SYMBOLS.get(symbol, {}).get("yahoo", symbol)
    interval_map = {
        "15m": ("15m", "60d"),
        "1h": ("1h", "180d"),
        "4h": ("1h", "365d"),  # Se re-muestrea a 4h si es necesario
        "1d": ("1d", "5y"),
    }
    yf_interval, yf_range = interval_map.get(interval, ("1h", "180d"))

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    params = {"interval": yf_interval, "range": yf_range}

    try:
        resp = _http_get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        res = result[0]
        timestamps = res.get("timestamp", [])
        quote = res.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps or not quote:
            return None

        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": pd.Series(quote.get("open", []), dtype="float64"),
                "high": pd.Series(quote.get("high", []), dtype="float64"),
                "low": pd.Series(quote.get("low", []), dtype="float64"),
                "close": pd.Series(quote.get("close", []), dtype="float64"),
                "volume": pd.Series(quote.get("volume", []), dtype="float64").fillna(0.0),
            }
        )
        df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)

        if interval == "4h" and yf_interval == "1h" and not df.empty:
            df = (
                df.set_index("timestamp")
                .resample("4h")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna()
                .reset_index()
            )

        if len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df
    except Exception:
        return None


def generate_synthetic_ohlcv(symbol: str, interval: str = "1h", limit: int = 1000) -> pd.DataFrame:
    """
    Generador de respaldo con regímenes de mercado realistas (tendencias, consolidaciones y volatilidad GARCH-like)
    por si el entorno no tiene conexión a internet en el momento de la prueba.
    """
    seed = int(hashlib.sha256(f"{symbol}_{interval}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    market = get_market_type(symbol)
    base_prices = {
        "BTC/USDT": 68000.0,
        "ETH/USDT": 3400.0,
        "SOL/USDT": 165.0,
        "BNB/USDT": 590.0,
        "EUR/USD": 1.0850,
        "GBP/USD": 1.2720,
        "USD/JPY": 153.50,
        "AUD/USD": 0.6580,
    }
    price = base_prices.get(symbol, 100.0 if market == "crypto" else 1.1000)
    base_vol = 0.009 if market == "crypto" else 0.0018

    freq_map = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
    freq = freq_map.get(interval, "1h")
    end_time = pd.Timestamp.now(tz="UTC").floor(freq)
    timestamps = pd.date_range(end=end_time, periods=limit, freq=freq)

    opens, highs, lows, closes, volumes = [], [], [], [], []
    regime_len = 0
    drift = 0.0
    vol_mult = 1.0

    for _ in range(limit):
        if regime_len <= 0:
            regime = rng.choice(["bull", "bear", "range", "breakout"], p=[0.32, 0.25, 0.33, 0.10])
            regime_len = int(rng.integers(25, 95))
            if regime == "bull":
                drift = base_vol * 0.22
                vol_mult = 1.0
            elif regime == "bear":
                drift = -base_vol * 0.20
                vol_mult = 1.15
            elif regime == "range":
                drift = 0.0
                vol_mult = 0.70
            else:
                drift = base_vol * rng.choice([-0.45, 0.50])
                vol_mult = 1.65
        regime_len -= 1

        ret = drift + rng.normal(0, base_vol * vol_mult)
        open_p = price
        close_p = max(open_p * (1.0 + ret), 1e-6)
        wick_up = abs(rng.normal(0, base_vol * 0.65 * vol_mult))
        wick_dn = abs(rng.normal(0, base_vol * 0.65 * vol_mult))
        high_p = max(open_p, close_p) * (1.0 + wick_up)
        low_p = min(open_p, close_p) * (1.0 - wick_dn)
        vol = float(rng.lognormal(mean=10.0, sigma=0.6) * vol_mult)

        opens.append(open_p)
        highs.append(high_p)
        lows.append(low_p)
        closes.append(close_p)
        volumes.append(vol)
        price = close_p

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def get_historical_data(
    symbol: str,
    interval: str = "1h",
    limit: int = 800,
    use_cache: bool = True,
) -> tuple[pd.DataFrame, str]:
    """
    Obtiene datos históricos OHLCV para cualquier par Cripto o Forex.
    Retorna `(dataframe, fuente_de_datos)`.
    """
    safe_sym = symbol.replace("/", "_").replace("=", "")
    cache_file = CACHE_DIR / f"{safe_sym}_{interval}_{limit}.csv"

    market = get_market_type(symbol)

    df: pd.DataFrame | None = None
    source = ""

    if market == "crypto":
        df = fetch_binance_klines(symbol, interval=interval, limit=limit)
        if df is not None and not df.empty:
            source = "Binance API (En Vivo)"

    if df is None or df.empty:
        df = fetch_yahoo_chart(symbol, interval=interval, limit=limit)
        if df is not None and not df.empty:
            source = "Yahoo Finance API (En Vivo)"

    if df is not None and not df.empty:
        if use_cache:
            df.to_csv(cache_file, index=False)
        return df, source

    if use_cache and cache_file.exists():
        cached_df = pd.read_csv(cache_file, parse_dates=["timestamp"])
        if not cached_df.empty:
            return cached_df, "Caché Local"

    synth_df = generate_synthetic_ohlcv(symbol, interval=interval, limit=limit)
    return synth_df, "Simulador Cuantitativo (Offline)"
