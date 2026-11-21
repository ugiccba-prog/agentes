# Laboratorio de investigación — Agente Tiburón

Estado: **experimental / solo lectura**. Esta rama no modifica el supervisor ni las siete carteras.

## Objetivo
Investigar y validar estrategias candidatas con evidencia reproducible antes de considerar su promoción. Ningún resultado histórico garantiza ganancias futuras.

## Estados
- `idea`: hipótesis sin prueba.
- `experimental`: datos y backtest disponibles; NO validada.
- `validated`: pasó todos los controles definidos, con trazabilidad.
- `rejected`: falló un control o carece de datos.
- `paused`: degradación detectada en observación posterior.

## Siete puertas (todas obligatorias)
1. Hipótesis explícita y falsable: quién pierde, por qué y bajo qué condiciones.
2. Costos: comisiones, spread, slippage y financiación; repetir con costos duplicados.
3. Fuera de muestra: train/validación/test cronológicos, sin elegir usando el test; walk-forward adicional.
4. Robustez: variaciones razonables de parámetros, activos y períodos.
5. Estrés: simulaciones de secuencias adversas, liquidez y caídas.
6. Referencia: superar alternativas simples después de costos, con riesgo comparable.
7. Muestra: reportar operaciones independientes, incertidumbre y límites; un umbral fijo no sustituye evidencia estadística.

## Barreras
- Prohibido conectar este laboratorio a `LiveExecutionBroker` o alterar `adaptive_config.json`, `wf_params.json`, `paper_*.json` y `overnight_log.txt`.
- No cambiar las siete carteras durante el período comparativo.
- Todo candidato empieza como `experimental`; no se autoproclama validado.
- Guardar fuente, fecha de descarga, zona horaria, versión de estrategia y parámetros.
- Datos ausentes o inválidos: prueba rechazada, nunca inventar precios.
- Solo velas cerradas para señales; entrada siguiente vela en simulaciones.
- No activar dinero real automáticamente; requerir autorización explícita y auditoría de ejecución.

## Hallazgos iniciales del repositorio
- `overnight_supervisor.py`: la canasta compartida puede mantener activos símbolos sin superar Walk-Forward; etiquetar como experimental, sin modificar comportamiento actual.
- `walk_forward.py`: existe división 50/25/25 y costos fijos estimados (0,03% por lado por defecto); revisar supuestos de ejecución y sensibilidad.
- `risk_manager.py`: hay límites de drawdown y riesgo agregado; Berserker permite hasta 4,5% por operación, solo apropiado para ensayo de alto riesgo.
- `exchange_connector.py`: `BinanceTestnetConnector.place_order` usa `verify=False`; corregir en rama y agregar pruebas antes de habilitar conectores.

## Próximos entregables
1. Tests automatizados de seguridad del conector y separación paper/ejecución.
2. Reporte JSON de puertas por candidato, sin modificar datos operativos.
3. Comparador de métricas de siete carteras, con fecha de corte y sin selección retrospectiva.
4. Investigador nocturno de solo lectura, con límites de consumo y bitácora.
5. Integración visual después de verificar pruebas y revisar resultados.

**Promoción a producción:** requiere revisión humana, tests verdes y decisión separada. Nunca fusionar automáticamente.
