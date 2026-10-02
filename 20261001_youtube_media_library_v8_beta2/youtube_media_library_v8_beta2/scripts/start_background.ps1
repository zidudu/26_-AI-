$ErrorActionPreference = 'Stop'
if (!(Get-ScheduledTask -TaskName 'YME_V8Beta_Server' -ErrorAction SilentlyContinue)) {
    throw 'Register the task using 03_install_autostart.bat first.'
}
Start-ScheduledTask -TaskName 'YME_V8Beta_Server'
if (Get-ScheduledTask -TaskName 'YME_V8Beta_Tunnel' -ErrorAction SilentlyContinue) {
    Start-ScheduledTask -TaskName 'YME_V8Beta_Tunnel'
}
Write-Host 'Start requested. Open your configured URL or run 04_status.bat.'
