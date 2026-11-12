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

    data["signal"] = signals
    data["signal_score"] = scores
    data["signal_reason"] = reasons
    return data


def evaluate_latest_candle(
    df_with_signals: pd.DataFrame,
    sl_atr_mult: float = 1.6,
    tp_atr_mult: float = 3.2,
    lookback_bars: int = 4,
) -> SignalDecision:
    """
    Evalúa las últimas velas cerradas para emitir una orden operativa en tiempo real.
    Verifica primero la señal de la estrategia optimizada en las últimas `lookback_bars` velas,
    y también evalúa si hay confluencia técnica inmediata en la vela actual.
    """
    last = df_with_signals.iloc[-1]
    price = float(last["close"])
    atr = float(last["atr"])

    # Buscar la señal más reciente dentro de la ventana de vigencia (últimas lookback_bars velas)
    chosen_row = last
    for offset in range(1, min(lookback_bars + 1, len(df_with_signals))):
        candidate = df_with_signals.iloc[-offset]
        if int(candidate["signal"]) != 0:
            chosen_row = candidate
            break

    sig_val = int(chosen_row["signal"])
    reason_text = str(chosen_row["signal_reason"])
    conf_score = abs(float(chosen_row["signal_score"]))

    # Si la estrategia principal no disparó en las últimas velas, verificar confluencia direccional fuerte en vivo
    if sig_val == 0:
        ema_fast = float(last["ema_fast"])
        ema_slow = float(last["ema_slow"])
        ema_trend = float(last["ema_trend"])
        rsi = float(last["rsi"])
        macd = float(last["macd"])
        macd_sig = float(last["macd_signal"])
        macd_hist = float(last["macd_hist"])
        prev_hist = float(df_with_signals.iloc[-2]["macd_hist"])
        bb_pct = float(last["bb_pct_b"])

        if price > ema_trend and ema_fast > ema_slow and macd > macd_sig and macd_hist >= prev_hist and 45.0 <= rsi <= 68.0:
            sig_val = 1
            conf_score = 72.0
            reason_text = f"Confluencia Alcista en Vivo (Precio > EMA100, EMA rápida > lenta, MACD+ y RSI={rsi:.1f})"
        elif price < ema_trend and ema_fast < ema_slow and macd < macd_sig and macd_hist <= prev_hist and 32.0 <= rsi <= 55.0:
            sig_val = -1
            conf_score = 72.0
            reason_text = f"Confluencia Bajista en Vivo (Precio < EMA100, EMA rápida < lenta, MACD- y RSI={rsi:.1f})"
        elif rsi < 33.0 and bb_pct < 0.22 and macd_hist > prev_hist:
            sig_val = 1
            conf_score = 70.0
            reason_text = f"Rebote de Sobreventa en Vivo (RSI={rsi:.1f}, Bollinger %B={bb_pct:.2f}, giro MACD)"
        elif rsi > 67.0 and bb_pct > 0.78 and macd_hist < prev_hist:
            sig_val = -1
            conf_score = 70.0
            reason_text = f"Rechazo de Sobrecompra en Vivo (RSI={rsi:.1f}, Bollinger %B={bb_pct:.2f}, giro MACD)"
        elif ema_fast > ema_slow and macd_hist > prev_hist and 48.0 <= rsi <= 72.0:
            sig_val = 1
            conf_score = 68.0
            reason_text = f"Impulso Tiburón Alcista (EMA rápida > lenta + Aceleración MACD + RSI={rsi:.1f})"
        elif ema_fast < ema_slow and macd_hist < prev_hist and 28.0 <= rsi <= 52.0:
            sig_val = -1
            conf_score = 68.0
            reason_text = f"Impulso Tiburón Bajista (EMA rápida < lenta + Aceleración MACD + RSI={rsi:.1f})"

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
