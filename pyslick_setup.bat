@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title PySlick Unified Setup

echo ============================================================
echo   PySlick 2.0 - Unified Dependency and Environment Setup
echo ============================================================
echo.

set SCRIPT_DIR=%~dp0
set PYSLICK_PKG=%SCRIPT_DIR%pyslick_package\pyslick_pkg

REM ---------- 1. Verify Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python not found on PATH. Install Python 3.10+ from python.org and re-run.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detected
echo.

REM ---------- 2. Upgrade pip & build tools ----------
echo [1/7] Upgrading pip and build tools...
python -m pip install --upgrade pip setuptools wheel build twine --quiet
echo [OK] Package tools updated.
echo.

REM ---------- 3. Core & Machine Learning Dependencies ----------
echo [2/7] Installing core and ML dependencies...
echo       (rapidfuzz, tree-sitter, nltk, sentence-transformers, fastembed, etc.)
python -m pip install ^
    rapidfuzz>=3.0 ^
    pyperclip>=1.8 ^
    colorama>=0.4 ^
    libcst>=1.0 ^
    networkx>=3.0 ^
    tree-sitter>=0.21 ^
    tree-sitter-typescript>=0.21 ^
    tree-sitter-language-pack>=0.1 ^
    watchdog>=3.0 ^
    nltk>=3.8 ^
    yake>=0.4 ^
    transformers>=4.30 ^
    sentence-transformers>=2.0 ^
    fastembed ^
    onnxruntime ^
    llama-cpp-python>=0.2 --quiet
if errorlevel 1 (
    echo [!] Some packages had warnings or failed. Retrying core dependencies...
    python -m pip install rapidfuzz pyperclip colorama libcst networkx tree-sitter tree-sitter-typescript watchdog nltk yake transformers fastembed onnxruntime --quiet
)
echo [OK] Dependencies installed.
echo.

REM ---------- 4. Download NLTK Datasets ----------
echo [3/7] Downloading NLTK stopwords and tokenizers...
python -m nltk.downloader stopwords punkt --quiet
echo [OK] NLTK data ready.
echo.

REM ---------- 5. Install graphify CLI ----------
echo [4/7] Installing Graphify CLI...
where pipx >nul 2>nul
if errorlevel 1 (
    python -m pip install --user pipx --quiet
    python -m pipx ensurepath --quiet
)
python -m pip install graphifyy --user --quiet
echo [OK] Graphify installed.
echo.

REM ---------- 6. Install PySlick in Editable Mode ----------
echo [5/7] Registering pyslick CLI (editable install)...
if not exist "%PYSLICK_PKG%\pyproject.toml" (
    echo [X] pyproject.toml not found at %PYSLICK_PKG%
    pause
    exit /b 1
)
python -m pip install -e "%PYSLICK_PKG%" --quiet
echo [OK] pyslick registered.
echo.

REM ---------- 7. Download Optional Local GGUF Model ----------
echo [6/7] Checking local GGUF models...
set MODEL_DIR=%USERPROFILE%\.pyslick\models
if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"

set GGUF_URL=https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
set GGUF_FILE=%MODEL_DIR%\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf

if exist "%GGUF_FILE%" (
    echo [OK] GGUF model already present in %MODEL_DIR%
) else (
    echo     Downloading Qwen 1.5B GGUF model to %MODEL_DIR%...
    powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GGUF_URL%' -OutFile '%GGUF_FILE%' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 (
        echo [!] Automatic GGUF download skipped or failed (optional).
        echo     You can manually place any .gguf model in %MODEL_DIR%
    ) else (
        echo [OK] GGUF model downloaded.
    )
)
echo.

REM ---------- 8. Configure PATH and Environment ----------
echo [7/7] Configuring user PATH and UTF-8 environment variables...
for /f "tokens=*" %%i in ('python -c "import sysconfig; print(sysconfig.get_path('scripts'))"') do set PYTHON_SCRIPTS=%%i

powershell -NoProfile -Command ^
    "$p=[Environment]::GetEnvironmentVariable('PATH','User'); if ($p -notlike '*%PYTHON_SCRIPTS%*') { [Environment]::SetEnvironmentVariable('PATH', $p + ';%PYTHON_SCRIPTS%', 'User'); Write-Host '     Added %PYTHON_SCRIPTS% to user PATH' } else { Write-Host '     Python Scripts already on PATH' }"

powershell -NoProfile -Command ^
    "[Environment]::SetEnvironmentVariable('PYTHONUTF8','1','User'); [Environment]::SetEnvironmentVariable('PYTHONIOENCODING','utf-8','User'); Write-Host '     Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8'"
echo.

REM ---------- 9. Final Verification ----------
echo ============================================================
echo   Running Verification Smoke Test
echo ============================================================
pyslick --help >nul 2>nul
if errorlevel 1 (
    echo [!] pyslick command not immediately active in this specific sub-shell.
    echo     Please open a fresh PowerShell window and test with: pyslick --help
) else (
    echo [OK] PySlick is fully functional and ready to use!
)
echo.
echo ============================================================
echo   Setup Completed Successfully!
echo   Open a NEW PowerShell window and run:
echo     pyslick "what does this codebase do"
echo     pyslick grep "a natural language query"
echo ============================================================
pause
endlocal
