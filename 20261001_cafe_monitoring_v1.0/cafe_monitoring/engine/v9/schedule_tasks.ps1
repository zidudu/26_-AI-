param(
    [ValidateSet('list','disable')][string]$Action = 'list',
    [string]$RequestFile
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    if ($Action -eq 'list') {
        $rows = @()
        foreach ($task in @(Get-ScheduledTask)) {
            $candidate = $task.TaskName -like 'NaverCafe_*'
            foreach ($a in @($task.Actions)) {
                if ($a.Arguments -match '[\\/]v(?:5|8|9)[\\/]main(?:_v5)?\.py') { $candidate = $true }
            }
            if (-not $candidate) { continue }
            $actions = @($task.Actions | ForEach-Object {
                @{execute=[string]$_.Execute; arguments=[string]$_.Arguments; working_directory=[string]$_.WorkingDirectory}
            })
            $triggers = @($task.Triggers | ForEach-Object {
                @{type=[string]$_.CimClass.CimClassName; start=[string]$_.StartBoundary; enabled=[bool]$_.Enabled}
            })
            $info = Get-ScheduledTaskInfo -InputObject $task
            $rows += @{
                name=[string]$task.TaskName; path=[string]$task.TaskPath; state=[string]$task.State
                description=[string]$task.Description; actions=$actions; triggers=$triggers
                start_when_available=[bool]$task.Settings.StartWhenAvailable
                next_run=$info.NextRunTime.ToString('o'); last_run=$info.LastRunTime.ToString('o')
                last_result=$info.LastTaskResult
                xml=[string](Export-ScheduledTask -InputObject $task)
            }
        }
        ConvertTo-Json -InputObject @($rows) -Depth 12 -Compress
        exit 0
    }
    if (-not $RequestFile) { throw 'RequestFile is required for a selected task.' }
    $request = Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    # Enumerate and compare literally: Get-ScheduledTask -TaskName accepts wildcards.
    $matches = @(Get-ScheduledTask | Where-Object {
        $_.TaskName -ceq [string]$request.name -and $_.TaskPath -ceq [string]$request.path
    })
    if ($matches.Count -ne 1) { throw 'The exact selected task no longer exists.' }
    $task = $matches[0]
    if ([string]$task.State -eq 'Running') { throw 'The task is running. Wait and inspect it again.' }
    if ([string]$task.State -eq 'Disabled') { throw 'The task is already disabled.' }
    $xml = [string](Export-ScheduledTask -InputObject $task)
    if ($xml -cne [string]$request.expected_xml) { throw 'Task settings changed after inspection. No changes made; inspect again.' }
    $backup = [string]$request.backup_path
    if (Test-Path -LiteralPath $backup) { throw 'Backup already exists. No changes made.' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
    [IO.File]::WriteAllText($backup, $xml, [Text.Encoding]::Unicode)
    if ([IO.File]::ReadAllText($backup, [Text.Encoding]::Unicode) -cne $xml) { throw 'Backup verification failed.' }
    Disable-ScheduledTask -InputObject $task | Out-Null
    $saved = @(Get-ScheduledTask | Where-Object {
        $_.TaskName -ceq [string]$request.name -and $_.TaskPath -ceq [string]$request.path
    })
    if ($saved.Count -ne 1 -or [string]$saved[0].State -ne 'Disabled') {
        throw ('Disable verification failed; inspect the task. XML backup: ' + $backup)
    }
    @{name=[string]$task.TaskName; state='Disabled'; backup_path=$backup} | ConvertTo-Json -Compress
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
