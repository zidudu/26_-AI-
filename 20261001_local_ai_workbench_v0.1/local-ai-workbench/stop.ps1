$ErrorActionPreference = 'Stop'
$data = if ($env:LOCAL_AI_WORKBENCH_DATA) { $env:LOCAL_AI_WORKBENCH_DATA } else { Join-Path $env:LOCALAPPDATA 'LocalAIWorkbench' }
$pidFile = Join-Path $data 'server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host '기록된 실행 서버가 없습니다.'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid" -ErrorAction SilentlyContinue
if ($process -and $process.CommandLine -match 'uvicorn' -and $process.CommandLine -match 'backend.app:app') {
    Stop-Process -Id $serverPid
    Write-Host "서버를 종료했습니다 (PID $serverPid)."
} else {
    Write-Host '기록된 PID에서 이 앱 서버를 찾지 못했습니다.'
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
