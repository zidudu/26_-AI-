$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$data = if ($env:LOCAL_AI_WORKBENCH_DATA) { $env:LOCAL_AI_WORKBENCH_DATA } else { Join-Path $env:LOCALAPPDATA 'LocalAIWorkbench' }
$python = Join-Path $project '.venv\Scripts\python.exe'
$frontend = Join-Path $project 'frontend'
$url = 'http://127.0.0.1:8768'

New-Item -ItemType Directory -Force -Path $data | Out-Null
try {
    $existing = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
    if ($existing.compare_models) {
        Write-Host "이미 실행 중입니다: $url"
        Start-Process $url
        exit 0
    }
} catch { }

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Python 가상 환경을 준비합니다...'
    python -m venv (Join-Path $project '.venv')
}
& $python -m pip install -q --disable-pip-version-check -r (Join-Path $project 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python 패키지 설치에 실패했습니다.' }

Push-Location $frontend
try {
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw '프런트엔드 패키지 설치에 실패했습니다.' }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '프런트엔드 빌드에 실패했습니다.' }
} finally { Pop-Location }

$process = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8768') -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'server.out.log') -RedirectStandardError (Join-Path $data 'server.err.log') -PassThru
Set-Content -LiteralPath (Join-Path $data 'server.pid') -Value $process.Id
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
        Write-Host "로컬 AI 작업대 실행: $url"
        if (-not $health.ollama_connected) { Write-Warning $health.error }
        Start-Process $url
        exit 0
    } catch {
        if ($process.HasExited) { break }
    }
}
throw "서버 시작에 실패했습니다. 로그: $(Join-Path $data 'server.err.log')"
