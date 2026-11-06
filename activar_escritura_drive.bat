@echo off
title AUTORIZAR PERMISO DE ESCRITURA EN GOOGLE DRIVE (SUBIDA PDF Y XLSX/CSV)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ========================================================================
echo   HABILITAR SUBIDA DE ARCHIVOS (.PDF y .XLSX/.CSV) A GOOGLE DRIVE
echo ========================================================================
echo.
echo El token anterior fue generado con alcance "drive.readonly" (Solo lectura).
echo Se abrira tu navegador con el enlace oficial de tu proyecto para autorizar
echo el permiso completo "https://www.googleapis.com/auth/drive" (Lectura y Escritura).
echo.
start "" "https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=https%%3A%%2F%%2Fdevelopers.google.com%%2Foauthplayground&prompt=consent&response_type=code&client_id=957819459488-nrvatc308qs0tiupq6gim5ldc6ct6bsh.apps.googleusercontent.com&scope=https%%3A%%2F%%2Fwww.googleapis.com%%2Fauth%%2Fdrive&access_type=offline"

echo 1. Acepta el permiso en el navegador (cuenta: vicente.shent@gmail.com).
echo 2. Copia la URL a la que te redirige (o el codigo "4/0A...") y pegala aqui abajo:
echo.
set /p "OAUTH_INPUT=Pega aqui la URL o el codigo y presiona ENTER: "

echo.
echo Canjeando codigo y actualizando credenciales...
node gdrive-sync-cli.js --exchange-code "%OAUTH_INPUT%"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] No se pudo canjear el codigo. Verifica que no haya expirado.
    pause
    exit /b 1
)

echo.
echo [OK] Permiso de escritura activado! Subiendo tus proyectos actuales a Google Drive...
set "PY_EXE=C:\Users\Usuario\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"

"%PY_EXE%" -c "import os, time, django; os.environ['APP_TERMINAL_DEBUG']='1'; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings'); django.setup(); from mobileApp.models import PdfPlan, WorkbookDocument; from mobileApp.terminal_monitor import on_pdf_plan_saved, on_workbook_saved, _SYNC_QUEUE; [on_pdf_plan_saved(PdfPlan, p, False) for p in PdfPlan.objects.all()]; [on_workbook_saved(WorkbookDocument, p.workbook, False) for p in PdfPlan.objects.filter(workbook__isnull=False)]; _SYNC_QUEUE.join()"

echo.
echo ========================================================================
echo   LISTO! Tus archivos .pdf y .xlsx ya estan en tus carpetas de Drive
echo   y vinculados en Supabase Cloud.
echo ========================================================================
pause

