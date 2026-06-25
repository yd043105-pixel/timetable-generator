@echo off
title Timetable Generator - Build EXE
echo ============================================================
echo   School Timetable Generator - Build EXE (PySide6 UI)
echo   (Run this only once. It takes several minutes.)
echo ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not installed. Install Python 3.10+ first.
  echo   IMPORTANT: check "Add Python to PATH" during install.
  pause
  exit /b
)
echo [1/3] Installing required libraries...
python -m pip install --upgrade pip
python -m pip install --upgrade ortools openpyxl pyside6 pyinstaller
if errorlevel 1 ( echo [ERROR] Library install failed. Check internet. & pause & exit /b )
echo.
echo [2/3] Building EXE... (please wait, this can take several minutes)
python -m PyInstaller --onefile --windowed --name TimetableGenerator --collect-all ortools --collect-all openpyxl --add-data "template.xlsx;." gui_qt.py
if errorlevel 1 ( echo [ERROR] Build failed. See messages above. & pause & exit /b )
echo.
echo [3/3] Copying EXE next to this script...
copy /Y dist\TimetableGenerator.exe TimetableGenerator.exe >nul
echo.
echo ============================================================
echo   DONE!  TimetableGenerator.exe is ready in THIS folder.
echo   Double-click it to run. (No console window will appear.)
echo ============================================================
pause
