@echo off
setlocal
cd /d "%~dp0"
if "%WSL_DISTRO%"=="" set WSL_DISTRO=Ubuntu

echo ==============================================================
echo  1/2  Python virtual environment
echo ==============================================================
python -m venv venv || goto :fail
call venv\Scripts\activate.bat
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail

if /I "%ZIGBEE_RADIO%"=="mock" (
    echo Skipping the simulator setup because ZIGBEE_RADIO=mock.
    goto :done
)

echo.
echo ==============================================================
echo  2/2  Zigbee NCP simulator inside WSL "%WSL_DISTRO%" (Renode)
echo ==============================================================
wsl -d %WSL_DISTRO% -e true >nul 2>&1
if errorlevel 1 (
    echo WSL distro "%WSL_DISTRO%" is not available. Install it with:  wsl --install -d Ubuntu
    echo or set WSL_DISTRO to the name of your distro ^(wsl -l^). Set ZIGBEE_RADIO=mock to skip the simulator.
    goto :fail
)
REM Strip CRLF in case the checkout converted line endings, then run the setup script from the repo root.
wsl -d %WSL_DISTRO% --cd "%~dp0" -- bash -c "sed -i 's/\r$//' sim/*.sh sim/renode/*.resc sim/renode/*.repl sim/renode/*.py sim/tools/*.py && bash sim/setup_wsl.sh" || goto :fail

:done
echo.
echo ==============================================================
echo Setup Complete!
echo You can now double-click 'run.bat' to launch the simulation.
echo ==============================================================
pause
exit /b 0

:fail
echo.
echo Setup FAILED - see the messages above.
pause
exit /b 1
