@echo off
rem NOTE: this file is intentionally ASCII-only (Chinese text is printed by the Python launcher)
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title MD2Q Desktop Shortcut

rem ---- pick a Python: py / python / python3 / Blender's bundled python ----
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (where python >nul 2>nul && set "PYCMD=python")
if not defined PYCMD (where python3 >nul 2>nul && set "PYCMD=python3")
if not defined PYCMD call :find_blender_python

if not defined PYCMD (
  echo.
  echo   [!] Python not found, so the shortcut cannot be created automatically.
  echo       You can also right-click the launcher .bat and choose
  echo       Send to ^> Desktop - create shortcut.
  echo.
  pause
  exit /b 1
)

%PYCMD% "%~dp0tools\md_launcher.py" --shortcut
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
