"""
risk_manager.py — Codicioso pero no boludo.

La agresividad vive en: tamaño que crece con la confianza y con la racha,
dejar correr ganadoras (breakeven + trailing) y exigir R:R >= 2.
La inteligencia vive en: topes de riesgo total, de apalancamiento, de
exposición por moneda y frenos automáticos por drawdown.

Uso en live_trader.py (dentro de execute_live_cycle, antes de broker.open_order):

    rm = RiskManager()
    ok, risk, why = rm.decide(
        equity=st.equity_usd, peak_equity=peak, day_start_equity=day0,
        open_positions=st.open_positions, symbol=sym, side="LONG",
        entry=dec.entry_price, sl=dec.stop_loss, tp=dec.take_profit,
        confidence=dec.confidence,
    )
    if ok:
        broker.open_order(..., risk_pct=risk, ...)
    else:
        print(f"  ⛔ {sym}: {why}")
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

ADAPTIVE_CONFIG_FILE = Path(__file__).with_name("adaptive_config.json")


_CRYPTO_BASES = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "SUI", "AVAX", "LINK", "PEPE", "ADA"}


def _is_crypto_symbol(symbol: str) -> bool:
    """Identifica pares cripto vs Forex/Oro sin introducir dependencias externas."""
    upper = symbol.upper().strip()
    if upper.endswith("/USDT") or upper.endswith("-USD"):
        return True
    base = upper.split("/", 1)[0]
    return base in _CRYPTO_BASES


@dataclass
class RiskConfig:
    base_risk_pct: float = 1.5        # riesgo base por trade (Violenta & Sagaz v2)
    max_risk_pct: float = 2.5         # techo cuando todo acompaña
    min_risk_pct: float = 0.5         # piso en recuperación / tras cooldown
    max_total_open_risk_pct: float = 7.5  # riesgo agregado de todas las abiertas
    max_leverage: float = 4.0         # nocional total / equity (Cripto)
    max_leverage_forex: float = 20.0  # nocional total / equity (Forex/Oro)
    min_rr: float = 2.0               # reward:risk mínimo
    max_net_per_currency: int = 2     # p.ej. no más de 2 posiciones netas en USD
    max_net_crypto: int = 2           # máx. 2 cripto netas en la misma dirección
    max_open_positions: int = 6
    min_confidence: float = 70.0
    daily_loss_halt_pct: float = 5.0  # pérdida diaria que frena aperturas
    dd_half_size_pct: float = 8.0     # drawdown realizado desde pico -> mitad de tamaño
    dd_halt_pct: float = 15.0         # drawdown realizado desde pico -> pausa defensiva
    dd_cooldown_hours: float = 24.0   # horas de pausa tras DD >= dd_halt_pct antes de reanudar a riesgo mínimo
    min_sl_pct: float = 0.08          # SL mínimo en Cripto
    min_sl_pct_forex: float = 0.025   # SL mínimo en Forex/Oro (~2.5 pips en 15m)
    trail_atr_mult: float = 1.5       # multiplicador ATR para trailing desde +1.5R
    be_trigger_r: float = 1.2         # mover a BE+buffer al alcanzar +1.2R
    be_buffer_pct: float = 0.15       # colchón Cripto sobre entry (cubre 0.10% fee + slippage)
    be_buffer_pct_forex: float = 0.03 # colchón Forex/Oro sobre entry (cubre 0.02% fee + spread)
    trail_trigger_r: float = 1.5      # activar trailing stop desde +1.5R
    max_cluster_risk_pct: float = 3.5  # riesgo máx. en un mismo bloque correlacionado (p.ej. cripto corta)
    # Costos ida y vuelta que cobra el broker paper (comisión x2 + slippage del stop), en % del precio.
    round_trip_cost_pct: float = 0.11        # Cripto: 0.05% + 0.05% + 0.01% slippage
    round_trip_cost_pct_forex: float = 0.022  # Forex/Oro: 0.01% + 0.01% + 0.002% slippage
    max_cost_r: float = 0.35          # rechazar si los costos se comen más de 0.35R del riesgo
    max_tick_frac_of_stop: float = 0.10  # rechazar si un solo tick de precio es >= 10% del stop

    def effective_round_trip_cost_pct(self, symbol: str) -> float:
        """Costo ida y vuelta estimado (%) según mercado (Cripto vs Forex/Oro)."""
        return self.round_trip_cost_pct if _is_crypto_symbol(symbol) else self.round_trip_cost_pct_forex

    def effective_min_sl_pct(self, symbol: str) -> float:
        """Stop loss mínimo (%) según mercado (Cripto vs Forex/Oro)."""
        return self.min_sl_pct if _is_crypto_symbol(symbol) else self.min_sl_pct_forex

    def effective_be_buffer_pct(self, market_or_symbol: str) -> float:
        """Colchón de Breakeven (%) según mercado (`FOREX`/`CRYPTo` o símbolo)."""
        val = market_or_symbol.strip()
        if val.lower() == "forex":
            return self.be_buffer_pct_forex
        if "/" in val and not _is_crypto_symbol(val):
            return self.be_buffer_pct_forex
        return self.be_buffer_pct

    def effective_max_leverage(self, symbol: str) -> float:
        """Apalancamiento máximo según mercado (Cripto vs Forex/Oro)."""
        return self.max_leverage if _is_crypto_symbol(symbol) else self.max_leverage_forex

    def room_notional(self, equity: float, open_positions: list[dict], symbol: str) -> float:
        """Presupuesto nocional disponible normalizado por apalancamiento de cada mercado."""
        if equity <= 0:
            return 0.0
        used_frac = 0.0
        for p in open_positions:
            p_sym = str(p.get("symbol", ""))
            p_lev = max(0.1, self.effective_max_leverage(p_sym))
            used_frac += float(p.get("notional_usd", 0.0)) / (equity * p_lev)
        sym_lev = max(0.1, self.effective_max_leverage(symbol))
        per_pos_share = 0.45 if _is_crypto_symbol(symbol) else 0.50
        return min(max(0.0, (1.0 - used_frac) * equity * sym_lev), equity * sym_lev * per_pos_share)

    @classmethod
    def from_adaptive_config(cls) -> "RiskConfig":
        """Carga parámetros dinámicos desde adaptive_config.json['risk_config'] si existe."""
        defaults = cls()
        if not ADAPTIVE_CONFIG_FILE.exists():
            return defaults
        try:
            raw = json.loads(ADAPTIVE_CONFIG_FILE.read_text(encoding="utf-8")).get("risk_config", {})
            if not isinstance(raw, dict):
                return defaults
        except Exception:
            return defaults

        aliases = {
            "max_per_currency": "max_net_per_currency",
            "max_crypto_net": "max_net_crypto",
            "daily_loss_limit_pct": "daily_loss_halt_pct",
            "dd_reduce_pct": "dd_half_size_pct",
            "dd_stop_pct": "dd_halt_pct",
        }
        valid_names = {f.name for f in fields(cls)}
        kwargs: dict = {}
        for k, v in raw.items():
            target_key = aliases.get(k, k)
            if target_key in valid_names and v is not None:
                default_val = getattr(defaults, target_key)
                if isinstance(default_val, bool):
                    kwargs[target_key] = bool(v)
                elif isinstance(default_val, int):
                    kwargs[target_key] = int(v)
                else:
                    kwargs[target_key] = float(v)
        return cls(**kwargs)


def estimate_price_tick(prices) -> float | None:
    """
    Estima el tick mínimo de precio a partir de cotizaciones reales (menor salto positivo entre
    precios distintos). Si los datos traen ruido de punto flotante el tick sale más chico, nunca
    más grande, así que el filtro de granularidad solo puede fallar hacia el lado permisivo.
    """
    try:
        arr = np.asarray(prices, dtype=float).ravel()
    except (TypeError, ValueError):
        return None
    arr = np.unique(arr[np.isfinite(arr) & (arr > 0)])
    if arr.size < 3:
        return None
    diffs = np.diff(arr)
    diffs = diffs[diffs > 0]
    return float(diffs.min()) if diffs.size else None


def currency_exposure(symbol: str, side: str) -> dict[str, int]:
    """LONG EUR/USD = +EUR, -USD. Cripto /USDT se agrupa como CRYPTO."""
    sign = 1 if side.upper().startswith("LONG") else -1
    base, _, quote = symbol.partition("/")
    if quote.upper() == "USDT":
        return {"CRYPTO": sign}
    return {base.upper(): sign, quote.upper(): -sign}


def _parse_utc(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig.from_adaptive_config()

    # ---------- sizing ----------
    def _risk_pct(self, confidence: float, drawdown_pct: float, dd_recovery_mode: bool = False) -> float:
        c = self.cfg
        if dd_recovery_mode or drawdown_pct >= c.dd_halt_pct:
            return c.min_risk_pct
        # confianza min_confidence -> base, +25 pts -> techo (lineal)
        t = max(0.0, min(1.0, (confidence - c.min_confidence) / 25.0))
        risk = c.base_risk_pct + t * (c.max_risk_pct - c.base_risk_pct)
        if drawdown_pct >= c.dd_half_size_pct:
            risk *= 0.5
        return max(c.min_risk_pct, min(c.max_risk_pct, risk))

    # ---------- gate de entrada ----------
    def decide(
        self,
        *,
        equity: float,
        peak_equity: float,
        day_start_equity: float,
        open_positions: list[dict],
        symbol: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        confidence: float,
        realized_peak_equity: float | None = None,
        dd_halt_until_utc: str = "",
        dd_recovery_mode: bool = False,
        now_utc: datetime | None = None,
        price_tick: float | None = None,
    ) -> tuple[bool, float, str]:
        c = self.cfg

        if confidence < c.min_confidence:
            return False, 0.0, f"confianza {confidence:.0f} < {c.min_confidence:.0f}"

        effective_peak = (
            realized_peak_equity
            if (realized_peak_equity is not None and realized_peak_equity > 0)
            else peak_equity
        )
        dd = (effective_peak - equity) / effective_peak * 100 if effective_peak > 0 else 0.0
        if dd >= c.dd_halt_pct + 5.0:
            return False, 0.0, f"FRENO EMERGENCIA: drawdown {dd:.1f}% desde pico realizado"

        now = now_utc or datetime.now(tz=timezone.utc)
        halt_until = _parse_utc(dd_halt_until_utc)
        if halt_until is not None and now < halt_until:
            return False, 0.0, f"FRENO: pausa defensiva por drawdown {dd:.1f}% (hasta {dd_halt_until_utc})"

        if dd >= c.dd_halt_pct and not dd_recovery_mode:
            return False, 0.0, f"FRENO_COOLDOWN: drawdown {dd:.1f}% desde pico realizado -> pausa {c.dd_cooldown_hours:.0f}h"

        day_pl = (equity - day_start_equity) / day_start_equity * 100 if day_start_equity > 0 else 0.0
        if day_pl <= -c.daily_loss_halt_pct:
            return False, 0.0, f"FRENO: pérdida diaria {day_pl:.1f}%"

        if any(p["symbol"] == symbol for p in open_positions):
            return False, 0.0, "ya hay posición abierta en este símbolo"
        if len(open_positions) >= c.max_open_positions:
            return False, 0.0, f"máx {c.max_open_positions} posiciones abiertas"

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        sl_pct = sl_dist / entry * 100
        min_sl = c.effective_min_sl_pct(symbol)
        if sl_pct < min_sl:
            return False, 0.0, f"SL {sl_pct:.3f}% demasiado corto (< {min_sl:.3f}%, spread se lo come)"
        if sl_dist <= 0 or tp_dist / sl_dist < c.min_rr:
            return False, 0.0, f"R:R {tp_dist / sl_dist if sl_dist else 0:.2f} < {c.min_rr}"

        # Costos en múltiplos de R: con stops cortos la comisión sola puede comerse media operación.
        cost_r = (c.effective_round_trip_cost_pct(symbol) / 100.0) * entry / sl_dist
        if cost_r > c.max_cost_r:
            return False, 0.0, f"costos {cost_r:.2f}R > {c.max_cost_r:.2f}R del riesgo (stop demasiado corto para las comisiones)"

        # Granularidad: si un tick es una fracción grande del stop, las señales y el stop son ruido de redondeo.
        if price_tick is not None and price_tick > 0:
            tick_frac = price_tick / sl_dist
            if tick_frac >= c.max_tick_frac_of_stop:
                return False, 0.0, (
                    f"precio demasiado granular: 1 tick = {tick_frac * 100:.0f}% del stop "
                    f"(máx {c.max_tick_frac_of_stop * 100:.0f}%)"
                )

        # exposición neta por moneda / cripto
        net: dict[str, int] = {}
        for p in open_positions:
            for ccy, s in currency_exposure(p["symbol"], p["side"]).items():
                net[ccy] = net.get(ccy, 0) + s
        for ccy, s in currency_exposure(symbol, side).items():
            limit = c.max_net_crypto if ccy == "CRYPTO" else c.max_net_per_currency
            if abs(net.get(ccy, 0) + s) > limit:
                return False, 0.0, f"exposición neta a {ccy} excedería {limit}"

        risk = self._risk_pct(confidence, dd, dd_recovery_mode=dd_recovery_mode)

        # riesgo total abierto (solo cuenta lo que realmente se puede perder:
        # una posición con stop en breakeven o trailing en ganancia no consume riesgo)
        open_risk_usd = sum(self.open_risk_usd(p) for p in open_positions)
        new_risk_usd = equity * risk / 100
        if (open_risk_usd + new_risk_usd) / equity * 100 > c.max_total_open_risk_pct:
            room = c.max_total_open_risk_pct - open_risk_usd / equity * 100
            if room < c.min_risk_pct:
                return False, 0.0, f"riesgo total abierto al tope ({c.max_total_open_risk_pct}%)"
            risk = room

        # riesgo correlacionado: 4 cortos en cripto son UNA sola apuesta contra el BTC-beta
        bucket_risk: dict[tuple[str, int], float] = {}
        for p in open_positions:
            r_usd = self.open_risk_usd(p)
            for ccy, s in currency_exposure(p["symbol"], p["side"]).items():
                bucket_risk[(ccy, s)] = bucket_risk.get((ccy, s), 0.0) + r_usd
        for ccy, s in currency_exposure(symbol, side).items():
            used_pct = bucket_risk.get((ccy, s), 0.0) / equity * 100
            room = c.max_cluster_risk_pct - used_pct
            if room < c.min_risk_pct:
                direction = "largo" if s > 0 else "corto"
                return False, 0.0, f"bloque correlacionado {ccy} {direction} al tope ({used_pct:.1f}% de {c.max_cluster_risk_pct}%)"
            risk = min(risk, room)

        # apalancamiento total diferenciado por mercado (Cripto vs Forex/Oro)
        sym_lev = c.effective_max_leverage(symbol)
        room_notional = c.room_notional(equity, open_positions, symbol)
        if room_notional <= 0:
            return False, 0.0, f"apalancamiento al tope ({sym_lev:.0f}x)"
        notional_new = equity * risk / 100 / (sl_pct / 100)
        if notional_new > room_notional:
            risk = min(risk, room_notional * (sl_pct / 100) / equity * 100)
            if risk < c.min_risk_pct:
                return False, 0.0, "poco margen de apalancamiento restante"

        return True, round(risk, 3), f"OK riesgo {risk:.2f}% (dd {dd:.1f}%)"

    # ---------- gestión de salida: dejar correr ganadoras ----------
    @staticmethod
    def open_risk_usd(p: dict) -> float:
        """USD que se pierden si salta el stop actual (0 si el stop ya está en breakeven o ganancia)."""
        entry = float(p["entry_price"])
        sl = float(p["stop_loss"])
        if entry <= 0:
            return 0.0
        dist = (entry - sl) if str(p["side"]).upper().startswith("LONG") else (sl - entry)
        return float(p["notional_usd"]) * max(0.0, dist) / entry

    @staticmethod
    def manage_stop(
        side: str, entry: float, price: float, sl: float, atr: float,
        initial_risk: float, trail_atr_mult: float = 1.5,
        be_trigger_r: float = 1.2, be_buffer_pct: float = 0.15,
        trail_trigger_r: float = 1.5,
    ) -> float:
        """
        +1.2R -> SL a breakeven + colchón de comisiones (0.15% por defecto).
        +1.5R -> trailing a trail_atr_mult * ATR del precio.
        Nunca empeora el SL. Pasar initial_risk = |entry - SL original|.
        """
        long = side.upper().startswith("LONG")
        moved = (price - entry) if long else (entry - price)
        new_sl = sl
        if moved >= be_trigger_r * initial_risk:
            target_be = entry * (1.0 + be_buffer_pct / 100.0) if long else entry * (1.0 - be_buffer_pct / 100.0)
            if long:
                be = target_be if target_be < price else entry
            else:
                be = target_be if target_be > price else entry
            new_sl = max(new_sl, be) if long else min(new_sl, be)
        if moved >= trail_trigger_r * initial_risk and atr > 0:
            trail = price - trail_atr_mult * atr if long else price + trail_atr_mult * atr
            new_sl = max(new_sl, trail) if long else min(new_sl, trail)
        return new_sl
