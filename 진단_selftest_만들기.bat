@echo off
title Self Test Builder
echo ============================================================
echo   ortools self-test builder
echo   Builds SelfTest.exe to find out WHERE it crashes.
echo ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 ( echo [ERROR] Python not installed. & pause & exit /b )
python -m pip install --upgrade ortools pyinstaller
echo.
echo Building SelfTest.exe (console visible)...
python -m PyInstaller --onefile --console --name SelfTest --collect-all ortools selftest.py
if errorlevel 1 ( echo [ERROR] Build failed. & pause & exit /b )
copy /Y dist\SelfTest.exe SelfTest.exe >nul
echo.
echo ============================================================
echo   DONE! Now run  SelfTest.exe
echo   It prints each step and writes  selftest_result.txt
echo   Send me that file (or the console screenshot).
echo ============================================================
pause
