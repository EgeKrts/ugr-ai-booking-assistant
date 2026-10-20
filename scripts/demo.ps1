<#
  Demo helper (Windows PowerShell). Run from anywhere; it switches to the repository folder.

    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 preflight          # check everything, warm up the model
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 prepare            # build the demo database (instant, from the recording)
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 prepare -Live      # same, but with the live model (6-9 minutes)
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 reset              # back to the prepared state (before every rehearsal)
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 dashboard          # start the dashboard on the demo database
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 dashboard -Offline # dashboard with the keyword chat (no model)
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 followup           # follow-up e-mails, live model
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 followup-offline   # follow-up e-mails, replayed (no model)
    powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 status             # what is stored now
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("preflight", "prepare", "reset", "dashboard", "followup", "followup-offline", "status")]
    [string]$Action = "preflight",
    [switch]$Live,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = ".\.venv\Scripts\python.exe"
$base = "data/demo_base.sqlite3"      # prepared once, never modified by the demo
$work = "data/demo.sqlite3"           # what the dashboard shows; reset copies the base over it
$recording = "data/private/extractions"
$model = "qwen3:8b"
$ollama = "http://localhost:11434"

function Ok($message)   { Write-Host "[ OK ] $message" -ForegroundColor Green }
function Warn($message) { Write-Host "[warn] $message" -ForegroundColor Yellow }
function Bad($message)  { Write-Host "[ !! ] $message" -ForegroundColor Red; $script:problems++ }

function Invoke-Cli {
    & $py -m ugr_booking.cli @args
    if ($LASTEXITCODE -ne 0) { throw "ugr_booking.cli failed (exit code $LASTEXITCODE)" }
}

function Preflight {
    $script:problems = 0
    if (Test-Path $py) { Ok "virtual environment" } else { Bad "no .venv: follow the README setup first"; return }
    if (Test-Path "data/private/dataset") { Ok "dataset" } else { Bad "dataset missing in data/private" }

    try {
        $version = Invoke-RestMethod "$ollama/api/version" -TimeoutSec 3
        Ok "Ollama $($version.version) is running"
        $names = (Invoke-RestMethod "$ollama/api/tags" -TimeoutSec 3).models.name
        if ($names -contains $model) { Ok "model $model is installed" } else { Bad "model $model missing: run  ollama pull $model" }
        $body = @{ model = $model; stream = $false; think = $false; keep_alive = "60m"
                   messages = @(@{ role = "user"; content = "ok" }); options = @{ num_predict = 1 } } | ConvertTo-Json -Depth 5
        $seconds = (Measure-Command { Invoke-RestMethod "$ollama/api/chat" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 180 | Out-Null }).TotalSeconds
        Ok ("model loaded and kept warm for 60 minutes ({0:N1} s)" -f $seconds)
    } catch {
        Warn "Ollama is not usable ($($_.Exception.Message)). The demo still works offline: replayed follow-ups and keyword chat."
    }

    if (Test-Path "$recording/EMAIL-001.json") {
        $state = & $py -c "from ugr_booking.recorded import RecordedExtractor as R; print('STALE' if R('$recording').stale else 'CURRENT')"
        if ($state -eq "CURRENT") { Ok "recorded model output is current" } else { Warn "recorded model output is STALE (prompt changed): run record-extractions again" }
    } else { Warn "no recorded model output in $recording (needed only for 'prepare' without -Live)" }

    if (Test-Path $work) { Ok "demo database $work exists" } else { Bad "no demo database: run  demo.ps1 prepare" }
    if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) {
        Bad "port 8501 is busy (an old dashboard is still running): close it with Ctrl+C in its window"
    } else { Ok "port 8501 is free" }

    if ($script:problems -eq 0) { Write-Host "`nReady for the demo." -ForegroundColor Green }
    else { Write-Host "`nFix the items marked !! first." -ForegroundColor Red }
}

function Reset-Demo {
    if (-not (Test-Path $base)) { throw "No prepared database. Run:  demo.ps1 prepare" }
    Copy-Item $base $work -Force
    Ok "demo database reset to the prepared state ($work)"
}

switch ($Action) {
    "preflight" { Preflight }
    "prepare" {
        Remove-Item $base, $work -ErrorAction SilentlyContinue
        if ($Live) {
            Invoke-Cli ingest --db "sqlite:///$base"
        } else {
            if (-not (Test-Path "$recording/EMAIL-001.json")) {
                throw "No recording in $recording. Use  prepare -Live  or run  record-extractions  first."
            }
            Invoke-Cli ingest --extractions $recording --db "sqlite:///$base"
        }
        Reset-Demo
    }
    "reset" { Reset-Demo }
    "dashboard" {
        $env:DATABASE_URL = "sqlite:///$work"
        if ($Offline) { $env:UGR_CHAT_PLANNER = "keywords" }
        & $py -m streamlit run ui/app.py
    }
    "followup" { Invoke-Cli ingest --inbox examples/demo_inbox --db "sqlite:///$work" }
    "followup-offline" { Invoke-Cli ingest --inbox examples/demo_inbox --extractions examples/demo_recording --db "sqlite:///$work" }
    "status" { Invoke-Cli list-bookings --events --db "sqlite:///$work" }
}
