@echo off
setlocal
set "ROOT=%~dp0"
py -3 "%ROOT%scripts\setup_wizard.py" %*
endlocal
