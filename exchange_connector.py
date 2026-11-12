"""
Conector de Ejecución en Tiempo Real para Cripto y Forex:
1. LocalPaperBroker (Por defecto): Opera en tiempo real con precios en vivo de Binance/Yahoo Finance
   y persiste tu cartera, saldo, posiciones abiertas e historial en `live_portfolio.json`.
2. BinanceTestnetConnector: Conector nativo HMAC-SHA256 para Binance Spot/Futures Testnet.
3. OandaForexDemoConnector: Conector REST v20 para cuentas Demo de Forex en OANDA.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode

from market_data import _http_get, get_historical_data, get_market_type
import requests

PORTFOLIO_FILE = Path(__file__).parent / "live_portfolio.json"


def load_env_file() -> dict[str, str]:
    """Carga variables desde .env si existe en el directorio del proyecto."""
    env_path = Path(__file__).parent / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            env_vars[k] = v
            os.environ.setdefault(k, v)
    return env_vars


@dataclass
class OpenPosition:
    id: str
    symbol: str
    market: str
    strategy: str
    side: str  # "LONG" o "SHORT"
    entry_time: str
    entry_price: float
    current_price: float
    size: float
    notional_usd: float
    stop_loss: float
    take_profit: float
    initial_risk_dist: float
    unrealized_pnl_usd: float
    unrealized_pnl_pct: float
    reason: str
    broker_mode: str


@dataclass
class ClosedLiveTrade:
    id: str
    symbol: str
    market: str
    strategy: str
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    size: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str
    broker_mode: str


@dataclass
class PortfolioState:
    initial_balance_usd: float = 10000.0
    cash_balance_usd: float = 10000.0
    equity_usd: float = 10000.0
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    last_updated: str = ""
    open_positions: list[dict] = field(default_factory=list)
    closed_trades: list[dict] = field(default_factory=list)


class BinanceTestnetConnector:
    """Conector oficial firmado con HMAC-SHA256 para Binance Spot / Futures Testnet."""

    def __init__(self, api_key: str, api_secret: str, use_futures: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.use_futures = use_futures
        self.base_url = (
            "https://testnet.binancefuture.com"
            if use_futures
            else "https://testnet.binance.vision"
        )

    def _sign_params(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{query}&signature={signature}"

    def place_order(self, symbol: str, side: str, quantity: float) -> dict:
        binance_sym = symbol.replace("/", "")
        endpoint = "/fapi/v1/order" if self.use_futures else "/api/v3/order"
        params = {
            "symbol": binance_sym,
            "side": "BUY" if side == "LONG" else "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.4f}",
        }
        signed_q = self._sign_params(params)
        url = f"{self.base_url}{endpoint}?{signed_q}"
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            resp = requests.post(url, headers=headers, timeout=10, verify=False)
            return {"ok": resp.status_code == 200, "status": resp.status_code, "data": resp.json()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class OandaForexDemoConnector:
    """Conector REST v20 para cuentas Demo (fxTrade Practice) de OANDA (Forex)."""

    def __init__(self, api_token: str, account_id: str) -> None:
        self.api_token = api_token
        self.account_id = account_id
        self.base_url = "https://api-fxpractice.oanda.com/v3"

    def place_order(self, symbol: str, side: str, units: float, stop_loss: float, take_profit: float) -> dict:
        instrument = symbol.replace("/", "_")
        signed_units = int(units) if side == "LONG" else -int(units)
        decimals = 3 if "JPY" in symbol else 5
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(signed_units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{stop_loss:.{decimals}f}"},
                "takeProfitOnFill": {"price": f"{take_profit:.{decimals}f}"},
            }
        }
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/accounts/{self.account_id}/orders"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10, verify=False)
            return {"ok": resp.status_code in (200, 201), "status": resp.status_code, "data": resp.json()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class LiveExecutionBroker:
    """
    Broker unificado en vivo:
    - Siempre mantiene la contabilidad exacta en `live_portfolio.json` con precios reales al segundo.
    - Si detecta claves de Binance Testnet (`BINANCE_TESTNET_API_KEY`) o de OANDA Demo (`OANDA_DEMO_API_TOKEN`)
      en `.env`, replica también las órdenes hacia el exchange/broker externo automáticamente.
    """

    def __init__(self, initial_balance_usd: float = 10000.0) -> None:
        load_env_file()
        self.binance_key = os.environ.get("BINANCE_TESTNET_API_KEY", "").strip()
        self.binance_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "").strip()
        self.oanda_token = os.environ.get("OANDA_DEMO_API_TOKEN", "").strip()
        self.oanda_account = os.environ.get("OANDA_DEMO_ACCOUNT_ID", "").strip()

        self.binance_client = (
            BinanceTestnetConnector(self.binance_key, self.binance_secret)
            if self.binance_key and self.binance_secret
            else None
        )
        self.oanda_client = (
            OandaForexDemoConnector(self.oanda_token, self.oanda_account)
            if self.oanda_token and self.oanda_account
            else None
        )

        self.state = self._load_or_init(initial_balance_usd)

    def _load_or_init(self, initial_balance_usd: float) -> PortfolioState:
        if PORTFOLIO_FILE.exists():
            try:
                raw = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
                return PortfolioState(**raw)
            except Exception:
                pass
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st = PortfolioState(
            initial_balance_usd=initial_balance_usd,
            cash_balance_usd=initial_balance_usd,
            equity_usd=initial_balance_usd,
            last_updated=now_str,
        )
        self.save(st)
        return st

    def save(self, state: PortfolioState | None = None) -> None:
        target = state or self.state
        target.last_updated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        PORTFOLIO_FILE.write_text(json.dumps(asdict(target), indent=2, ensure_ascii=False), encoding="utf-8")

    def reset_portfolio(self, initial_balance_usd: float = 10000.0) -> PortfolioState:
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self.state = PortfolioState(
            initial_balance_usd=initial_balance_usd,
            cash_balance_usd=initial_balance_usd,
            equity_usd=initial_balance_usd,
            last_updated=now_str,
        )
        self.save()
        return self.state

    def get_live_price(self, symbol: str) -> float:
        """Obtiene el último precio en tiempo real desde Binance API (Cripto) o Yahoo Finance (Forex)."""
        market = get_market_type(symbol)
        if market == "crypto":
            binance_sym = symbol.replace("/", "")
            try:
                resp = _http_get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": binance_sym},
                    timeout=6,
                )
                if resp.status_code == 200:
                    return float(resp.json()["price"])
            except Exception:
                pass

        df, _ = get_historical_data(symbol, interval="15m", limit=5, use_cache=False)
        return float(df["close"].iloc[-1])

    def update_open_positions(self, atr_by_symbol: dict[str, float] | None = None) -> list[str]:
        """
        Actualiza el precio en vivo de cada posición abierta, verifica si se alcanzó
        Stop Loss o Take Profit, aplica Trailing Stop automático y recalcula el Equity.
        """
        events: list[str] = []
        remaining_positions: list[dict] = []
        unrealized_total = 0.0
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for pos in self.state.open_positions:
            sym = pos["symbol"]
            live_price = self.get_live_price(sym)
            pos["current_price"] = live_price

            side_mult = 1.0 if pos["side"] == "LONG" else -1.0
            entry = float(pos["entry_price"])
            size = float(pos["size"])
            sl = float(pos["stop_loss"])
            tp = float(pos["take_profit"])
            init_risk = float(pos.get("initial_risk_dist", abs(entry - sl)))

            raw_pnl = (live_price - entry) * size * side_mult
            market = pos["market"].lower()
            fee_rate = 0.0005 if market == "crypto" else 0.0001
            fees = (entry * size + live_price * size) * fee_rate
            net_pnl = raw_pnl - fees
            notional = entry * size
            pnl_pct = (net_pnl / notional * 100.0) if notional > 0 else 0.0

            pos["unrealized_pnl_usd"] = round(net_pnl, 2)
            pos["unrealized_pnl_pct"] = round(pnl_pct, 2)

            closed_reason = ""
            if pos["side"] == "LONG":
                if live_price <= sl:
                    closed_reason = "Stop Loss / Trailing Stop Ejecutado"
                elif live_price >= tp:
                    closed_reason = "Take Profit Alcanzado"
                elif init_risk > 0 and (live_price - entry) >= 1.1 * init_risk:
                    atr_val = (atr_by_symbol or {}).get(sym, init_risk / 1.6)
                    new_sl = max(sl, entry + 0.15 * init_risk, live_price - 1.35 * atr_val)
                    if new_sl > sl:
                        pos["stop_loss"] = round(new_sl, 10)
                        events.append(f"🔒 Trailing Stop subido en {sym} LONG a {pos['stop_loss']}")
            else:  # SHORT
                if live_price >= sl:
                    closed_reason = "Stop Loss / Trailing Stop Ejecutado"
                elif live_price <= tp:
                    closed_reason = "Take Profit Alcanzado"
                elif init_risk > 0 and (entry - live_price) >= 1.1 * init_risk:
                    atr_val = (atr_by_symbol or {}).get(sym, init_risk / 1.6)
                    new_sl = min(sl, entry - 0.15 * init_risk, live_price + 1.35 * atr_val)
                    if new_sl < sl:
                        pos["stop_loss"] = round(new_sl, 10)
                        events.append(f"🔒 Trailing Stop bajado en {sym} SHORT a {pos['stop_loss']}")

            if closed_reason:
                self.state.cash_balance_usd = round(self.state.cash_balance_usd + net_pnl, 2)
                self.state.realized_pnl_usd = round(self.state.realized_pnl_usd + net_pnl, 2)
                self.state.total_trades += 1
                if net_pnl > 0:
                    self.state.winning_trades += 1
                else:
                    self.state.losing_trades += 1

                closed_record = ClosedLiveTrade(
                    id=pos["id"],
                    symbol=sym,
                    market=pos["market"],
                    strategy=pos["strategy"],
                    side=pos["side"],
                    entry_time=pos["entry_time"],
                    exit_time=now_str,
                    entry_price=entry,
                    exit_price=live_price,
                    size=size,
                    pnl_usd=round(net_pnl, 2),
                    pnl_pct=round(pnl_pct, 2),
                    exit_reason=closed_reason,
                    broker_mode=pos.get("broker_mode", "Paper Trading En Vivo"),
                )
                self.state.closed_trades.append(asdict(closed_record))
                events.append(
                    f"✅ CERRADA {sym} ({pos['side']}) a {live_price} | PnL: ${net_pnl:+.2f} USD ({closed_reason})"
                )
            else:
                unrealized_total += net_pnl
                remaining_positions.append(pos)

        self.state.open_positions = remaining_positions
        self.state.unrealized_pnl_usd = round(unrealized_total, 2)
        self.state.equity_usd = round(self.state.cash_balance_usd + unrealized_total, 2)
        self.save()
        return events

    def open_order(
        self,
        symbol: str,
        side: str,
        strategy: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_pct: float = 2.0,
        reason: str = "",
    ) -> tuple[bool, str]:
        """Abre una nueva posición en tiempo real si el par no tiene ya una posición abierta."""
        for pos in self.state.open_positions:
            if pos["symbol"] == symbol:
                # Si la señal cambió al lado opuesto, cerramos primero la posición actual
                if pos["side"] != side:
                    self.close_position_by_symbol(symbol, reason=f"Giro de Señal a {side}")
                    break
                return False, f"Ya existe una posición {pos['side']} abierta en {symbol}."

        stop_dist = abs(entry_price - stop_loss)
        if stop_dist <= 0:
            return False, f"Distancia de Stop Loss inválida en {symbol}."

        market = get_market_type(symbol).upper()
        risk_usd = self.state.equity_usd * (risk_pct / 100.0)
        size = risk_usd / stop_dist

        max_lev = 6.0 if market == "CRYPTO" else 20.0
        max_notional = (self.state.equity_usd * max_lev) / max(1, len(self.state.open_positions) + 1)
        if size * entry_price > max_notional:
            size = max_notional / entry_price

        notional_usd = size * entry_price
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        order_id = f"ORD-{int(time.time() * 1000) % 1000000}"

        broker_mode = "Paper Trading En Vivo (Datos Reales)"
        # Enviar a Binance Testnet o OANDA Demo si el usuario configuró sus API Keys en .env
        if market == "CRYPTO" and self.binance_client is not None:
            ext_res = self.binance_client.place_order(symbol, side, size)
            broker_mode = "Binance Testnet API" if ext_res.get("ok") else "Paper Trading (Fallback Testnet)"
        elif market == "FOREX" and self.oanda_client is not None:
            ext_res = self.oanda_client.place_order(symbol, side, size, stop_loss, take_profit)
            broker_mode = "OANDA Forex Demo API" if ext_res.get("ok") else "Paper Trading (Fallback OANDA)"

        new_pos = OpenPosition(
            id=order_id,
            symbol=symbol,
            market=market,
            strategy=strategy,
            side=side,
            entry_time=now_str,
            entry_price=round(entry_price, 10),
            current_price=round(entry_price, 10),
            size=round(size, 5),
            notional_usd=round(notional_usd, 2),
            stop_loss=round(stop_loss, 10),
            take_profit=round(take_profit, 10),
            initial_risk_dist=round(stop_dist, 10),
            unrealized_pnl_usd=0.0,
            unrealized_pnl_pct=0.0,
            reason=reason,
            broker_mode=broker_mode,
        )
        self.state.open_positions.append(asdict(new_pos))
        self.save()
        msg = (
            f"🚀 ORDEN ABIERTA [{broker_mode}] -> {symbol} {side} | "
            f"Entrada: {entry_price:.8f} | SL: {stop_loss:.8f} | TP: {take_profit:.8f} | "
            f"Tamaño: ${notional_usd:,.2f} USD (Riesgo controlado: ${risk_usd:,.2f})"
        )
        return True, msg

    def close_position_by_symbol(self, symbol: str, reason: str = "Cierre Manual / Señal") -> str:
        remaining: list[dict] = []
        msg = f"No se encontró posición abierta en {symbol}."
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for pos in self.state.open_positions:
            if pos["symbol"] == symbol:
                live_price = self.get_live_price(symbol)
                side_mult = 1.0 if pos["side"] == "LONG" else -1.0
                entry = float(pos["entry_price"])
                size = float(pos["size"])
                fee_rate = 0.0005 if pos["market"].lower() == "crypto" else 0.0001
                fees = (entry * size + live_price * size) * fee_rate
                net_pnl = (live_price - entry) * size * side_mult - fees
                notional = entry * size
                pnl_pct = (net_pnl / notional * 100.0) if notional > 0 else 0.0

                self.state.cash_balance_usd = round(self.state.cash_balance_usd + net_pnl, 2)
                self.state.realized_pnl_usd = round(self.state.realized_pnl_usd + net_pnl, 2)
                self.state.total_trades += 1
                if net_pnl > 0:
                    self.state.winning_trades += 1
                else:
                    self.state.losing_trades += 1

                closed_record = ClosedLiveTrade(
                    id=pos["id"],
                    symbol=symbol,
                    market=pos["market"],
                    strategy=pos["strategy"],
                    side=pos["side"],
                    entry_time=pos["entry_time"],
                    exit_time=now_str,
                    entry_price=entry,
                    exit_price=live_price,
                    size=size,
                    pnl_usd=round(net_pnl, 2),
                    pnl_pct=round(pnl_pct, 2),
                    exit_reason=reason,
                    broker_mode=pos.get("broker_mode", "Paper Trading En Vivo"),
                )
                self.state.closed_trades.append(asdict(closed_record))
                msg = f"✅ Cerrada posición {pos['side']} en {symbol} a {live_price:.5f} | PnL: ${net_pnl:+.2f} USD ({reason})"
            else:
                remaining.append(pos)

        self.state.open_positions = remaining
        self.update_open_positions()
        return msg
