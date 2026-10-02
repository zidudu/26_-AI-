#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if ((Read-Host 'Remove ONLY the YME V8 beta startup tasks? Type YES') -cne 'YES') { exit 2 }
if (Test-Path $Python) {
    & $Python (Join-Path $Root 'stop_beta.py')
    if ($LASTEXITCODE -ne 0) { throw 'Graceful shutdown failed. Check the server before removing the task.' }
    & $Python (Join-Path $Root 'tunnel_runner.py') --stop
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel shutdown failed. Check it before removing the task.' }
}
foreach ($Name in @('YME_V8Beta_Server', 'YME_V8Beta_Tunnel')) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
}
Write-Host 'Tasks removed. Your config, library and media files were NOT deleted.'
Write-Host 'Tailscale Serve remains configured. To remove only HTTPS 443, use: tailscale serve --https=443 off'
