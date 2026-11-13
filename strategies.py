"""
Estrategias de Trading Automatizadas basadas en Análisis Técnico:
1. Agente de Confluencia Multi-Indicador (EMA + MACD + RSI + Bandas de Bollinger)
2. Tendencia EMA + MACD + Filtro RSI
3. Reversión a la Media con Bandas de Bollinger + RSI (Especial Forex / Rangos)
4. Ruptura de Compresión de Bollinger + Impulso MACD (Especial Cripto)
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class SignalDecision:
    action: str  # "LONG", "SHORT", "HOLD"
    confidence: float  # 0.0 a 100.0
    reasons: list[str]
    stop_loss: float
    take_profit: float
    entry_price: float


STRATEGY_CATALOG: dict[str, str] = {
    "confluence": "Agente Confluencia IA/Técnica (EMA + MACD + RSI + Bollinger)",
    "trend_macd": "Seguidor de Tendencia (Cruce EMA + Impulso MACD + RSI)",
    "bollinger_reversion": "Reversión a la Media (Bandas de Bollinger + RSI Extremo)",
    "volatility_breakout": "Ruptura de Volatilidad (Squeeze de Bollinger + MACD)",
    "ema_pullback": "Retroceso Institucional a la Media (Pullback EMA + RSI + MACD)",
    "momentum_shark": "Modo Tiburón Agresivo (Explosión de Momento + Aceleración MACD)",
    "chimichurri_alpha": "Chimichurri Quant Alpha (ADX + Donchian + Volumen + Confluencia)",
}


def generate_signals(
    df: pd.DataFrame,
    strategy_name: str = "confluence",
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
) -> pd.DataFrame:
    """
    Genera la columna `signal` (1 = LONG/Compra, -1 = SHORT/Venta, 0 = Neutro)
    y `signal_reason` explicando por qué el agente tomó la decisión en cada vela.
    """
    data = df.copy()
    n = len(data)
    signals = np.zeros(n, dtype=int)
    scores = np.zeros(n, dtype=float)
    reasons = [""] * n

    close = data["close"].to_numpy()
    open_p = data["open"].to_numpy()
    ema_fast = data["ema_fast"].to_numpy()
    ema_slow = data["ema_slow"].to_numpy()
    ema_trend = data["ema_trend"].to_numpy()
    rsi = data["rsi"].to_numpy()
    macd = data["macd"].to_numpy()
    macd_sig = data["macd_signal"].to_numpy()
    macd_hist = data["macd_hist"].to_numpy()
    bb_pct = data["bb_pct_b"].to_numpy()
    bb_width = data["bb_width"].to_numpy()
    bb_upper = data["bb_upper"].to_numpy()
    bb_lower = data["bb_lower"].to_numpy()
    adx = data["adx"].to_numpy() if "adx" in data.columns else np.full(n, 22.0)
    plus_di = data["plus_di"].to_numpy() if "plus_di" in data.columns else np.full(n, 20.0)
    minus_di = data["minus_di"].to_numpy() if "minus_di" in data.columns else np.full(n, 20.0)
    vol_ratio = data["vol_ratio"].to_numpy() if "vol_ratio" in data.columns else np.ones(n)
    donchian_high = data["donchian_high"].to_numpy() if "donchian_high" in data.columns else close
    donchian_low = data["donchian_low"].to_numpy() if "donchian_low" in data.columns else close

    for i in range(30, n):
        if strategy_name == "confluence":
            score = 0.0
            r_list: list[str] = []

            # 1. Tendencia Estructural (Medias Móviles)
            if close[i] > ema_trend[i] and ema_fast[i] > ema_slow[i]:
                score += 35.0
                r_list.append("Tendencia Alcista (EMA rápida > lenta > EMA100)")
            elif close[i] < ema_trend[i] and ema_fast[i] < ema_slow[i]:
                score -= 35.0
                r_list.append("Tendencia Bajista (EMA rápida < lenta < EMA100)")

            # 2. Impulso MACD
            if macd[i] > macd_sig[i] and macd_hist[i] > macd_hist[i - 1]:
                score += 30.0
                r_list.append("Impulso MACD Alcista creciente")
            elif macd[i] < macd_sig[i] and macd_hist[i] < macd_hist[i - 1]:
                score -= 30.0
                r_list.append("Impulso MACD Bajista creciente")

            # 3. RSI + Bandas de Bollinger (Rebote o Continuación sana)
            if rsi[i - 1] < rsi_oversold and rsi[i] > rsi[i - 1] and bb_pct[i - 1] < 0.25:
                score += 45.0
                r_list.append(f"Rebote de Sobreventa (RSI={rsi[i]:.1f}, Bollinger %B={bb_pct[i]:.2f})")
            elif rsi[i - 1] > rsi_overbought and rsi[i] < rsi[i - 1] and bb_pct[i - 1] > 0.75:
                score -= 45.0
                r_list.append(f"Rechazo en Sobrecompra (RSI={rsi[i]:.1f}, Bollinger %B={bb_pct[i]:.2f})")
            elif 48.0 <= rsi[i] <= 66.0 and close[i] > open_p[i] and score > 0:
                score += 20.0
                r_list.append(f"Fuerza RSI saludable ({rsi[i]:.1f})")
            elif 34.0 <= rsi[i] <= 52.0 and close[i] < open_p[i] and score < 0:
                score -= 20.0
                r_list.append(f"Debilidad RSI confirmada ({rsi[i]:.1f})")

            scores[i] = score
            if score >= 65.0:
                signals[i] = 1
                reasons[i] = " + ".join(r_list)
            elif score <= -65.0:
                signals[i] = -1
                reasons[i] = " + ".join(r_list)

        elif strategy_name == "trend_macd":
            bull_cross = (macd[i] > macd_sig[i]) and (macd[i - 1] <= macd_sig[i - 1] or ema_fast[i - 1] <= ema_slow[i - 1])
            bear_cross = (macd[i] < macd_sig[i]) and (macd[i - 1] >= macd_sig[i - 1] or ema_fast[i - 1] >= ema_slow[i - 1])

            if bull_cross and ema_fast[i] > ema_slow[i] and close[i] > ema_trend[i] and 42.0 < rsi[i] < 70.0:
                signals[i] = 1
                scores[i] = 80.0
                reasons[i] = f"Cruce Alcista MACD/EMA a favor de EMA100 con RSI={rsi[i]:.1f}"
            elif bear_cross and ema_fast[i] < ema_slow[i] and close[i] < ema_trend[i] and 30.0 < rsi[i] < 58.0:
                signals[i] = -1
                scores[i] = -80.0
                reasons[i] = f"Cruce Bajista MACD/EMA a favor de EMA100 con RSI={rsi[i]:.1f}"

        elif strategy_name == "bollinger_reversion":
            # Ideal para mercados laterales y Forex: rebote en banda extrema + giro de vela
            touch_lower = (close[i - 1] <= bb_lower[i - 1] or bb_pct[i - 1] <= 0.12) and close[i] > open_p[i]
            touch_upper = (close[i - 1] >= bb_upper[i - 1] or bb_pct[i - 1] >= 0.88) and close[i] < open_p[i]

            if touch_lower and (rsi[i - 1] < rsi_oversold + 5 or rsi[i] < 42.0):
                signals[i] = 1
                scores[i] = 75.0
                reasons[i] = f"Rebote en Banda Inferior Bollinger (%B={bb_pct[i - 1]:.2f}) + Giro RSI ({rsi[i]:.1f})"
            elif touch_upper and (rsi[i - 1] > rsi_overbought - 5 or rsi[i] > 58.0):
                signals[i] = -1
                scores[i] = -75.0
                reasons[i] = f"Rechazo en Banda Superior Bollinger (%B={bb_pct[i - 1]:.2f}) + Giro RSI ({rsi[i]:.1f})"

        elif strategy_name == "volatility_breakout":
            # Ruptura de banda con expansión de histograma MACD
            avg_width = np.mean(bb_width[max(20, i - 25) : i])
            expanding = bb_width[i] > avg_width * 1.02
            if expanding and close[i] > bb_upper[i - 1] and macd_hist[i] > 0 and macd_hist[i] > macd_hist[i - 1] and rsi[i] < 76.0:
                signals[i] = 1
                scores[i] = 85.0
                reasons[i] = f"Ruptura Alcista de Bollinger con expansión MACD (RSI={rsi[i]:.1f})"
            elif expanding and close[i] < bb_lower[i - 1] and macd_hist[i] < 0 and macd_hist[i] < macd_hist[i - 1] and rsi[i] > 24.0:
                signals[i] = -1
                scores[i] = -85.0
                reasons[i] = f"Ruptura Bajista de Bollinger con expansión MACD (RSI={rsi[i]:.1f})"

        elif strategy_name == "ema_pullback":
            # Compra en retroceso a la EMA rápida dentro de tendencia alcista estructural (o venta en rebote a EMA en tendencia bajista)
            uptrend = ema_fast[i] > ema_slow[i] and ema_slow[i] > ema_trend[i]
            downtrend = ema_fast[i] < ema_slow[i] and ema_slow[i] < ema_trend[i]
            pullback_buy = (close[i - 1] <= ema_fast[i - 1]) and (close[i] > ema_fast[i]) and (close[i] > open_p[i])
            pullback_sell = (close[i - 1] >= ema_fast[i - 1]) and (close[i] < ema_fast[i]) and (close[i] < open_p[i])

            if uptrend and pullback_buy and 40.0 <= rsi[i] <= 65.0 and macd_hist[i] >= macd_hist[i - 1]:
                signals[i] = 1
                scores[i] = 82.0
                reasons[i] = f"Pullback Alcista sobre EMA rápida en tendencia estructural (RSI={rsi[i]:.1f})"
            elif downtrend and pullback_sell and 35.0 <= rsi[i] <= 60.0 and macd_hist[i] <= macd_hist[i - 1]:
                signals[i] = -1
                scores[i] = -82.0
                reasons[i] = f"Pullback Bajista bajo EMA rápida en tendencia estructural (RSI={rsi[i]:.1f})"

        elif strategy_name == "momentum_shark":
            # Estrategia agresiva para capturar impulsos fuertes y rupturas de momento
            bull_surge = (
                close[i] > ema_fast[i]
                and ema_fast[i] >= ema_slow[i] * 0.999
                and macd_hist[i] > macd_hist[i - 1] > macd_hist[i - 2]
                and 52.0 <= rsi[i] <= 78.0
                and close[i] > open_p[i]
            )
            bear_surge = (
                close[i] < ema_fast[i]
                and ema_fast[i] <= ema_slow[i] * 1.001
                and macd_hist[i] < macd_hist[i - 1] < macd_hist[i - 2]
                and 22.0 <= rsi[i] <= 48.0
                and close[i] < open_p[i]
            )
            if bull_surge:
                signals[i] = 1
                scores[i] = 90.0
                reasons[i] = f"Explosión Alcista Shark (3 velas de aceleración MACD + RSI={rsi[i]:.1f})"
            elif bear_surge:
                signals[i] = -1
                scores[i] = -90.0
                reasons[i] = f"Explosión Bajista Shark (3 velas de aceleración MACD + RSI={rsi[i]:.1f})"

        elif strategy_name == "chimichurri_alpha":
            # Régimen de Tendencia (ADX >= 22) + Ruptura Donchian o Pullback con Volumen
            strong_trend = adx[i] >= 22.0
            vol_ok = vol_ratio[i] >= 1.10 or vol_ratio[i] == 1.0  # Forex puede tener vol=0 -> 1.0

            donchian_bull = close[i] > donchian_high[i] and plus_di[i] > minus_di[i] and macd_hist[i] > 0
            donchian_bear = close[i] < donchian_low[i] and minus_di[i] > plus_di[i] and macd_hist[i] < 0

            pullback_bull = (
                strong_trend
                and ema_fast[i] > ema_slow[i] > ema_trend[i]
                and close[i - 1] <= ema_fast[i - 1]
                and close[i] > ema_fast[i]
                and macd_hist[i] > macd_hist[i - 1]
                and 42.0 <= rsi[i] <= 68.0
            )
            pullback_bear = (
                strong_trend
                and ema_fast[i] < ema_slow[i] < ema_trend[i]
                and close[i - 1] >= ema_fast[i - 1]
                and close[i] < ema_fast[i]
                and macd_hist[i] < macd_hist[i - 1]
                and 32.0 <= rsi[i] <= 58.0
            )

            if (donchian_bull and strong_trend and vol_ok and rsi[i] < 76.0) or pullback_bull:
                signals[i] = 1
                scores[i] = min(98.0, 78.0 + (adx[i] - 20.0) * 0.6 + (10.0 if vol_ratio[i] >= 1.3 else 0.0))
                reasons[i] = (
                    f"Chimichurri Alpha LONG (ADX={adx[i]:.1f}, Vol={vol_ratio[i]:.2f}x, "
                    f"RSI={rsi[i]:.1f}, {'Ruptura Donchian' if donchian_bull else 'Pullback Tendencia'})"
                )
            elif (donchian_bear and strong_trend and vol_ok and rsi[i] > 24.0) or pullback_bear:
                signals[i] = -1
                scores[i] = -min(98.0, 78.0 + (adx[i] - 20.0) * 0.6 + (10.0 if vol_ratio[i] >= 1.3 else 0.0))
                reasons[i] = (
                    f"Chimichurri Alpha SHORT (ADX={adx[i]:.1f}, Vol={vol_ratio[i]:.2f}x, "
                    f"RSI={rsi[i]:.1f}, {'Ruptura Donchian' if donchian_bear else 'Pullback Tendencia'})"
                )

    data["signal"] = signals
    data["signal_score"] = scores
    data["signal_reason"] = reasons
    return data


def evaluate_latest_candle(
    df_with_signals: pd.DataFrame,
    sl_atr_mult: float = 1.6,
    tp_atr_mult: float = 3.2,
    lookback_bars: int = 1,
    macro_bias: int = 0,
) -> SignalDecision:
    """
    Evalúa la última vela cerrada para emitir una orden operativa en tiempo real.
    Usa únicamente la señal emitida por la estrategia validada (sin entradas improvisadas).
    """
    last = df_with_signals.iloc[-1]
    price = float(last["close"])
    atr = float(last["atr"])
    adx_val = float(last["adx"]) if "adx" in last.index else 22.0
    vol_ratio = float(last["vol_ratio"]) if "vol_ratio" in last.index else 1.0
    rsi = float(last["rsi"])

    # Buscar la señal más reciente dentro de la ventana de vigencia (por defecto solo última vela cerrada)
    chosen_row = last
    for offset in range(1, min(lookback_bars + 1, len(df_with_signals))):
        candidate = df_with_signals.iloc[-offset]
        if int(candidate["signal"]) != 0:
            chosen_row = candidate
            break

    sig_val = int(chosen_row["signal"])
    reason_text = str(chosen_row["signal_reason"])
    conf_score = abs(float(chosen_row["signal_score"]))

    # Filtro Multi-Timeframe: si va contra la tendencia 1h y NO es un extremo de RSI, bloquear entrada débil
    if sig_val == 1 and macro_bias == -1 and rsi > 30.0:
        if conf_score < 85.0:
            sig_val = 0
            reason_text = f"Señal LONG filtrada por tendencia Macro 1h Bajista (RSI={rsi:.1f}, ADX={adx_val:.1f})."
    elif sig_val == -1 and macro_bias == 1 and rsi < 70.0:
        if conf_score < 85.0:
            sig_val = 0
            reason_text = f"Señal SHORT filtrada por tendencia Macro 1h Alcista (RSI={rsi:.1f}, ADX={adx_val:.1f})."

    # Bonificación de convicción si hay alineación macro + ADX fuerte + volumen
    if sig_val != 0:
        if (sig_val == 1 and macro_bias == 1) or (sig_val == -1 and macro_bias == -1):
            conf_score += 7.0
        if adx_val >= 26.0:
            conf_score += 5.0
        if vol_ratio >= 1.35:
            conf_score += 5.0

    if sig_val == 1:
        sl = price - sl_atr_mult * atr
        tp = price + tp_atr_mult * atr
        return SignalDecision(
            action="LONG (COMPRAR)",
            confidence=min(98.0, max(65.0, conf_score)),
            reasons=[reason_text],
            stop_loss=sl,
            take_profit=tp,
            entry_price=price,
        )
    elif sig_val == -1:
        sl = price + sl_atr_mult * atr
        tp = price - tp_atr_mult * atr
        return SignalDecision(
            action="SHORT (VENDER)",
            confidence=min(98.0, max(65.0, conf_score)),
            reasons=[reason_text],
            stop_loss=sl,
            take_profit=tp,
            entry_price=price,
        )
    else:
        diag = (
            f"Sin confluencia suficiente aún (RSI={last['rsi']:.1f}, "
            f"MACD Hist={last['macd_hist']:.5f}, Bollinger %B={last['bb_pct_b']:.2f}). "
            "El agente protege tu capital esperando confirmación clara."
        )
        return SignalDecision(
            action="HOLD (ESPERAR)",
            confidence=50.0,
            reasons=[diag],
            stop_loss=price - sl_atr_mult * atr,
            take_profit=price + tp_atr_mult * atr,
            entry_price=price,
        )
