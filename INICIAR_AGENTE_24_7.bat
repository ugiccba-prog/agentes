@echo off
chcp 65001 >nul
title 🤖 Lanzador 24/7 - Agente de Trading Modo Tiburon
cd /d "%~dp0"

echo ============================================================================
echo  🦈 CONFIGURANDO PC PARA OPERAR 24/7 SIN APAGARSE (ANTI-SUSPENSION)
echo ============================================================================
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 30
echo [OK] Suspension automatica de Windows desactivada.

echo.
echo ============================================================================
echo  📦 VERIFICANDO LIBRERIAS DE PYTHON...
echo ============================================================================
python -m pip install --quiet pandas numpy requests urllib3 streamlit altair

echo.
echo ============================================================================
echo  🚀 INICIANDO PANEL VISUAL WEB (http://localhost:8501)
echo ============================================================================
start "Panel Web Trading (Streamlit)" cmd /k "cd /d "%~dp0" && python -m streamlit run dashboard.py --server.port 8501"

echo.
echo ============================================================================
echo  🌙 INICIANDO SUPERVISOR AUTONOMO MODO TIBURON (SESIONES 1H + 20M)
echo ============================================================================
:loop
python -u overnight_supervisor.py
echo [AVISO] El proceso se detuvo, reintentando en 10 segundos...
timeout /t 10
goto loop
