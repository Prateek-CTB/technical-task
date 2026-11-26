<#
.SYNOPSIS
  One command: install everything, load the real review data, analyse it, build the dashboard,
  start both servers, open the browser and print a short tour.

.EXAMPLE
  .\setup.cmd                 # from the repo root (double-click works too)
  .\setup.cmd -SkipInstall    # already installed: just start everything
  .\setup.cmd -NoBrowser      # do not open the browser

  Stop everything with Ctrl+C in this window.
#>
param(
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [int]$ApiPort = 8000,
    [int]$WebPort = 3000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Cli = Join-Path $Venv "Scripts\reviewhub.exe"
$Frontend = Join-Path $Root "frontend"
$Logs = Join-Path $Root "data\logs"

function Step([string]$n, [string]$text) { Write-Host ""; Write-Host "[$n] $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Fail([string]$text) { Write-Host ""; Write-Host "    ERROR  $text" -ForegroundColor Red; exit 1 }
function Run([string]$exe, [string[]]$argv, [string]$what) {
    # Native tools write progress to stderr; Windows PowerShell 5.1 would turn that into error
    # records. Success or failure is decided by the exit code alone.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @argv 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE). See the output above." }
}
# Human-readable, quiet backend output during setup.
$env:REVIEWHUB_LOG_LEVEL = "WARNING"
$env:REVIEWHUB_LOG_FORMAT = "text"
function PortBusy([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

Write-Host "Azzurro Review Intelligence - setup and launch" -ForegroundColor White
Write-Host "Repository: $Root"

# ------------------------------------------------------------------ 1. prerequisites
Step "1/6" "Checking prerequisites"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail "Python 3.11+ is required: https://www.python.org/downloads/ (tick 'Add python.exe to PATH')" }
$pyver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyver -lt [version]"3.11") { Fail "Python 3.11+ is required (found $pyver)." }
Ok "Python $pyver"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Fail "Node.js 20+ is required: https://nodejs.org/" }
$nodever = (& node --version).TrimStart("v")
if ([version]$nodever -lt [version]"20.0") { Fail "Node.js 20+ is required (found $nodever)." }
Ok "Node.js $nodever"
foreach ($p in @($ApiPort, $WebPort)) {
    if (PortBusy $p) { Fail "Port $p is already in use. Close the program using it, or run with -ApiPort / -WebPort." }
}
Ok "Ports $ApiPort and $WebPort are free"

if (-not $SkipInstall) {
    # -------------------------------------------------------------- 2. backend
    Step "2/6" "Installing the backend (Python virtual environment)"
    if (-not (Test-Path $Py)) { Run "python" @("-m", "venv", $Venv) "Creating the virtual environment" }
    Run $Py @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "Upgrading pip"
    Run $Py @("-m", "pip", "install", "--quiet", "-e", (Join-Path $Root "backend")) "Installing backend packages"
    Ok "Backend installed"

    # -------------------------------------------------------------- 3. data
    Step "3/6" "Preparing the database and the 973 real reviews"
    Run $Cli @("db", "upgrade") "Database migration"
    Run $Cli @("sample", "load") "Loading the sample reviews"
    Run $Cli @("ai", "analyze", "--provider", "builtin") "Analysing reviews (offline)"
    Ok "Database ready: data\reviewhub.db"

    # -------------------------------------------------------------- 4. frontend
    Step "4/6" "Installing and building the dashboard (Next.js)"
    $envLocal = Join-Path $Frontend ".env.local"
    if (-not (Test-Path $envLocal)) {
        Set-Content -Path $envLocal -Value "BACKEND_URL=http://127.0.0.1:$ApiPort" -Encoding utf8
    }
    Push-Location $Frontend
    try {
        Run "npm.cmd" @("ci", "--no-audit", "--no-fund", "--loglevel=error") "npm ci"
        Run "npm.cmd" @("run", "build") "Building the dashboard"
    } finally { Pop-Location }
    Ok "Dashboard built"
} else {
    Step "2-4/6" "Skipping installation (-SkipInstall)"
    if (-not (Test-Path $Cli)) { Fail "Not installed yet. Run without -SkipInstall first." }
}

# ------------------------------------------------------------------ 5. start
Step "5/6" "Starting the API and the dashboard"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$env:BACKEND_URL = "http://127.0.0.1:$ApiPort"
$api = Start-Process -FilePath $Cli -ArgumentList @("serve", "--port", "$ApiPort") -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Logs "api.out.log") -RedirectStandardError (Join-Path $Logs "api.err.log")
$web = Start-Process -FilePath "npm.cmd" -ArgumentList @("start", "--", "-p", "$WebPort") -WorkingDirectory $Frontend -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Logs "web.out.log") -RedirectStandardError (Join-Path $Logs "web.err.log")

function WaitFor([string]$url, [string]$what) {
    for ($i = 0; $i -lt 60; $i++) {
        try { $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2; if ($r.StatusCode -eq 200) { return } } catch { }
        Start-Sleep -Seconds 1
    }
    Fail "$what did not start in 60 s. Logs: $Logs"
}
WaitFor "http://127.0.0.1:$ApiPort/api/v1/health" "The API"
Ok "API       http://127.0.0.1:$ApiPort   (interactive docs: /docs)"
WaitFor "http://127.0.0.1:$WebPort/" "The dashboard"
Ok "Dashboard http://localhost:$WebPort"

# ------------------------------------------------------------------ 6. tour
Step "6/6" "Quick tour (full version: docs\TOUR.md)"
$tour = @"
    1. Executive Overview    /            this week's rating vs last week, trends, top issues to act on
    2. Properties            /properties  the four hotels side by side; click one for its own page
    3. Review Intelligence   /insights    the 8 topics, '% of negative reviews mentioning X', guest quotes
    4. Review Explorer       /reviews     every review: search, filter by property/date/rating/topic
    5. Data & Collection     /data        where data comes from, BLOCKED attempts, import a CSV/JSON
"@
Write-Host $tour
if (-not $NoBrowser) { Start-Process "http://localhost:$WebPort" }

Write-Host ""
Write-Host "Running. Press Ctrl+C to stop both servers." -ForegroundColor Yellow
try {
    while ($true) {
        Start-Sleep -Seconds 2
        if ($api.HasExited) { Write-Host "The API stopped. Log: $Logs\api.err.log" -ForegroundColor Red; break }
        if ($web.HasExited) { Write-Host "The dashboard stopped. Log: $Logs\web.err.log" -ForegroundColor Red; break }
    }
} finally {
    foreach ($p in @($web, $api)) {
        if ($p -and -not $p.HasExited) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null }
    }
    Write-Host "Stopped."
}
