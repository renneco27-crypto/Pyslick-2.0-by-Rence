@echo off
set "VENV_PYTHON=%~dp0venv\Scripts\python.exe"
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -m pyslick %*
) else (
    python -m pyslick %*
)
