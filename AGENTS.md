# Contexto Automático del Proyecto para Antigravity (`agentes`)

Cuando el usuario abra este proyecto en otra PC (por ejemplo, **la PC del club**) y pida prender o poner a funcionar el agente:

1. **Qué es este repositorio:**
   - Es el **Agente de Trading Cuantitativo Multi-Mercado (Cripto + Forex + Oro) en Modo Tiburón Agresivo**.
   - Opera en ciclos autónomos de **60 minutos de Trading en Vivo** + **20 minutos de Auto-Corrección y Recalibración** (`overnight_supervisor.py`).
   - Escanea 18 mercados en tiempo real (`PEPE/USDT`, `SOL/USDT`, `DOGE/USDT`, `XAU/USD`, `GBP/USD`, `EUR/USD`, `AUD/USD`, `ADA/USDT`, `LINK/USDT`, `SUI/USDT`, `ETH/USDT`, etc.), descartando automáticamente los de bajo Profit Factor (`< 1.15`) y potenciando el riesgo (`3.5%` base, `x1.35` = `~4.7%` en mercados Élite con `PF >= 1.70`).
   - Toda la cartera, saldo en vivo (`$10,000 USD` iniciales + ganancias acumuladas) y posiciones abiertas están persistidas en `live_portfolio.json`, `adaptive_config.json` y `overnight_log.txt`.

2. **Qué debe hacer el asistente apenas el usuario diga "prendelo" o "hacé todo acá":**
   - Ejecutar `powercfg /change standby-timeout-ac 0` y `powercfg /change hibernate-timeout-ac 0` para que la PC del club no se suspenda ni se apague sola.
   - Verificar si están instalados `pandas`, `numpy`, `requests`, `urllib3`, `streamlit`, `altair` (`python -m pip install pandas numpy requests urllib3 streamlit altair`).
   - Iniciar en segundo plano (`IsDaemon=true`) el supervisor autónomo: `python -u overnight_supervisor.py`.
   - Iniciar en segundo plano (`IsDaemon=true`) el panel web interactivo: `python -m streamlit run dashboard.py --server.headless true --server.port 8501`.
   - Informar al usuario el estado actual de `live_portfolio.json` y darle el link `http://localhost:8501`.
