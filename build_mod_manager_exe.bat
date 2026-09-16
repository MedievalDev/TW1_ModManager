@echo off
rem Baut den TW1 Mod Manager als eine Exe (PyInstaller, onefile, ohne Konsole).
rem Ergebnis: %~dp0dist\TW1_Mod_Manager.exe - danach auf den Desktop kopieren.
setlocal
pushd "%~dp0"
"C:\Users\marco\AppData\Local\Programs\Python\Python313\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
  --name "TW1_Mod_Manager" --icon "%~dp0mod_manager.ico" --add-data "%~dp0mod_manager.ico;." ^
  --hidden-import theme --hidden-import guidebook --hidden-import updater --hidden-import version ^
  --distpath "%~dp0dist" --workpath "%TEMP%\mod_manager_build" --specpath "%TEMP%\mod_manager_build" mod_manager.py
set rc=%errorlevel%
popd
if %rc% neq 0 (echo BUILD FEHLGESCHLAGEN & exit /b %rc%)
echo BUILD OK: %~dp0dist\TW1_Mod_Manager.exe
