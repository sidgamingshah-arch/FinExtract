<#
  FinEx - one-click start on Windows. Run by Start-FinEx.bat (double-click it).

  First run: creates the Python environment (backend\.venv), installs the backend and the web
  app, and asks once for the LLM gateway key, which it saves in backend\.env (never committed).
  Every run: starts the API (http://127.0.0.1:8000) and the web app (http://localhost:5173) in
  two minimised windows and opens the browser. Stop-FinEx.bat closes both.

  Needs Python 3.11+ (python.org, "py" launcher) and Node.js 18+ (nodejs.org). Behind a corporate
  proxy, pip and npm use the proxy settings your machine already has.

  Options (pass after Start-FinEx.bat):
    -WithOcr     also install Docling and fetch its models, for scanned pages (large download)
    -Reinstall   reinstall the backend and web-app packages
    -NoBrowser   do not open the browser
#>
param([switch]$WithOcr, [switch]$Reinstall, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$Root     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Venv     = Join-Path $Backend '.venv'
$VPy      = Join-Path $Venv 'Scripts\python.exe'
$State    = Join-Path $Root '.finex'
$PidFile  = Join-Path $State 'pids.txt'
$ApiUrl   = 'http://127.0.0.1:8000'
$WebUrl   = 'http://localhost:5173'

function Say($m)  { Write-Host "[FinEx] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[FinEx] $m" -ForegroundColor Yellow }
function Fail($m) {
    Write-Host "[FinEx] $m" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

# A local address is asked directly, never through the machine's proxy.
function Test-Up($url) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Proxy = $null
        $req.Timeout = 2000
        $res = $req.GetResponse(); $res.Close(); return $true
    } catch { return $false }
}

function Wait-Up($url, $what, $seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Up $url) { return $true }
        Start-Sleep -Seconds 1
    }
    Warn "$what did not answer at $url within $seconds s - see its window for the error."
    return $false
}

New-Item -ItemType Directory -Force -Path $State | Out-Null

# --- 1. Python 3.11+ ---
if (-not (Test-Path $VPy)) {
    $py = $null
    foreach ($cand in @(@('py', '-3.12'), @('py', '-3.11'), @('py', '-3'), @('python'))) {
        $exe = $cand[0]; $rest = @($cand | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    }
    if (-not $py) {
        Fail 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run Start-FinEx.bat again.'
    }
    Say "Creating the Python environment in backend\.venv ..."
    $exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python environment.' }
    $Reinstall = $true
}

# --- 2. Backend packages ---
$pyMarker = Join-Path $Venv '.finex-installed'
$pyStamp  = (Get-FileHash (Join-Path $Backend 'pyproject.toml')).Hash + $(if ($WithOcr) { '+ocr' } else { '' })
if ($Reinstall -or -not (Test-Path $pyMarker) -or ((Get-Content $pyMarker -Raw).Trim() -ne $pyStamp)) {
    Say 'Installing the backend (first run takes a few minutes) ...'
    Push-Location $Backend
    try {
        & $VPy -m pip install --upgrade pip --quiet
        $extras = if ($WithOcr) { '.[pdf,cjk,docling]' } else { '.[pdf,cjk]' }
        & $VPy -m pip install -e $extras
        if ($LASTEXITCODE -ne 0) { Fail 'Installing the backend failed - see the messages above.' }
        if ($WithOcr) {
            Say 'Fetching the Docling models for offline OCR ...'
            & $VPy scripts\fetch_docling_models.py
            if ($LASTEXITCODE -ne 0) { Warn 'The Docling models were not fetched; scanned pages will be skipped.' }
        }
    } finally { Pop-Location }
    Set-Content -Path $pyMarker -Value $pyStamp
}

# --- 3. Web app packages ---
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail 'Node.js was not found. Install the LTS version from https://nodejs.org/, then run Start-FinEx.bat again.'
}
$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 18) { Fail "Node.js 18 or newer is needed (found $(& node --version))." }
$jsMarker = Join-Path $Frontend 'node_modules\.finex-installed'
$jsStamp  = (Get-FileHash (Join-Path $Frontend 'package.json')).Hash
if ($Reinstall -or -not (Test-Path $jsMarker) -or ((Get-Content $jsMarker -Raw).Trim() -ne $jsStamp)) {
    Say 'Installing the web app ...'
    Push-Location $Frontend
    try {
        & cmd.exe /c 'npm install --no-audit --no-fund'
        if ($LASTEXITCODE -ne 0) { Fail 'Installing the web app failed - see the messages above.' }
    } finally { Pop-Location }
    Set-Content -Path $jsMarker -Value $jsStamp
}

# --- 4. The LLM key ---
# The variable's NAME comes from backend\config.toml [llm] api_key_env; the key itself lives only
# in the environment or in backend\.env, which is gitignored.
$toml = Get-Content (Join-Path $Backend 'config.toml') -Raw
$keyName = 'LLM_GATEWAY_TOKEN'
$m = [regex]::Match($toml, '(?ms)^\[llm\].*?^api_key_env\s*=\s*"([^"]+)"')
if ($m.Success) { $keyName = $m.Groups[1].Value }
$envFile = Join-Path $Backend '.env'
$inFile = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern "^\s*$keyName\s*=\s*\S" -Quiet)
$inEnv = [bool][Environment]::GetEnvironmentVariable($keyName)
if (-not $inFile -and -not $inEnv) {
    Warn "No LLM key found ($keyName)."
    $secure = Read-Host "Paste the LLM gateway key and press Enter (or just Enter to run without the model)" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($plain) {
        # On a line of its own even when the file does not end in a newline.
        if ((Test-Path $envFile) -and ((Get-Content $envFile -Raw) -notmatch "(\r?\n)$") -and
            ((Get-Item $envFile).Length -gt 0)) { Add-Content -Path $envFile -Value '' }
        Add-Content -Path $envFile -Value "$keyName=$plain"
        Say "Saved in backend\.env (not committed). To change it later, edit that file."
    } else {
        Warn 'Continuing without the model: extraction runs on the deterministic route only.'
    }
}

# --- 5. Start ---
$pids = @()
if (Test-Up "$ApiUrl/health") {
    Say "The API is already running at $ApiUrl."
} else {
    Say 'Starting the API ...'
    $api = Start-Process -FilePath $VPy -WorkingDirectory $Backend -WindowStyle Minimized -PassThru `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'
    $pids += $api.Id
}
if (Test-Up $WebUrl) {
    Say "The web app is already running at $WebUrl."
} else {
    Say 'Starting the web app ...'
    $web = Start-Process -FilePath 'cmd.exe' -WorkingDirectory $Frontend -WindowStyle Minimized -PassThru `
        -ArgumentList '/c', 'title FinEx web app && npm run dev'
    $pids += $web.Id
}
# Each process is recorded with its start time, so Stop-FinEx.bat stops it only while it is still
# the process this script started (a process id can be reused by Windows once it has exited).
if ($pids.Count) {
    foreach ($id in $pids) {
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($p) { Add-Content -Path $PidFile -Value "$id,$($p.StartTime.ToFileTimeUtc())" }
    }
}

$apiOk = Wait-Up "$ApiUrl/health" 'The API' 180
$webOk = Wait-Up $WebUrl 'The web app' 120
if ($apiOk -and $webOk) {
    Say "FinEx is running: $WebUrl   (API: $ApiUrl/docs)"
    if (-not $NoBrowser) { Start-Process $WebUrl }
    Say 'Close it with Stop-FinEx.bat. This window can be closed.'
    Start-Sleep -Seconds 4
} else {
    Read-Host 'Press Enter to close'
}
