@echo off
setlocal enabledelayedexpansion
title PySlick Setup

echo ============================================================
echo   PySlick Universal Recon - One-Time Setup
echo ============================================================
echo.

REM ---------- 1. Verify Python 3.12 ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python not found on PATH. Install Python 3.12 from python.org and re-run.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detected
echo.

REM ---------- 2. Upgrade pip ----------
echo [1/6] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo.

REM ---------- 3. Core third-party deps ----------
echo [2/6] Installing core dependencies (this may take a while)...
python -m pip install ^
    llama-cpp-python ^
    sentence-transformers ^
    graphify ^
    pyperclip ^
    colorama ^
    libcst ^
    networkx ^
    rapidfuzz ^
    tree-sitter ^
    tree-sitter-typescript ^
    tree-sitter-language-pack ^
    watchdog
if errorlevel 1 (
    echo [X] Core dependency install failed. Scroll up for the error.
    pause
    exit /b 1
)
echo [OK] Core dependencies installed
echo.

REM ---------- 4. Install pyslick editable ----------
echo [3/6] Installing pyslick (editable)...
set PYSLICK_PKG=C:\Users\yuze2\Documents\Pyslick-2.0-by-Rence\pyslick_package\pyslick_pkg
if not exist "%PYSLICK_PKG%\pyproject.toml" (
    echo [X] pyproject.toml not found at %PYSLICK_PKG%
    echo     Edit PYSLICK_PKG in this script to point to the right folder.
    pause
    exit /b 1
)
python -m pip install -e "%PYSLICK_PKG%"
if errorlevel 1 (
    echo [X] Editable install of pyslick failed.
    pause
    exit /b 1
)
echo [OK] pyslick installed
echo.

REM ---------- 5. Download GGUF model ----------
echo [4/6] Downloading Qwen2.5-Coder-1.5B-Instruct GGUF...
set MODEL_DIR=%USERPROFILE%\.cache\pyslick\models
if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"

set GGUF_URL=https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
set GGUF_FILE=%MODEL_DIR%\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf

if exist "%GGUF_FILE%" (
    echo [OK] GGUF already present, skipping download
) else (
    echo     Downloading to %GGUF_FILE%
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%GGUF_URL%' -OutFile '%GGUF_FILE%' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 (
        echo [!] Automatic GGUF download failed.
        echo     Open this URL in a browser, then place the .gguf file in:
        echo     %MODEL_DIR%
        echo     %GGUF_URL%
    ) else (
        echo [OK] GGUF downloaded
    )
)
echo.

REM ---------- 6. Add Scripts dir to user PATH ----------
echo [5/6] Ensuring Python Scripts dir is on user PATH...
set SCRIPTS_DIR=%LOCALAPPDATA%\Programs\Python\Python312\Scripts
powershell -NoProfile -Command ^
    "$p=[Environment]::GetEnvironmentVariable('PATH','User'); if ($p -notlike '*%SCRIPTS_DIR%*') { [Environment]::SetEnvironmentVariable('PATH', $p + ';%SCRIPTS_DIR%', 'User'); Write-Host '     Added %SCRIPTS_DIR% to user PATH' } else { Write-Host '     Already on PATH' }"
echo.

REM ---------- 7. Smoke test ----------
echo [6/6] Smoke test...
where pyslick >nul 2>nul
if errorlevel 1 (
    echo [!] pyslick not on PATH yet in this shell.
    echo     Close this window, open a NEW PowerShell, and run: pyslick --help
) else (
    pyslick --help >nul 2>nul
    if errorlevel 1 (
        echo [X] pyslick found but failed to run. Run "pyslick --help" manually to see the error.
    ) else (
        echo [OK] pyslick works
    )
)
echo.
echo ============================================================
echo   Setup complete.
echo   Open a NEW PowerShell window for PATH changes to apply.
echo   Then run:  pyslick "query what does this codebase do"
echo ============================================================
pause
endlocal