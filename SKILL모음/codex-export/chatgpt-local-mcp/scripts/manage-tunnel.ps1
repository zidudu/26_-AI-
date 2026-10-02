param(
    [Parameter(Mandatory = $true)][string]$SettingsPath,
    [ValidateSet('connect', 'status', 'stop')][string]$Mode = 'status',
    [string]$DiagnosticsPath = ''
)
$ErrorActionPreference = 'Stop'
$Phase = 'settings'
$FailureCode = ''
if ($PSVersionTable.PSVersion.Major -lt 7) {
    # tunnel-client emits UTF-8 JSON, including project paths with Korean text.
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    try { [Console]::OutputEncoding = $Utf8NoBom } catch {}
    $OutputEncoding = $Utf8NoBom
}

function Invoke-TunnelJson([string[]]$CliArguments) {
    $SavedPreference = $ErrorActionPreference
    try {
        # Windows PowerShell can promote native stderr to terminating errors.
        $ErrorActionPreference = 'Continue'
        $Captured = & $TunnelExe @CliArguments 2>$null
        $NativeCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $SavedPreference }
    if ($NativeCode -ne 0) { return @{ok=$false; exit_code=$NativeCode; data=$null} }
    try { $Parsed = ($Captured -join "`n") | ConvertFrom-Json }
    catch { return @{ok=$false; exit_code=2; data=$null} }
    return @{ok=$true; exit_code=0; data=$Parsed}
}

function Get-Summary($Data) {
    return [ordered]@{
        alias = $Alias
        process_running = [bool]$Data.process_running
        healthy = [bool]$Data.healthy
        ready = [bool]$Data.ready
        runtime_state = $Data.runtime_state
        ui_url = $Data.ui_url
    }
}

try {
    $Config = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $Allowed = @('alias','tunnel_id','tunnel_client_path','profile_dir','api_key_env','mcp_command','mcp_server_url')
    if (@($Config.PSObject.Properties.Name | Where-Object { $_ -notin $Allowed }).Count -gt 0) {
        throw 'Unexpected settings fields. Store references, never credentials.'
    }
    $Alias = [string]$Config.alias
    $TunnelExe = [string]$Config.tunnel_client_path
    if ($Alias -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') { throw 'Invalid project alias.' }
    if (![IO.Path]::IsPathRooted($TunnelExe) -or !(Test-Path -LiteralPath $TunnelExe -PathType Leaf)) {
        throw 'A verified absolute tunnel-client executable path is required.'
    }
    $ProfileDir = [string]$Config.profile_dir
    if (![IO.Path]::IsPathRooted($ProfileDir)) { throw 'An absolute profile_dir is required.' }
    $RequestedProfile = [IO.Path]::GetFullPath($ProfileDir).TrimEnd('\','/')
    $TunnelId = [string]$Config.tunnel_id
    if ($TunnelId -notmatch '^tunnel_[A-Za-z0-9_]+$' -or $TunnelId -match 'REPLACE') {
        throw 'An actual tunnel ID is required.'
    }

    $Phase = 'read runtime status'
    $Current = Invoke-TunnelJson @('runtimes','status',$Alias,'--json')
    if (!$Current.ok -and $Mode -ne 'connect') { throw 'Cannot read the configured runtime.' }
    if ($Current.ok -and $Mode -ne 'status') {
        $Phase = 'verify alias ownership'
        $CurrentDir = [string]$Current.data.profile_dir
        if ([string]::IsNullOrWhiteSpace($CurrentDir)) { throw 'Existing alias ownership could not be verified.' }
        $CurrentDir = [IO.Path]::GetFullPath($CurrentDir).TrimEnd('\','/')
        if (![string]::Equals($CurrentDir,$RequestedProfile,[StringComparison]::OrdinalIgnoreCase) -or
                [string]$Current.data.tunnel_id -ne $TunnelId) {
            throw 'Alias belongs to a different profile or tunnel. Inspect it or choose a new alias.'
        }
    }
    if ($Mode -eq 'connect') {
        $Phase = 'prepare connection'
        $HasCommand = ![string]::IsNullOrWhiteSpace([string]$Config.mcp_command)
        $HasUrl = ![string]::IsNullOrWhiteSpace([string]$Config.mcp_server_url)
        if ($HasCommand -eq $HasUrl) { throw 'Specify exactly one MCP command or server URL.' }
        $KeyName = if ($Config.api_key_env) { [string]$Config.api_key_env } else { 'OPENAI_API_KEY' }
        if ($KeyName -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid key environment reference.' }
        $KeyValue = [Environment]::GetEnvironmentVariable($KeyName,'Process')
        if ([string]::IsNullOrWhiteSpace($KeyValue)) {
            $KeyValue = [Environment]::GetEnvironmentVariable($KeyName,'User')
            if (![string]::IsNullOrWhiteSpace($KeyValue)) {
                [Environment]::SetEnvironmentVariable($KeyName,$KeyValue,'Process')
            }
        }
        if ([string]::IsNullOrWhiteSpace($KeyValue)) { throw 'Approved key reference is not configured.' }
        $KeyValue = $null
        $ConnectArgs = @('runtimes','connect','--alias',$Alias,'--profile',$Alias,
            '--profile-dir',$ProfileDir,'--tunnel-id',$TunnelId,
            '--runtime-api-key',('env:' + $KeyName),'--json')
        if ($HasCommand) {
            $McpCommand = [string]$Config.mcp_command
            # Windows PowerShell 5.1 strips embedded quotes from native arguments.
            # Escape them so tunnel-client receives the complete quoted command.
            if ($PSVersionTable.PSVersion.Major -lt 7) {
                $McpCommand = $McpCommand.Replace('"','\"')
            }
            $ConnectArgs += @('--mcp-command',$McpCommand)
        }
        else { $ConnectArgs += @('--mcp-server-url',[string]$Config.mcp_server_url) }
        $Phase = 'connect runtime'
        $ActionResult = Invoke-TunnelJson $ConnectArgs
        $FailureCode = [string]$ActionResult.exit_code
        if (!$ActionResult.ok) { throw 'Tunnel connect failed. Inspect official diagnostics without exposing credentials.' }
        $Phase = 'verify connection'
        $Current = Invoke-TunnelJson @('runtimes','status',$Alias,'--json')
    } elseif ($Mode -eq 'stop') {
        $ActionResult = Invoke-TunnelJson @('runtimes','stop',$Alias,'--json')
        if (!$ActionResult.ok) { throw 'Could not stop the configured runtime.' }
        $Current = Invoke-TunnelJson @('runtimes','status',$Alias,'--json')
    }
    if (!$Current.ok) { throw 'Could not verify the managed runtime after the action.' }
    $Summary = Get-Summary $Current.data
    $Summary | ConvertTo-Json
    if ($Mode -eq 'connect' -and !($Summary.process_running -and $Summary.healthy -and $Summary.ready)) { exit 2 }
    if ($Mode -eq 'stop' -and $Summary.process_running) { exit 2 }
    exit 0
} catch {
    # Captured daemon output and key values are deliberately never printed.
    if ($DiagnosticsPath) {
        try { Add-Content -LiteralPath $DiagnosticsPath -Value "$(Get-Date -Format o) helper phase=$Phase native-exit=$FailureCode" -Encoding UTF8 } catch {}
    }
    [Console]::Error.WriteLine("MCP helper failed at $Phase (native exit: $FailureCode). Check JSON settings, alias ownership, installation, network, and key permissions.")
    exit 1
}
