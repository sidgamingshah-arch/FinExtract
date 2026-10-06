<# FinEx - stop what Start-FinEx.bat started (the API and the web app, with their child processes). #>
$Root    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = Join-Path $Root '.finex\pids.txt'
if (-not (Test-Path $PidFile)) {
    Write-Host '[FinEx] Nothing to stop: no FinEx processes were started from Start-FinEx.bat.'
} else {
    foreach ($line in Get-Content $PidFile) {
        if ($line -notmatch '^(\d+),(\d+)$') { continue }
        $id = [int]$Matches[1]; $started = [long]$Matches[2]
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        # Only while it is still the process Start-FinEx.bat started.
        if ($p -and $p.StartTime.ToFileTimeUtc() -eq $started) {
            & taskkill.exe /PID $id /T /F 2>$null | Out-Null
        }
    }
    Remove-Item $PidFile -Force
    Write-Host '[FinEx] Stopped.'
}
Start-Sleep -Seconds 2
