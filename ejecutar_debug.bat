@echo off
setlocal
title Planos PDF y DXF - Servidor local
pushd "%~dp0" || exit /b 1
set "PYTHONIOENCODING=utf-8"

if exist ".venv-local\Scripts\python.exe" (
    ".venv-local\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 goto local_python
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 goto project_python
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto launcher_python
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto path_python

echo No se encontro Python 3.10 o superior. Instalalo y vuelve a ejecutar este archivo.
set "RESULT=1"
goto finish

:local_python
".venv-local\Scripts\python.exe" scripts\start_local.py %*
goto capture_result

:project_python
".venv\Scripts\python.exe" scripts\start_local.py %*
goto capture_result

:launcher_python
py -3 scripts\start_local.py %*
goto capture_result

:path_python
python scripts\start_local.py %*

:capture_result
set "RESULT=%ERRORLEVEL%"

:finish
popd
if not "%RESULT%"=="0" if "%~1"=="" pause
exit /b %RESULT%
