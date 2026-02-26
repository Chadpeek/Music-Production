param(
    [Parameter(Mandatory = $false)]
    [string[]]$Paths = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-SignToolPath {
    if ($env:WINDOWS_SIGNTOOL_PATH -and (Test-Path $env:WINDOWS_SIGNTOOL_PATH)) {
        return $env:WINDOWS_SIGNTOOL_PATH
    }

    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $kitsRoots = @(
        "C:\Program Files (x86)\Windows Kits\10\bin",
        "C:\Program Files\Windows Kits\10\bin"
    )
    foreach ($root in $kitsRoots) {
        if (!(Test-Path $root)) { continue }
        $candidate = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }
    return $null
}

function Write-SigningSummary {
    param([string]$Message)
    Write-Host "[sign] $Message"
}

$enabled = [string]($env:WINDOWS_SIGN_ENABLE ?? "")
if ($enabled -ne "1") {
    Write-SigningSummary "Signing skipped (placeholders mode): WINDOWS_SIGN_ENABLE is not 1."
    exit 0
}

if (!$Paths -or $Paths.Count -eq 0) {
    Write-SigningSummary "Signing skipped: no artifact paths were provided."
    exit 0
}

$certB64 = [string]($env:WINDOWS_SIGN_CERT_B64 ?? "")
$certPassword = [string]($env:WINDOWS_SIGN_CERT_PASSWORD ?? "")
if ([string]::IsNullOrWhiteSpace($certB64) -or [string]::IsNullOrWhiteSpace($certPassword)) {
    Write-SigningSummary "Signing skipped (placeholders mode): certificate secrets are not configured."
    exit 0
}

$signTool = Resolve-SignToolPath
if ($null -eq $signTool) {
    throw "signtool.exe not found. Provide WINDOWS_SIGNTOOL_PATH or install Windows SDK signing tools."
}

$timestampUrl = [string]($env:WINDOWS_SIGN_TIMESTAMP_URL ?? "")
if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    $timestampUrl = "http://timestamp.digicert.com"
}

$tmpDir = Join-Path $env:RUNNER_TEMP "produceros-signing"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$pfxPath = Join-Path $tmpDir "codesign.pfx"

try {
    [IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($certB64))

    foreach ($artifact in $Paths) {
        if ([string]::IsNullOrWhiteSpace($artifact)) { continue }
        if (!(Test-Path $artifact)) {
            throw "Artifact not found for signing: $artifact"
        }
        Write-SigningSummary "Signing $artifact"
        & $signTool sign `
            /fd SHA256 `
            /td SHA256 `
            /f $pfxPath `
            /p $certPassword `
            /tr $timestampUrl `
            $artifact
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed for $artifact (exit code $LASTEXITCODE)"
        }
    }

    Write-SigningSummary "Artifacts signed successfully."
}
finally {
    if (Test-Path $pfxPath) {
        Remove-Item $pfxPath -Force -ErrorAction SilentlyContinue
    }
}
