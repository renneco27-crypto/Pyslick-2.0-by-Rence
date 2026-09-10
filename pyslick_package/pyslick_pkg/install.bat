@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  pyslick installer — https://github.com/cortega26/pyslick
::  Installs pyslick globally so you can run it from anywhere.
:: ============================================================

echo.
echo  ██████╗ ██╗   ██╗███████╗██╗     ██╗ ██████╗██╗  ██╗
echo  ██╔══██╗╚██╗ ██╔╝██╔════╝██║     ██║██╔════╝██║ ██╔╝
echo  ██████╔╝ ╚████╔╝ ███████╗██║     ██║██║     █████╔╝
echo  ██╔═══╝   ╚██╔╝  ╚════██║██║     ██║██║     ██╔═██╗
echo  ██║        ██║   ███████║███████╗██║╚██████╗██║  ██╗
echo  ╚═╝        ╚═╝   ╚══════╝╚══════╝╚═╝ ╚═════╝╚═╝  ╚═╝
echo.
echo  Smart code navigator + patcher  ^|  v0.2.0
echo  ──────────────────────────────────────────────────────
echo.

:: ── 1. Check Python ──────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found on PATH.
    echo          Download it from https://www.python.org/downloads/
    echo          Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER% found.

:: ── 2. Check pip ─────────────────────────────────────────────
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] pip not found. Try: python -m ensurepip --upgrade
    pause
    exit /b 1
)
echo  [OK] pip found.

:: ── 3. Locate the package folder ─────────────────────────────
::  Expected layout after unzip:
::    pyslick_pkg\
::      pyslick_pkg\          <── this is where pyproject.toml lives
::        pyslick\
::        pyproject.toml
::
set "SCRIPT_DIR=%~dp0"
set "PKG_DIR=%SCRIPT_DIR%"

:: If install.bat sits inside the outer pyslick_pkg, look one level in
if exist "%SCRIPT_DIR%pyproject.toml" (
    set "PKG_DIR=%SCRIPT_DIR%"
) else if exist "%SCRIPT_DIR%pyslick_pkg\pyproject.toml" (
    set "PKG_DIR=%SCRIPT_DIR%pyslick_pkg\"
) else (
    echo  [ERROR] Cannot find pyproject.toml.
    echo          Make sure install.bat is in the same folder as pyproject.toml
    echo          (usually: pyslick_pkg\pyslick_pkg\install.bat)
    pause
    exit /b 1
)

echo  [OK] Package found at: %PKG_DIR%

:: ── 4. Install ───────────────────────────────────────────────
echo.
echo  Installing pyslick globally (editable mode)...
echo.
pip install -e "%PKG_DIR%" --quiet
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] pip install failed. Try running this script as Administrator,
    echo          or install to user site: pip install -e "%PKG_DIR%" --user
    pause
    exit /b 1
)

:: ── 5. Verify the command registered ─────────────────────────
pyslick --help >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [WARN] pyslick installed but the command isn't on PATH yet.
    echo         This usually means Python's Scripts folder isn't in PATH.
    echo.
    for /f "delims=" %%s in ('python -c "import sysconfig; print(sysconfig.get_path(\"scripts\"))"') do set SCRIPTS=%%s
    echo         Add this folder to your PATH and restart PowerShell:
    echo         !SCRIPTS!
    echo.
    echo         Quick fix — paste this in PowerShell (once, as yourself):
    echo         [Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";!SCRIPTS!", "User")
    echo.
) else (
    echo.
    echo  ══════════════════════════════════════════════════════
    echo   pyslick installed successfully!
    echo  ══════════════════════════════════════════════════════
    echo.
    echo   Run from any project folder:
    echo.
    echo     pyslick query "what renders the login button"
    echo     pyslick ls
    echo     pyslick lines src\app\page.tsx
    echo     pyslick grep index.html "addEventListener"
    echo     pyslick patchit src\app\page.tsx -l
    echo     pyslick recon "find and fix the mic button size"
    echo.
    echo   Re-install after updates:
    echo     pip install -e . --force-reinstall --no-deps
    echo.
)

pause
endlocal
