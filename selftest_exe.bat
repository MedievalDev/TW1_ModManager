@echo off
rem Startet die gebaute Exe im Selbsttest-Modus und zeigt die Zeile (PY_TOOL_DESIGN.md 7.6).
set "MOD_MANAGER_SELFTEST=%TEMP%\mod_manager_selftest.txt"
del "%MOD_MANAGER_SELFTEST%" 2>nul
start "" /wait "%~dp0dist\TW1_Mod_Manager.exe"
type "%MOD_MANAGER_SELFTEST%"
