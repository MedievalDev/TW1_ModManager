@echo off
rem Baut den TW1 Mod Manager als eine Exe (PyInstaller, onefile, ohne Konsole).
rem Ergebnis: %~dp0dist\TW1_Mod_Manager.exe - danach auf den Desktop kopieren.
setlocal
pushd "%~dp0"
"C:\Users\marco\AppData\Local\Programs\Python\Python313\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
  --name "TW1_Mod_Manager" --icon "%~dp0mod_manager.ico" --add-data "%~dp0mod_manager.ico;." ^
  --hidden-import theme --hidden-import guidebook --hidden-import version --hidden-import levelcache --hidden-import tw1_lhc --hidden-import tw1_lnd --hidden-import fieldnames --add-data "%~dp0tw1_sdk_fields.json;." --hidden-import mergeui --hidden-import modscan --hidden-import lndmap --hidden-import tw1_par --hidden-import tw1_lan --hidden-import packages --hidden-import twse_install --hidden-import twse_patch --add-data "%~dp0bin\twse.dll;bin" ^
  --distpath "%~dp0dist" --workpath "%TEMP%\mod_manager_build" --specpath "%TEMP%\mod_manager_build" mod_manager.py
set rc=%errorlevel%
popd
if %rc% neq 0 (echo BUILD FEHLGESCHLAGEN & exit /b %rc%)
echo BUILD OK: %~dp0dist\TW1_Mod_Manager.exe
