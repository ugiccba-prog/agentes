"""
Motor de Backtesting Profesional con Gestión de Riesgo Dinámica y Auto-Optimizador.
Incluye:
- Position Sizing por riesgo fijo por operación (ej. arriesgar 1.5% o 2% de la cuenta por trade)
- Stop Loss y Take Profit basados en volatilidad real (ATR)
- Break-Even y Trailing Stop automático para asegurar ganancias cuando el precio avanza a favor
- Comisiones y spreads realistas (diferenciados entre Cripto y Forex)
- Optimizador automático de estrategias y parámetros por activo
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from indicators import compute_indicators
from market_data import get_market_type
from strategies import STRATEGY_CATALOG, generate_signals


@dataclass
class TradeRecord:
    symbol: str
    strategy: str
    side: str  # "LONG" o "SHORT"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    pnl_usd: float
    pnl_pct_account: float
    exit_reason: str
    signal_reason: str


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    strategy_label: str
    initial_capital: float
    final_capital: float
    net_profit_usd: float
    total_return_pct: float
    buy_and_hold_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_win_usd: float
    avg_loss_usd: float
    expectancy_usd: float
    params: dict[str, float | int | str] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)


def run_backtest(
    df_raw: pd.DataFrame,
    symbol: str,
    strategy_name: str = "confluence",
    initial_capital: float = 1000.0,
    risk_per_trade_pct: float = 2.0,
    sl_atr_mult: float = 1.6,
    tp_atr_mult: float = 3.2,
    use_trailing_stop: bool = True,
    ema_fast: int = 12,
    ema_slow: int = 26,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
) -> BacktestResult:
    """
    Simula vela por vela el comportamiento del agente con gestión de riesgo institucional.
    """
    market = get_market_type(symbol)
    # Comisiones realistas por lado: Cripto ~0.05%, Forex ~0.01%
    fee_rate = 0.0005 if market == "crypto" else 0.0001
    max_leverage = 6.0 if market == "crypto" else 20.0

    df_ind = compute_indicators(
        df_raw,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )
    df = generate_signals(
        df_ind,
        strategy_name=strategy_name,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
    )

    capital = initial_capital
    peak_capital = initial_capital
    max_dd_pct = 0.0

    in_position = False
    pos_side = 0  # 1 = LONG, -1 = SHORT
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    initial_risk_dist = 0.0
    pos_size = 0.0
    entry_time = pd.Timestamp.now(tz="UTC")
    entry_reason = ""

    trades: list[TradeRecord] = []
    equity_records: list[dict[str, object]] = []

    timestamps = df["timestamp"].tolist()
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    atrs = df["atr"].to_numpy()
    signals = df["signal"].to_numpy()
    reasons = df["signal_reason"].tolist()

    for i in range(len(df)):
        ts = timestamps[i]
        high_p = float(highs[i])
        low_p = float(lows[i])
        close_p = float(closes[i])
        atr_val = float(atrs[i])
        sig = int(signals[i])

        if in_position:
            exit_triggered = False
            exit_price = close_p
            exit_reason = ""

            if pos_side == 1:  # LONG
                if low_p <= stop_loss:
                    exit_triggered = True
                    exit_price = stop_loss
                    exit_reason = "Stop Loss / Trailing Stop"
                elif high_p >= take_profit:
                    exit_triggered = True
                    exit_price = take_profit
                    exit_reason = "Take Profit Alcanzado"
                elif sig == -1:
                    exit_triggered = True
                    exit_price = close_p
                    exit_reason = "Giro de Señal a SHORT"
                elif use_trailing_stop and initial_risk_dist > 0:
                    # Si el precio ya avanzó +1R a nuestro favor, subimos Stop Loss a Break-Even o Trailing
                    current_gain = high_p - entry_price
                    if current_gain >= 1.1 * initial_risk_dist:
                        new_sl = max(stop_loss, entry_price + 0.15 * initial_risk_dist, high_p - 1.35 * atr_val)
                        stop_loss = new_sl

            elif pos_side == -1:  # SHORT
                if high_p >= stop_loss:
                    exit_triggered = True
                    exit_price = stop_loss
                    exit_reason = "Stop Loss / Trailing Stop"
                elif low_p <= take_profit:
                    exit_triggered = True
                    exit_price = take_profit
                    exit_reason = "Take Profit Alcanzado"
                elif sig == 1:
                    exit_triggered = True
                    exit_price = close_p
                    exit_reason = "Giro de Señal a LONG"
                elif use_trailing_stop and initial_risk_dist > 0:
                    current_gain = entry_price - low_p
                    if current_gain >= 1.1 * initial_risk_dist:
                        new_sl = min(stop_loss, entry_price - 0.15 * initial_risk_dist, low_p + 1.35 * atr_val)
                        stop_loss = new_sl

            if exit_triggered:
                gross_pnl = (exit_price - entry_price) * pos_size * pos_side
                fees = (entry_price * pos_size + exit_price * pos_size) * fee_rate
                net_pnl = gross_pnl - fees
                prev_cap = capital
                capital = max(10.0, capital + net_pnl)
                pnl_pct_acc = (net_pnl / prev_cap) * 100.0 if prev_cap > 0 else 0.0

                trades.append(
                    TradeRecord(
                        symbol=symbol,
                        strategy=strategy_name,
                        side="LONG" if pos_side == 1 else "SHORT",
                        entry_time=entry_time,
                        exit_time=ts,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        position_size=pos_size,
                        pnl_usd=net_pnl,
                        pnl_pct_account=pnl_pct_acc,
                        exit_reason=exit_reason,
                        signal_reason=entry_reason,
                    )
                )
                in_position = False
                pos_side = 0

        # Evaluar nueva entrada si no estamos en posición
        if not in_position and sig != 0 and atr_val > 0 and i < len(df) - 1:
            risk_usd = capital * (risk_per_trade_pct / 100.0)
            stop_dist = sl_atr_mult * atr_val
            if stop_dist > 0:
                raw_size = risk_usd / stop_dist
                max_notional = capital * max_leverage
                if raw_size * close_p > max_notional:
                    raw_size = max_notional / close_p

                pos_size = raw_size
                pos_side = sig
                entry_price = close_p
                initial_risk_dist = stop_dist
                entry_time = ts
                entry_reason = str(reasons[i])
                if sig == 1:
                    stop_loss = entry_price - sl_atr_mult * atr_val
                    take_profit = entry_price + tp_atr_mult * atr_val
                else:
                    stop_loss = entry_price + sl_atr_mult * atr_val
                    take_profit = entry_price - tp_atr_mult * atr_val
                in_position = True

        # Calcular equidad flotante
        floating_pnl = 0.0
        if in_position:
            floating_pnl = (close_p - entry_price) * pos_size * pos_side
        current_equity = max(10.0, capital + floating_pnl)
        if current_equity > peak_capital:
            peak_capital = current_equity
        dd = ((peak_capital - current_equity) / peak_capital) * 100.0 if peak_capital > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

        equity_records.append(
            {
                "timestamp": ts,
                "equity": current_equity,
                "close": close_p,
                "drawdown_pct": -dd,
            }
        )

    # Estadísticas finales
    net_profit = capital - initial_capital
    total_return_pct = (net_profit / initial_capital) * 100.0
    first_close = float(closes[0]) if len(closes) > 0 else 1.0
    last_close = float(closes[-1]) if len(closes) > 0 else 1.0
    buy_and_hold_pct = ((last_close - first_close) / first_close) * 100.0

    total_trades = len(trades)
    wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losses = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_profit = float(sum(wins)) if wins else 0.0
    gross_loss = abs(float(sum(losses))) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (9.99 if gross_profit > 0 else 0.0)

    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = (net_profit / total_trades) if total_trades > 0 else 0.0

    eq_df = pd.DataFrame(equity_records)
    if not eq_df.empty and len(eq_df) > 2:
        rets = eq_df["equity"].pct_change().dropna()
        std_ret = float(rets.std())
        sharpe = float((rets.mean() / std_ret) * np.sqrt(252 * 24)) if std_ret > 1e-9 else 0.0
    else:
        sharpe = 0.0

    return BacktestResult(
        symbol=symbol,
        strategy=strategy_name,
        strategy_label=STRATEGY_CATALOG.get(strategy_name, strategy_name),
        initial_capital=initial_capital,
        final_capital=capital,
        net_profit_usd=net_profit,
        total_return_pct=total_return_pct,
        buy_and_hold_pct=buy_and_hold_pct,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        avg_win_usd=avg_win,
        avg_loss_usd=avg_loss,
        expectancy_usd=expectancy,
        params={
            "strategy": strategy_name,
            "sl_atr_mult": sl_atr_mult,
            "tp_atr_mult": tp_atr_mult,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
        },
        trades=trades,
        equity_curve=eq_df,
    )


def optimize_for_symbol(
    df_raw: pd.DataFrame,
    symbol: str,
    initial_capital: float = 1000.0,
    risk_per_trade_pct: float = 2.0,
) -> tuple[BacktestResult, list[BacktestResult]]:
    """
    Prueba las 4 estrategias técnicas y distintas relaciones Riesgo/Beneficio (Stop Loss / Take Profit)
    sobre el activo, y selecciona la configuración más rentable y sólida (mayor retorno ajustado por Drawdown).
    """
    candidate_configs = [
        {"strategy_name": "confluence", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "ema_fast": 12, "ema_slow": 26},
        {"strategy_name": "confluence", "sl_atr_mult": 1.8, "tp_atr_mult": 3.8, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "trend_macd", "sl_atr_mult": 1.6, "tp_atr_mult": 3.5, "ema_fast": 12, "ema_slow": 26},
        {"strategy_name": "trend_macd", "sl_atr_mult": 2.2, "tp_atr_mult": 4.5, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "bollinger_reversion", "sl_atr_mult": 1.4, "tp_atr_mult": 2.6, "ema_fast": 12, "ema_slow": 26},
        {"strategy_name": "bollinger_reversion", "sl_atr_mult": 1.8, "tp_atr_mult": 3.2, "ema_fast": 20, "ema_slow": 50},
        {"strategy_name": "volatility_breakout", "sl_atr_mult": 1.5, "tp_atr_mult": 3.5, "ema_fast": 12, "ema_slow": 26},
        {"strategy_name": "volatility_breakout", "sl_atr_mult": 1.8, "tp_atr_mult": 4.0, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "ema_pullback", "sl_atr_mult": 1.6, "tp_atr_mult": 3.4, "ema_fast": 12, "ema_slow": 26},
        {"strategy_name": "ema_pullback", "sl_atr_mult": 2.0, "tp_atr_mult": 4.2, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "momentum_shark", "sl_atr_mult": 1.3, "tp_atr_mult": 3.8, "ema_fast": 8, "ema_slow": 21},
        {"strategy_name": "momentum_shark", "sl_atr_mult": 1.5, "tp_atr_mult": 4.5, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "chimichurri_alpha", "sl_atr_mult": 1.6, "tp_atr_mult": 4.2, "ema_fast": 9, "ema_slow": 21},
        {"strategy_name": "chimichurri_alpha", "sl_atr_mult": 1.8, "tp_atr_mult": 5.0, "ema_fast": 12, "ema_slow": 26},
    ]

    results: list[BacktestResult] = []
    for cfg in candidate_configs:
        res = run_backtest(
            df_raw=df_raw,
            symbol=symbol,
            strategy_name=str(cfg["strategy_name"]),
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            sl_atr_mult=float(cfg["sl_atr_mult"]),
            tp_atr_mult=float(cfg["tp_atr_mult"]),
            ema_fast=int(cfg["ema_fast"]),
            ema_slow=int(cfg["ema_slow"]),
        )
        results.append(res)

    # Ordenar por ganancia neta penalizando drawdowns excesivos y premiando un mínimo de operaciones
    def score_result(r: BacktestResult) -> float:
        trade_penalty = -50.0 if r.total_trades < 4 else 0.0
        return r.total_return_pct - (0.35 * r.max_drawdown_pct) + trade_penalty

    results.sort(key=score_result, reverse=True)
    return results[0], results
