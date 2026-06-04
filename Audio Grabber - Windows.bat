@echo off
rem Audio Grabber launcher for Windows — requires Python 3 (python.org or "winget install Python.Python.3.12")
set "SRV=%~dp0Audio Grabber.app\Contents\Resources\server.py"
where pythonw >nul 2>nul && (start "" pythonw "%SRV%" & exit /b)
where python >nul 2>nul && (start "" /min python "%SRV%" & exit /b)
echo Python 3 is required but was not found.
echo Install it from https://www.python.org/downloads/ and check "Add python.exe to PATH".
pause
