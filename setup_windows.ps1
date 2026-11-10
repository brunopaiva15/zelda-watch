# Installe zelda-watch sur ton PC Windows et le fait tourner en arrière-plan
# (tâche planifiée lancée à chaque ouverture de session, sans fenêtre).
#
# Clic droit sur ce fichier → « Exécuter avec PowerShell », ou dans un terminal ouvert dans ce dossier :
#   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1          → surveille Interdiscount et Fnac
#   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 tout     → surveille toutes les boutiques
#   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 stop     → arrête et retire la tâche
#
# Log en direct : Get-Content .\zelda.log -Wait -Tail 30

param([string]$Mode = "")

$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot
$TaskName = "zelda-watch"
$env:PYTHONUTF8 = "1"
Set-Location $Dir

function Pause-End { Write-Host ""; Read-Host "Appuie sur Entrée pour fermer" | Out-Null }

if ($Mode -eq "stop") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Surveillance arrêtée." -ForegroundColor Yellow
    Pause-End; exit 0
}

# ── 1. Python ──
Write-Host "> Recherche de Python..." -ForegroundColor Cyan
$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 --version *> $null
    if ($LASTEXITCODE -eq 0) { $pyCmd = @("py", "-3") }
}
if (-not $pyCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python --version *> $null
    if ($LASTEXITCODE -eq 0) { $pyCmd = @("python") }
}
if (-not $pyCmd) {
    Write-Host "Python n'est pas installé. Installe-le avec :" -ForegroundColor Red
    Write-Host "    winget install Python.Python.3.12"
    Write-Host "puis ferme ce terminal, rouvre-le et relance ce script."
    Pause-End; exit 1
}

# ── 2. Environnement + Playwright ──
$venvPy  = Join-Path $Dir ".venv\Scripts\python.exe"
$venvPyw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "> Création de l'environnement Python..." -ForegroundColor Cyan
    if ($pyCmd.Count -gt 1) { & $pyCmd[0] $pyCmd[1] -m venv .venv } else { & $pyCmd[0] -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw "Échec de la création de .venv" }
}
Write-Host "> Installation de Playwright et des navigateurs (quelques minutes la 1re fois)..." -ForegroundColor Cyan
& $venvPy -m pip install -q --upgrade pip playwright
if ($LASTEXITCODE -ne 0) { throw "Échec de pip install playwright" }
& $venvPy -m playwright install chromium firefox
if ($LASTEXITCODE -ne 0) { throw "Échec de playwright install" }

# ── 3. Fichier .env ──
$envFile = Join-Path $Dir ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $Dir ".env.example") $envFile }

$lines = [System.IO.File]::ReadAllLines($envFile) | Where-Object { $_ -notmatch '^ONLY_SITES=' }
if ($Mode -ne "tout") { $lines += "ONLY_SITES=Interdiscount,Fnac" }
[System.IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))

$configured = Select-String -Path $envFile -Pattern '^(NTFY_TOPIC|TELEGRAM_BOT_TOKEN)=\S+' -Quiet
if (-not $configured) {
    Write-Host ""
    Write-Host "Aucune notification configurée." -ForegroundColor Yellow
    Write-Host "Le Bloc-notes s'ouvre : remplis NTFY_TOPIC ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID,"
    Write-Host "enregistre, ferme-le, puis relance ce script."
    Start-Process notepad.exe $envFile
    Pause-End; exit 1
}

# ── 4. Passage de test depuis ta connexion ──
Write-Host ""
Write-Host "> Passage de test depuis ta connexion (1 à 3 min)..." -ForegroundColor Cyan
& $venvPy zelda_watch.py --test

# ── 5. Tâche planifiée en arrière-plan ──
Write-Host ""
Write-Host "> Création de la tâche planifiée '$TaskName'..." -ForegroundColor Cyan
$user = "$env:USERDOMAIN\$env:USERNAME"
$logFile = Join-Path $Dir "zelda.log"
$action = New-ScheduledTaskAction -Execute $venvPyw `
    -Argument "`"$Dir\zelda_watch.py`" --log `"$logFile`"" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$what = if ($Mode -eq "tout") { "toutes les boutiques" } else { "Interdiscount + Fnac" }
Write-Host ""
Write-Host "Surveillance lancée en arrière-plan ($what)." -ForegroundColor Green
Write-Host "  Log en direct : Get-Content `"$logFile`" -Wait -Tail 30"
Write-Host "  Arrêter       : powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 stop"
Write-Host "  Le PC ne se met pas en veille tant que la surveillance tourne."
Pause-End
