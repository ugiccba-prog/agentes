# 🤖 Agente de Trading Cuantitativo (Cripto & Forex)

Sistema autónomo de análisis técnico, backtesting institucional, optimización automática de estrategias y escáner de señales en tiempo real para **Criptomonedas** (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `BNB/USDT`) y **Forex** (`EUR/USD`, `GBP/USD`, `USD/JPY`, `AUD/USD`).

---

## 🚀 Cómo Ejecutar el Agente

### 1. Ejecutar el Backtest Multi-Mercado + Escáner de Señales en Vivo (Terminal)
```powershell
python agent.py
```
Opciones personalizables:
```powershell
# Analizar en velas de 4 horas con $2,000 USD por mercado y 1.5% de riesgo por operación
python agent.py --interval 4h --capital 2000 --risk 1.5

# Analizar pares específicos
python agent.py --symbols BTC/USDT ETH/USDT EUR/USD GBP/USD
```

### 2. Ejecutar el Operador en Tiempo Real (Cuenta Demo / Binance Testnet / OANDA Demo)
```powershell
# Ejecutar un ciclo de escaneo y apertura/gestión de posiciones en vivo (velas de 15m)
python live_trader.py

# Dejar al agente operando autónomamente en bucle continuo cada 60 segundos
python live_trader.py --loop --sleep 60

# Reiniciar la cuenta Demo a $10,000 USD
python live_trader.py --reset --balance 10000
```

### 3. Abrir el Panel Visual Interactivo (Dashboard Web)
```powershell
streamlit run dashboard.py
```

---

## 🧠 Arquitectura del Agente

1. **[`market_data.py`](./market_data.py)** — Conecta en tiempo real a la **API pública de Binance** (para Criptomonedas) y a la **API v8 de Yahoo Finance** (para divisas Forex), guardando caché local en `data_cache/`.
2. **[`indicators.py`](./indicators.py)** — Calcula indicadores técnicos vectorizados:
   - **Medias Móviles Exponenciales** (`EMA rápida`, `EMA lenta` y filtro de tendencia institucional `EMA 100`)
   - **RSI** (Relative Strength Index de Wilder)
   - **MACD** (Línea MACD, Señal e Histograma de impulso)
   - **Bandas de Bollinger** (Banda Superior, Media, Inferior, `%B` y compresión `Bandwidth`)
   - **ATR** (Average True Range) para adaptar el Stop Loss y Take Profit a la volatilidad real de cada moneda.
3. **[`strategies.py`](./strategies.py)** — Incluye 5 motores estratégicos:
   - `confluence`: Agente de confluencia multi-indicador (combina tendencia EMA + impulso MACD + zonas de Bollinger/RSI).
   - `trend_macd`: Seguidor de tendencia por cruces confirmados a favor de la media institucional.
   - `bollinger_reversion`: Reversión a la media en extremos de Bandas de Bollinger + sobreventa/sobrecompra de RSI (ideal para Forex y mercados laterales).
   - `volatility_breakout`: Ruptura de compresión de volatilidad con expansión de MACD.
   - `ema_pullback`: Entrada en retrocesos institucionales sobre la EMA rápida dentro de tendencias fuertes.
4. **[`backtester.py`](./backtester.py)** — Motor de simulación realista vela por vela:
   - **Position Sizing automático**: Calcula el tamaño exacto de cada compra/venta para nunca arriesgar más del `%` configurado (ej. `2%`) si salta el Stop Loss.
   - **Trailing Stop y Break-Even automático**: Cuando la operación avanza `+1.1R` a favor, protege la posición subiendo el Stop Loss automáticamente.
   - **Auto-Optimizador**: Evalúa todas las estrategias y configuraciones de Stop Loss / Take Profit en cada moneda y selecciona la más rentable y estable.
5. **[`agent.py`](./agent.py)** y **[`dashboard.py`](./dashboard.py)** — Interfaz de consola y Dashboard interactivo con curva de capital (`Equity Curve`), ranking de estrategias y exportación de auditoría en `trades_history.csv` y `agent_report.json`.
