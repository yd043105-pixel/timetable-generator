@echo off
title Timetable Generator - DEBUG Build (console visible)
echo Building DEBUG EXE with console (to see errors if it crashes)...
where python >nul 2>nul
if errorlevel 1 ( echo [ERROR] Python not installed. & pause & exit /b )
python -m pip install --upgrade ortools openpyxl pyside6 pyinstaller
python -m PyInstaller --onefile --name TimetableGenerator_debug --collect-all ortools --collect-all openpyxl --add-data "template.xlsx;." gui_qt.py
if errorlevel 1 ( echo [ERROR] Build failed. & pause & exit /b )
copy /Y dist\TimetableGenerator_debug.exe TimetableGenerator_debug.exe >nul
echo DONE! Run TimetableGenerator_debug.exe ; if it crashes the console shows the error.
echo Also check %USERPROFILE%\시간표생성기_오류기록.txt
pause
