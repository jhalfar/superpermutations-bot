# bot_ctl.ps1 - run the bot in the background on Windows: start, stop, restart, status, log.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bot_ctl.ps1 start|stop|restart|status|log [LINES]
#
# start runs scripts/run_bot.ps1 in a hidden window (settings from .env in the repository root, which this script never
# reads itself), with the output in logs/bot.out and logs/bot.err and the process id in logs/bot.pid. stop ends that process and all its
# children (a running check included). The token must be in .env: a background start cannot ask for it.
param([Parameter(Position = 0)][string]$cmd = "status", [Parameter(Position = 1)][int]$lines = 30)
$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$here = Split-Path -Parent $scripts     # the repository root: .env, logs/
$logs = Join-Path $here "logs"
$pidFile = Join-Path $logs "bot.pid"
New-Item -ItemType Directory -Force $logs | Out-Null

function Get-BotProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $id = [int](Get-Content $pidFile -Raw)
    return Get-Process -Id $id -ErrorAction SilentlyContinue
}

function Stop-Bot {
    $p = Get-BotProcess
    if ($p) {
        & taskkill.exe /PID $p.Id /T /F | Out-Null
        Write-Host "stopped (pid $($p.Id) and its children)"
    } else { Write-Host "not running" }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Start-Bot {
    if (Get-BotProcess) { Write-Host "already running (pid $((Get-BotProcess).Id))"; return }
    $envFile = if ($env:SUPERPERM_ENV_FILE) { $env:SUPERPERM_ENV_FILE } else { Join-Path $here ".env" }
    if (-not (Test-Path $envFile)) { Write-Host "no settings file ${envFile}: copy .env.example to .env and fill it in"; exit 1 }
    $env:SUPERPERM_NONINTERACTIVE = "1"
    $out = Join-Path $logs "bot.out"; $err = Join-Path $logs "bot.err"
    foreach ($f in $out, $err) { if (Test-Path $f) { Move-Item -Force $f "$f.prev" } }
    $p = Start-Process powershell.exe -WindowStyle Hidden -PassThru -WorkingDirectory $here `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scripts "run_bot.ps1"))
    Set-Content -Path $pidFile -Value $p.Id
    Start-Sleep -Seconds 8
    if (Get-BotProcess) { Write-Host "started (pid $($p.Id)); log: logs\bot.out, logs\bot.err" }
    else { Write-Host "the bot ended at once; last lines:"; Get-Content $out, $err -Tail 15 -ErrorAction SilentlyContinue }
}

switch ($cmd) {
    "start"   { Start-Bot }
    "stop"    { Stop-Bot }
    "restart" { Stop-Bot; Start-Sleep -Seconds 2; Start-Bot }
    "status"  { $p = Get-BotProcess; if ($p) { Write-Host "running (pid $($p.Id), since $($p.StartTime))" } else { Write-Host "not running" } }
    "log"     { Get-Content (Join-Path $logs "bot.out"), (Join-Path $logs "bot.err") -Tail $lines -ErrorAction SilentlyContinue }
    default   { Write-Host "usage: scripts\bot_ctl.ps1 start|stop|restart|status|log [LINES]"; exit 2 }
}
