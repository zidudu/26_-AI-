#Requires -RunAsAdministrator
param([switch]$AtLogonOnly)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (!(Test-Path $Python) -or !(Test-Path (Join-Path $Root 'config\owner.json'))) {
    throw 'Run 01_setup_beta.bat first.'
}
if (Test-Path (Join-Path $Root 'config\runtime.json')) {
    throw 'Stop the running beta server with stop_v8_beta.bat before registering startup.'
}
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "This registers a private media server task for $User."
Write-Host 'It does not change firewall rules, port forwarding, sleep, or Windows login settings.'
Write-Host 'After registration, do not move this project folder.'
if ((Read-Host 'Type YES to register the startup task') -cne 'YES') { exit 2 }
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($AtLogonOnly) {
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    Write-Host 'Logon-only mode: server starts AFTER Windows sign-in, NOT before sign-in.'
} else {
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    Write-Host 'Use your WINDOWS account password, not Windows Hello PIN and not the media-library password.'
    $Credential = Get-Credential -UserName $User -Message 'Windows startup task credentials (stored by Task Scheduler)'
    if (!$Credential) { exit 2 }
    $ExpectedSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $GivenSid = ([System.Security.Principal.NTAccount]::new($Credential.UserName)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($ExpectedSid -ne $GivenSid) { throw 'Use the same Windows account that created the beta config folder.' }
    $Password = $Credential.GetNetworkCredential().Password
}
function Register-BetaTask([string]$Name, [string]$Script) {
    $Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + (Join-Path $Root $Script) + '" --service --no-browser') -WorkingDirectory $Root
    $Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings `
        -Description 'YouTube Media Library V8 beta. Loopback only; authenticated remote proxy.'
    if ($AtLogonOnly) {
        $Task.Principal = $Principal
        Register-ScheduledTask -TaskName $Name -InputObject $Task -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $Name -InputObject $Task -User $Credential.UserName -Password $Password -Force | Out-Null
    }
}
try {
    Register-BetaTask 'YME_V8Beta_Server' 'launcher.py'
    if ((Test-Path (Join-Path $Root 'config\tunnel.token'))) {
        if ((Read-Host 'Also register OPTIONAL Cloudflare management tunnel? Type YES') -ceq 'YES') {
            Register-BetaTask 'YME_V8Beta_Tunnel' 'tunnel_runner.py'
        }
    }
} finally { $Password = $null; $Credential = $null }
Write-Host 'Registered. The task restarts after failure up to 3 times; logs are in logs\server.log.'
$Ts = Get-Command tailscale.exe -ErrorAction SilentlyContinue
$TsPath = if ($Ts) { $Ts.Source } else { $null }
if (!$TsPath) {
    $Candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path $Candidate) { $TsPath = $Candidate }
}
if ($TsPath) {
    if ((Read-Host 'Enable Tailscale unattended connection before Windows sign-in? Type YES') -ceq 'YES') {
        & $TsPath up --unattended=true
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Tailscale setting failed. Use its tray menu Preferences > Run unattended. Existing custom network settings were not reset.'
        }
    }
}
if ((Read-Host 'Start the beta server task now? Type YES') -ceq 'YES') {
    Start-ScheduledTask -TaskName 'YME_V8Beta_Server'
    if (Get-ScheduledTask -TaskName 'YME_V8Beta_Tunnel' -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName 'YME_V8Beta_Tunnel'
    }
    Write-Host 'Start requested. Check 04_status.bat and logs\server.log; this is not proof of remote connectivity.'
}
