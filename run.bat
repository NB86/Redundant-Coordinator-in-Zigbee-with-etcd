@echo off
setlocal
cd /d "%~dp0"
if "%WSL_DISTRO%"=="" set WSL_DISTRO=Ubuntu

echo Starting etcd (Docker)...
docker-compose up -d || goto :fail

if /I "%ZIGBEE_RADIO%"=="mock" goto skip_ncp
echo.
echo Starting two simulated EFR32MG24 NCPs on one 802.15.4 medium (Renode in WSL "%WSL_DISTRO%")...
echo   coordinator NCP: tcp 127.0.0.1:24842   end-device NCP: tcp 127.0.0.1:24852   Renode monitor: telnet 24999
start "Renode NCPs (WSL)" wsl -d %WSL_DISTRO% --cd "%~dp0" -- bash sim/run_ncps.sh
echo Waiting for the NCPs to boot...
timeout /t 12 /nobreak >nul
:skip_ncp

echo.
echo Waiting for etcd to initialize...
timeout /t 3 /nobreak >nul

REM Each node keeps its own copy of the zigpy database (both nodes run on this one machine).
echo Starting Active Coordinator...
start "Active Coordinator" cmd /k "call venv\Scripts\activate.bat & set ZIGBEE_DB=zigbee_devices_active.db & python src\coordinator.py"

echo Waiting 2 seconds before starting the Standby Coordinator...
timeout /t 2 /nobreak >nul

echo Starting Standby Coordinator...
start "Standby Coordinator" cmd /k "call venv\Scripts\activate.bat & set ZIGBEE_DB=zigbee_devices_standby.db & python src\coordinator.py"

if /I "%ZIGBEE_RADIO%"=="mock" goto launched
echo Starting the Zigbee End Device (second NCP joining the coordinator's network)...
start "Zigbee End Device" cmd /k "call venv\Scripts\activate.bat & python src\end_device_sim.py"

:launched
echo.
echo ===================================================================
echo All components launched!
echo Close the terminal windows to stop the python scripts.
echo Close the "Renode NCPs (WSL)" window to stop the simulated NCPs.
echo Run 'docker-compose down' to stop etcd ('docker-compose down -v' also wipes its data).
echo Set ZIGBEE_RADIO=mock before running this script to use the mock radio instead.
echo ===================================================================
pause
exit /b 0

:fail
echo Failed to start etcd. Is Docker Desktop running?
pause
exit /b 1
