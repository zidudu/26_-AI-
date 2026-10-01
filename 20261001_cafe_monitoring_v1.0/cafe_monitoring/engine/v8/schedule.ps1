param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [ValidateSet('register','unregister')][string]$Action = 'register'
)
$ErrorActionPreference = 'Stop'
try {
    $project = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $cfg = Get-Content -LiteralPath (Join-Path $project 'config_v8.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $name = [string]$cfg.schedule.task_name
    $python = Join-Path $project '.venv_v6\Scripts\python.exe'
    $entry = Join-Path $project 'v8\main.py'
    $arguments = '-u "' + $entry + '" run'
    $existing = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing) {
        if (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $python -or $existing.Actions[0].Arguments -ne $arguments) {
            throw 'A task with the same name belongs to another command. It was not changed.'
        }
    }
    if ($Action -eq 'unregister') {
        if ($existing) { Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false }
        Write-Host ('Schedule removed: ' + $name + '. Existing files were preserved.')
        exit 0
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $entry)) { throw 'V8 Python/entry not found.' }
    if ([TimeZoneInfo]::Local.GetUtcOffset((Get-Date)).TotalHours -ne 9) {
        throw 'Set the Windows time zone to Korea (UTC+09:00) before registering this schedule.'
    }
    if ([string]$cfg.schedule.time -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') { throw 'Invalid schedule time.' }
    $at = [datetime]::ParseExact([string]$cfg.schedule.time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $actionObject = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $project
    if ($cfg.schedule.weekdays_only) {
        $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $at
    } else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $at
    }
    # 로그인 후 놓친 구간도 확인합니다. 아직 처리할 구간이 없으면 run이 즉시 종료합니다.
    $loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $task = New-ScheduledTask -Action $actionObject -Trigger @($trigger, $loginTrigger) -Principal $principal -Settings $settings -Description 'Naver Cafe V8: collection, analysis, validated PPT and Outlook mail.'
    Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $task -Force | Out-Null
    $saved = Get-ScheduledTask -TaskName $name -TaskPath '\'
    if ($saved.Actions[0].Execute -ne $python -or $saved.Actions[0].Arguments -ne $arguments -or $saved.Principal.LogonType -ne 'Interactive') {
        throw 'Registered task verification failed. Check Task Scheduler.'
    }
    Write-Host ('Schedule registered: ' + $name + ' / ' + $cfg.schedule.time + ' KST')
    Write-Host 'Runs in the signed-in Windows session. No immediate run was requested.'
    exit 0
} catch {
    Write-Host ('Schedule error: ' + $_.Exception.Message)
    exit 1
}
