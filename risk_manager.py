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
import json
from pathlib import Path

ADAPTIVE_CONFIG_FILE = Path(__file__).with_name("adaptive_config.json")


@dataclass
class RiskConfig:
    base_risk_pct: float = 1.5        # riesgo base por trade (Violenta & Sagaz)
    max_risk_pct: float = 3.0         # techo cuando todo acompaña
    min_risk_pct: float = 0.5         # piso cuando el agente está golpeado
    max_total_open_risk_pct: float = 10.0  # riesgo agregado de todas las abiertas
    max_leverage: float = 5.0         # nocional total / equity
    min_rr: float = 2.0               # reward:risk mínimo
    max_net_per_currency: int = 2     # p.ej. no más de 2 posiciones netas en USD
    max_net_crypto: int = 4
    max_open_positions: int = 8
    min_confidence: float = 70.0
    daily_loss_halt_pct: float = 5.0  # pérdida diaria que frena aperturas
    dd_half_size_pct: float = 8.0     # drawdown desde el pico -> mitad de tamaño
    dd_halt_pct: float = 15.0         # drawdown desde el pico -> no abrir más
    min_sl_pct: float = 0.08          # SL más chico que esto se lo come el spread
    trail_atr_mult: float = 1.5       # multiplicador ATR para trailing desde +2R
    max_cluster_risk_pct: float = 6.0  # riesgo máx. en un mismo bloque correlacionado (p.ej. cripto corta)

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
                kwargs[target_key] = int(v) if isinstance(default_val, int) else float(v)
        return cls(**kwargs)


def currency_exposure(symbol: str, side: str) -> dict[str, int]:
    """LONG EUR/USD = +EUR, -USD. Cripto /USDT se agrupa como CRYPTO."""
    sign = 1 if side.upper().startswith("LONG") else -1
    base, _, quote = symbol.partition("/")
    if quote.upper() == "USDT":
        return {"CRYPTO": sign}
    return {base.upper(): sign, quote.upper(): -sign}


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig.from_adaptive_config()

    # ---------- sizing ----------
    def _risk_pct(self, confidence: float, drawdown_pct: float) -> float:
        c = self.cfg
        # confianza 70 -> base, 95+ -> techo (lineal)
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
    ) -> tuple[bool, float, str]:
        c = self.cfg

        if confidence < c.min_confidence:
            return False, 0.0, f"confianza {confidence:.0f} < {c.min_confidence:.0f}"

        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        if dd >= c.dd_halt_pct:
            return False, 0.0, f"FRENO: drawdown {dd:.1f}% desde el pico"

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
        if sl_pct < c.min_sl_pct:
            return False, 0.0, f"SL {sl_pct:.3f}% demasiado corto (spread se lo come)"
        if sl_dist <= 0 or tp_dist / sl_dist < c.min_rr:
            return False, 0.0, f"R:R {tp_dist / sl_dist if sl_dist else 0:.2f} < {c.min_rr}"

        # exposición neta por moneda / cripto
        net: dict[str, int] = {}
        for p in open_positions:
            for ccy, s in currency_exposure(p["symbol"], p["side"]).items():
                net[ccy] = net.get(ccy, 0) + s
        for ccy, s in currency_exposure(symbol, side).items():
            limit = c.max_net_crypto if ccy == "CRYPTO" else c.max_net_per_currency
            if abs(net.get(ccy, 0) + s) > limit:
                return False, 0.0, f"exposición neta a {ccy} excedería {limit}"

        risk = self._risk_pct(confidence, dd)

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

        # apalancamiento total
        notional_new = equity * risk / 100 / (sl_pct / 100)
        notional_open = sum(p["notional_usd"] for p in open_positions)
        if (notional_open + notional_new) / equity > c.max_leverage:
            room_notional = c.max_leverage * equity - notional_open
            if room_notional <= 0:
                return False, 0.0, f"apalancamiento al tope ({c.max_leverage}x)"
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
    ) -> float:
        """
        +1R  -> SL a breakeven.
        +2R  -> trailing a trail_atr_mult * ATR del precio.
        Nunca empeora el SL. Pasar initial_risk = |entry - SL original|.
        """
        long = side.upper().startswith("LONG")
        moved = (price - entry) if long else (entry - price)
        new_sl = sl
        if moved >= initial_risk:
            be = entry
            new_sl = max(sl, be) if long else min(sl, be)
        if moved >= 2 * initial_risk:
            trail = price - trail_atr_mult * atr if long else price + trail_atr_mult * atr
            new_sl = max(new_sl, trail) if long else min(new_sl, trail)
        return new_sl
