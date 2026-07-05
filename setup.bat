@echo off
echo Setting up Python Virtual Environment...
python -m venv venv
call venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ==============================================================
echo Setup Complete!
echo You can now double-click 'run.bat' to launch the simulation.
echo ==============================================================
pause
