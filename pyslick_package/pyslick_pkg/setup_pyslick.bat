@echo off
setlocal enabledelayedexpansion

:: ─────────────────────────────────────────────────────────────────────────────
:: setup_pyslick.bat  —  single-file setup, works on any PC
:: Lives at:  <anywhere>\pyslick\pyslick_package\setup_pyslick.bat
::
:: Steps:
::   1. Resolve root dir (one level up from this file)
::   2. Write __main__.py
::   3. Write pyslick.bat at root (goes on PATH)
::   4. Register "recon" command inline into __init__.py
::   5. Install package (editable)
::   6. Add root to User PATH
:: ─────────────────────────────────────────────────────────────────────────────

:: This bat lives inside pyslick_package\ — go up one for the real root
set "PACKAGE_DIR=%~dp0pyslick"
pushd "%~dp0.."
set "ROOT_DIR=%CD%"
popd

set "VENV_PYTHON=%ROOT_DIR%\venv\Scripts\python.exe"
set "MAIN_PY=%PACKAGE_DIR%\__main__.py"
set "INIT_PY=%PACKAGE_DIR%\__init__.py"
set "RECON_PY=%PACKAGE_DIR%\recon.py"
set "PYSLICK_BAT=%ROOT_DIR%\pyslick.bat"

:: Pick python — prefer venv
if exist "%VENV_PYTHON%" (
    set "PY=%VENV_PYTHON%"
) else (
    set "PY=python"
)

echo.
echo =======================================================
echo  PySlick Setup
echo  Root    : %ROOT_DIR%
echo  Package : %PACKAGE_DIR%
echo  Python  : %PY%
echo =======================================================
echo.

:: ── Sanity check ─────────────────────────────────────────────────────────────
if not exist "%PACKAGE_DIR%" (
    echo [ERROR] Package folder not found: %PACKAGE_DIR%
    echo         Make sure setup_pyslick.bat is inside pyslick_package\
    goto :END
)

:: ── 1. Write __main__.py ─────────────────────────────────────────────────────
echo [1/5] Writing __main__.py ...
(
    echo import sys
    echo from pyslick import main
    echo.
    echo if __name__ == "__main__":
    echo     main^(^)
) > "%MAIN_PY%"
echo [OK] %MAIN_PY%

:: ── 2. Write pyslick.bat at root ─────────────────────────────────────────────
echo [2/5] Writing pyslick.bat ...
(
    echo @echo off
    echo set "VENV_PYTHON=%%~dp0venv\Scripts\python.exe"
    echo if exist "%%VENV_PYTHON%%" ^(
    echo     "%%VENV_PYTHON%%" -m pyslick %%*
    echo ^) else ^(
    echo     python -m pyslick %%*
    echo ^)
) > "%PYSLICK_BAT%"
echo [OK] %PYSLICK_BAT%

:: ── 3. Register recon in __init__.py (inline Python, no extra file) ──────────
echo [3/5] Registering recon command ...

if not exist "%RECON_PY%" (
    echo [WARNING] recon.py not found at %RECON_PY%
    echo           Place recon.py in %PACKAGE_DIR% then re-run setup.
) else (
    "%PY%" -c ^
        "import sys; p=sys.argv[1]; src=open(p,encoding='utf-8').read(); already='command == \"recon\"' in src; print('[NOTICE] recon already registered.' if already else ''); exit(0) if already else None; src=src.replace('def import_patchit():\n    from patchit import main as patchit_main\n    return patchit_main','def import_patchit():\n    from patchit import main as patchit_main\n    return patchit_main\n\ndef import_recon():\n    from recon import main as recon_main\n    return recon_main'); src=src.replace('elif command == \"patchit\":','elif command == \"recon\":\n            try:\n                recon_main = import_recon()\n                sys.argv = [\"recon\"] + args\n                recon_main()\n            except Exception as e:\n                print(f\"Error: {e}\"); sys.exit(1)\n        elif command == \"patchit\":'); open(p+'.bak','w',encoding='utf-8').write(open(p,encoding='utf-8').read()); open(p,'w',encoding='utf-8').write(src); print('[OK] recon registered in __init__.py')" ^
        "%INIT_PY%"
)

:: ── 4. Install package ────────────────────────────────────────────────────────
echo [4/5] Installing pyslick_package ...
"%PY%" -m pip install -e "%~dp0." --quiet
echo [OK] Package installed.

:: ── 5. Add root to User PATH ──────────────────────────────────────────────────
echo [5/5] Checking User PATH ...
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"

echo !USER_PATH! | find /i "%ROOT_DIR%" >nul
if %errorlevel% equ 0 (
    echo [NOTICE] Already in User PATH.
) else (
    if defined USER_PATH (
        set "NEW_PATH=!USER_PATH!;%ROOT_DIR%"
    ) else (
        set "NEW_PATH=%ROOT_DIR%"
    )
    setx PATH "!NEW_PATH!" >nul
    echo [OK] Added to PATH: %ROOT_DIR%
)

echo.
echo =======================================================
echo  Done! Restart your terminal then run from anywhere:
echo    pyslick --help
echo    pyslick recon "make the microphone larger"
echo    pyslick log
echo =======================================================
echo.

:END
pause
