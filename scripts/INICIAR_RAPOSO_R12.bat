@echo off
setlocal
set "RAPOSO_DATA_ROOT=%~dp0..\..\Raposo_Data"
cd /d "%~dp0..\app"
title RAPOSO BOT V3.86 - R12
where pyw >nul 2>&1 && start "" pyw -3 "main_v386_r11.py" && exit /b
where pythonw >nul 2>&1 && start "" pythonw "main_v386_r11.py" && exit /b
py -3 "main_v386_r11.py"
endlocal
