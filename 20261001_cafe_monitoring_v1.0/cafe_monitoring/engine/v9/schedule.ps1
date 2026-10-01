param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [ValidateSet('register','unregister')][string]$Action = 'register'
)
$ErrorActionPreference = 'Stop'
$previousXml = $null
$changed = $false
try {
    $project = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $cfg = Get-Content -LiteralPath (Join-Path $project 'config_v9.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $prior = Get-Content -LiteralPath (Join-Path $project 'config_v8.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $name = [string]$cfg.schedule.task_name
    if ($name -notmatch '^[A-Za-z0-9_-]{1,80}$' -or $name -ne [string]$prior.schedule.task_name) {
        throw 'V9 must use the existing V8 task name to prevent duplicate schedules.'
    }
    $python = Join-Path $project '.venv_v6\Scripts\python.exe'
    $entry = Join-Path $project 'v9\main.py'
    $arguments = '-u "' + $entry + '" run'
    $oldArguments = '-u "' + (Join-Path $project 'v8\main.py') + '" run'
    $existing = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing) {
        if (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $python -or
            $existing.Actions[0].Arguments -notin @($arguments,$oldArguments)) {
            throw 'A task with the same name belongs to another command. It was preserved.'
        }
        if ($existing.State -eq 'Running') { throw 'Wait for the running task to finish before changing its schedule.' }
        $previousXml = Export-ScheduledTask -TaskName $name -TaskPath '\'
    }
    if ($Action -eq 'unregister') {
        if ($existing -and $existing.Actions[0].Arguments -ne $arguments) {
            throw 'This task still belongs to V8. It was preserved.'
        }
        if ($existing) { Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false }
        Write-Host ('V9 schedule removed: ' + $name + '. Files and V8 settings were preserved.')
        exit 0
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $entry)) { throw 'V9 Python/entry not found.' }
    if ([TimeZoneInfo]::Local.GetUtcOffset((Get-Date)).TotalHours -ne 9) { throw 'Set Windows time zone to Korea (UTC+09:00).' }
    if ([string]$cfg.schedule.time -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') { throw 'Invalid time.' }
    $at = [datetime]::ParseExact([string]$cfg.schedule.time,'HH:mm',[Globalization.CultureInfo]::InvariantCulture)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $taskAction = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $project
    if ($cfg.schedule.weekdays_only) {
        $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $at
    } else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $at
    }
    $loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $task = New-ScheduledTask -Action $taskAction -Trigger @($trigger,$loginTrigger) -Principal $principal -Settings $settings -Description 'Naver Cafe V9: nine cafes, individual cursors, validated combined PPT and one Outlook mail.'
    if ($previousXml) {
        $backupFolder = Join-Path $project 'output_v9\schedule_backups'
        New-Item -ItemType Directory -Path $backupFolder -Force | Out-Null
        $backupPath = Join-Path $backupFolder ('before_v9_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fffffff') + '.xml')
        [System.IO.File]::WriteAllText($backupPath,$previousXml,[System.Text.Encoding]::Unicode)
    }
    $changed = $true
    Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $task -Force | Out-Null
    $saved = Get-ScheduledTask -TaskName $name -TaskPath '\'
    if ($saved.Actions[0].Execute -ne $python -or $saved.Actions[0].Arguments -ne $arguments -or $saved.Principal.LogonType -ne 'Interactive') {
        throw 'V9 task verification failed.'
    }
    Write-Host ('Schedule switched to V9: ' + $name + ' / ' + $cfg.schedule.time + ' KST')
    Write-Host 'The existing task name is reused. No immediate run was requested.'
    exit 0
} catch {
    if ($changed) {
        try {
            if ($previousXml) { Register-ScheduledTask -TaskName $name -TaskPath '\' -Xml $previousXml -Force | Out-Null }
            else { Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue }
            Write-Host 'Previous schedule restored after registration failure.'
        } catch { Write-Host ('Schedule rollback failed. Check Task Scheduler: ' + $_.Exception.Message) }
    }
    Write-Host ('Schedule error: ' + $_.Exception.Message)
    exit 1
}
