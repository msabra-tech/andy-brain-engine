@echo off
setlocal
set "ROOT=%~dp0"
py -3 "%ROOT%brain" run-once
py -3 "%ROOT%brain" notifications send
endlocal
