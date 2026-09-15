$bat = @'
@echo off
setlocal EnableDelayedExpansion
set REPO=%1
if "%REPO%"=="" set REPO=%CD%
if not exist "%REPO%" (
    echo [ERROR] Path not found: %REPO%
    exit /b 1
)
echo.
echo ============================================================
echo   How to run: %REPO%
echo ============================================================
echo.
if exist "%REPO%\package.json" (
    echo [Node project detected]
    set PKG_MGR=npm
    if exist "%REPO%\pnpm-lock.yaml" set PKG_MGR=pnpm
    if exist "%REPO%\yarn.lock"      set PKG_MGR=yarn
    if exist "%REPO%\bun.lockb"      set PKG_MGR=bun
    findstr /C:"\"packageManager\"" "%REPO%\package.json" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        for /f "tokens=2 delims=:" %%a in ('findstr /C:"\"packageManager\"" "%REPO%\package.json"') do set RAW=%%a
        set RAW=!RAW:"=!
        set RAW=!RAW:,=!
        for /f "tokens=1 delims=@ " %%b in ("!RAW!") do set PKG_MGR=%%b
    )
    echo   Package manager: !PKG_MGR!
    echo.
    echo   Install:  !PKG_MGR! install
    echo   Dev:      !PKG_MGR! run dev
    echo   Build:    !PKG_MGR! run build
    echo   Test:     !PKG_MGR! test
    echo   Lint:     !PKG_MGR! run lint
    echo.
    echo   Scripts actually declared in package.json:
    powershell -NoProfile -Command "$j = Get-Content '%REPO%\package.json' -Raw | ConvertFrom-Json; if ($j.scripts) { $j.scripts.PSObject.Properties | ForEach-Object { Write-Host ('    {0,-20} -> {1}' -f $_.Name, $_.Value) } } else { Write-Host '    (no scripts)' }"
    echo.
)
if exist "%REPO%\pyproject.toml" (
    echo [Python project detected - pyproject.toml]
    echo   Create venv:  python -m venv .venv
    echo   Activate:     .venv\Scripts\activate
    echo   Install:      pip install -e .
    echo   Test:         pytest
    echo.
)
if exist "%REPO%\requirements.txt" (
    echo [Python requirements.txt detected]
    echo   Install:      pip install -r requirements.txt
    echo.
)
if exist "%REPO%\Cargo.toml" (
    echo [Rust project detected]
    echo   Build:  cargo build
    echo   Run:    cargo run
    echo   Test:   cargo test
    echo.
)
if exist "%REPO%\go.mod" (
    echo [Go project detected]
    echo   Build:  go build ./...
    echo   Run:    go run .
    echo   Test:   go test ./...
    echo.
)
if exist "%REPO%\pom.xml" (
    echo [Maven project detected]
    echo   Build:  mvn package
    echo   Test:   mvn test
    echo.
)
if exist "%REPO%\build.gradle" (
    echo [Gradle project detected]
    echo   Build:  gradlew build
    echo   Test:   gradlew test
    echo.
)
if exist "%REPO%\Gemfile" (
    echo [Ruby project detected]
    echo   Install:  bundle install
    echo   Test:     bundle exec rspec
    echo.
)
if exist "%REPO%\composer.json" (
    echo [PHP project detected]
    echo   Install:  composer install
    echo   Test:     composer test
    echo.
)
if exist "%REPO%\Dockerfile" (
    echo [Dockerfile present]
    echo   Build:  docker build -t app .
    echo   Run:    docker run -p 3000:3000 app
    echo.
)
if exist "%REPO%\docker-compose.yml" (
    echo [docker-compose.yml present]
    echo   Up:     docker compose up
    echo   Down:   docker compose down
    echo.
)
if exist "%REPO%\Makefile" (
    echo [Makefile present]  Available targets:
    findstr /R /C:"^[a-zA-Z0-9_-]*:" "%REPO%\Makefile"
    echo.
)
if exist "%REPO%\README.md" (
    echo [README.md present]  Showing any lines containing "install", "run", "start", or "build":
    findstr /I /C:"install" /C:"npm " /C:"pnpm " /C:"yarn " /C:"pip " /C:"cargo " /C:"go run" /C:"docker" /C:"make " "%REPO%\README.md"
    echo.
)
echo ============================================================
echo   Done.
echo ============================================================
'@
Set-Content -Path 'scripts\runrepo.bat' -Value $bat -Encoding ASCII