# run_bot.ps1 - start the bot on Windows with the settings from .env (in the repository root, the parent of scripts/).
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_bot.ps1    (or right-click > Run with PowerShell)
#
# .env has KEY=VALUE lines (see .env.example); only DISCORD_TOKEN and SUPERPERM_* keys are used, values without
# quotes. The values are put into the environment of this process only and are never printed. If DISCORD_TOKEN is
# empty, the script asks for it without showing it. SUPERPERM_PYTHON chooses the Python (default: python on PATH).
# SUPERPERM_ENV_FILE (set in the environment, not in .env) reads the settings from another file.
# Stop the bot with Ctrl+C.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)     # the repository root
$envFile = if ($env:SUPERPERM_ENV_FILE) { $env:SUPERPERM_ENV_FILE } else { Join-Path $here ".env" }
if (-not (Test-Path $envFile)) {
    Write-Host "No settings file $envFile. Copy .env.example to .env and fill it in."
    exit 1
}
$set = @()
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    if ($i -lt 1) { Write-Host "ignored a line of .env without KEY=VALUE"; continue }
    $key = $t.Substring(0, $i).Trim()
    $val = $t.Substring($i + 1).Trim()
    if ($key -notmatch '^(DISCORD_TOKEN|SUPERPERM_[A-Z_]+)$') { Write-Host "ignored unknown key $key"; continue }
    if ($val -eq "") { continue }
    [Environment]::SetEnvironmentVariable($key, $val, "Process")
    $set += $key
}
if (-not $env:DISCORD_TOKEN -and $env:SUPERPERM_NONINTERACTIVE) { Write-Host "DISCORD_TOKEN is empty in $envFile"; exit 1 }
if (-not $env:DISCORD_TOKEN) {
    $sec = Read-Host "Discord token (not shown)" -AsSecureString
    $env:DISCORD_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    $set += "DISCORD_TOKEN (typed)"
}
Write-Host ("settings from .env: " + (($set | Sort-Object) -join ", "))
$py = if ($env:SUPERPERM_PYTHON) { $env:SUPERPERM_PYTHON } else { "python" }
Set-Location $here
$ErrorActionPreference = "Continue"     # a native program writing to stderr must not stop the script
& $py -I -c "import discord" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "discord.py is missing for $py : run  $py -m pip install -r requirements.txt"; exit 1 }
if (-not (Test-Path (Join-Path $here "bin\BUILD.json"))) { & $py -I checker\build.py }
& $py -I bot\discord_bot.py
