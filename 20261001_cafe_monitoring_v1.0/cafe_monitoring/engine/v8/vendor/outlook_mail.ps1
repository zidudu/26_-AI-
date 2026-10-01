param(
    [Parameter(Mandatory=$true)]
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"

function Normalize-List {
    param($Value)

    if ($null -eq $Value) {
        return @()
    }

    if ($Value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($Value)) {
            return @()
        }
        return @($Value)
    }

    return @($Value | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
}

try {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "Config file not found: $ConfigPath"
    }

    $configText = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    $config = $configText | ConvertFrom-Json

    $to = Normalize-List $config.to
    $cc = Normalize-List $config.cc
    $bcc = Normalize-List $config.bcc
    $attachments = Normalize-List $config.attachments

    if ($to.Count -lt 1) {
        throw "At least one TO recipient is required."
    }

    foreach ($file in $attachments) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Attachment not found: $file"
        }
    }

    $outlook = New-Object -ComObject Outlook.Application
    $mail = $outlook.CreateItem(0)

    $mail.To = ($to -join ";")

    if ($cc.Count -gt 0) {
        $mail.CC = ($cc -join ";")
    }

    if ($bcc.Count -gt 0) {
        $mail.BCC = ($bcc -join ";")
    }

    $mail.Subject = [string]$config.subject

    if ($config.html_body -and -not [string]::IsNullOrWhiteSpace([string]$config.html_body)) {
        $mail.HTMLBody = [string]$config.html_body
    }
    else {
        $mail.Body = [string]$config.body
    }

    if (-not $mail.Recipients.ResolveAll()) {
        throw "One or more recipients could not be resolved by Outlook."
    }

    foreach ($file in $attachments) {
        $fullPath = (Resolve-Path -LiteralPath $file).Path
        [void]$mail.Attachments.Add($fullPath)
    }

    $displayOnly = $false
    if ($null -ne $config.display_only) {
        $displayOnly = [bool]$config.display_only
    }

    if ($displayOnly) {
        $mail.Display()
        $action = "displayed"
    }
    else {
        $mail.Send()
        $action = "sent"
    }

    $result = [PSCustomObject]@{
        ok = $true
        action = $action
        to = $to
        cc = $cc
        bcc = $bcc
        attachment_count = $attachments.Count
        subject = [string]$config.subject
        timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }

    $result | ConvertTo-Json -Depth 5 -Compress
    exit 0
}
catch {
    $result = [PSCustomObject]@{
        ok = $false
        error = $_.Exception.Message
        timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }

    $result | ConvertTo-Json -Depth 5 -Compress
    exit 1
}
