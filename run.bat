@echo off
echo Starting backend infrastructure (Mosquitto, etcd, Home Assistant)...
docker-compose up -d

echo.
echo Waiting for backend to initialize...
timeout /t 5 /nobreak >nul

echo Starting Active Coordinator...
start "Active Coordinator" cmd /k "call venv\Scripts\activate.bat & python src\coordinator.py"

echo Waiting 2 seconds before starting the Standby Coordinator...
timeout /t 2 /nobreak >nul

echo Starting Standby Coordinator...
start "Standby Coordinator" cmd /k "call venv\Scripts\activate.bat & python src\coordinator.py"

echo Starting Sensor Traffic Simulator...
start "Sensor Traffic Sim" cmd /k "call venv\Scripts\activate.bat & python src\sensor_sim.py"

echo.
echo ===================================================================
echo All components launched!
echo Home Assistant is available at http://localhost:8123
echo Close the terminal windows to stop the python scripts.
echo Run 'docker-compose down' to stop the backend infrastructure.
echo ===================================================================
pause
