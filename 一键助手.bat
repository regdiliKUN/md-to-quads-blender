@echo off
rem NOTE: this file is intentionally ASCII-only (Chinese text is printed by the Python launcher)
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title MD2Q Helper

rem ---- pick a Python: py / python / python3 / Blender's bundled python ----
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (where python >nul 2>nul && set "PYCMD=python")
if not defined PYCMD (where python3 >nul 2>nul && set "PYCMD=python3")
if not defined PYCMD call :find_blender_python

if not defined PYCMD (
  echo.
  echo   [!] Python not found, so this helper cannot run.
  echo.
  echo   Option 1: install Python 3 from https://www.python.org/downloads/
  echo             tick "Add Python to PATH", then double-click this file again.
  echo.
  echo   Option 2: install the add-on manually inside Blender:
  echo             Edit ^> Preferences ^> Add-ons ^> Install... ^> pick the
  echo             md_to_quads-*.zip that sits next to this file.
  echo.
  pause
  exit /b 1
)

%PYCMD% "%~dp0tools\md_launcher.py" %*
endlocal
exit /b 0

:find_blender_python
for /d %%D in ("%ProgramFiles%\Blender Foundation\*") do (
  if exist "%%~D\blender.exe" (
    for /d %%V in ("%%~D\*") do (
      if exist "%%~V\python\bin\python.exe" set "PYCMD=%%~V\python\bin\python.exe"
    )
  )
)
if not defined PYCMD (
  for /d %%D in ("%ProgramFiles(x86)%\Blender Foundation\*") do (
    if exist "%%~D\blender.exe" (
      for /d %%V in ("%%~D\*") do (
        if exist "%%~V\python\bin\python.exe" set "PYCMD=%%~V\python\bin\python.exe"
      )
    )
  )
)
exit /b 0
