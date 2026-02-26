param(
    [string]$ZipOutput = "",
    [int]$SmokeTestTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
Push-Location $repoRoot
try {
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
    if (Test-Path "build_gui_entry.build") { Remove-Item -Recurse -Force "build_gui_entry.build" }

    $nuitkaArgs = @(
        "--standalone",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        "--assume-yes-for-downloads",
        "--output-dir=dist",
        "--output-filename=ProducerOS",
        "--include-package=producer_os",
        "--include-package=librosa",
        "--include-package=scipy",
        "--include-package=numba",
        "--include-package=llvmlite",
        "--include-module=soundfile",
        "--include-package-data=librosa",
        "--include-package-data=scipy",
        "--include-package-data=numba",
        "--include-package-data=llvmlite",
        "--include-module=sklearn",
        "--include-module=packaging",
        "--include-module=joblib",
        "--include-package=qdarktheme",
        "--include-package-data=qdarktheme",
        "--module-parameter=numba-disable-jit=yes",
        # Avoid recursively compiling large upstream test suites in standalone builds.
        "--nofollow-import-to=numba.tests",
        "--nofollow-import-to=llvmlite.tests",
        "--nofollow-import-to=scipy.tests",
        "--nofollow-import-to=sklearn.tests"
    )
    if (Test-Path "assets\app_icon.ico") {
        $nuitkaArgs += "--windows-icon-from-ico=assets/app_icon.ico"
    }
    $nuitkaArgs += "build_gui_entry.py"

    python -m nuitka @nuitkaArgs

    $distBundle = "dist\build_gui_entry.dist"
    $qwindows = Get-ChildItem -Path $distBundle -Recurse -Filter "qwindows.dll" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $qwindows) {
        throw "Qt platform plugin qwindows.dll not found in standalone build output."
    }
    Write-Host "Found qwindows.dll at $($qwindows.FullName)"

    $exePath = Join-Path $distBundle "ProducerOS.exe"
    if (!(Test-Path $exePath)) {
        throw "Standalone executable missing: $exePath"
    }
    Write-Host "Running packaged GUI smoke test..."
    $env:PRODUCER_OS_SMOKE_TEST = "1"
    $env:PRODUCER_OS_SMOKE_TEST_MS = "250"
    $proc = Start-Process -FilePath $exePath -PassThru
    try {
        Wait-Process -Id $proc.Id -Timeout $SmokeTestTimeoutSeconds
        $proc.Refresh()
        if ($proc.ExitCode -ne 0) {
            throw "Smoke test failed with exit code $($proc.ExitCode)"
        }
    }
    catch {
        try {
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {}
        throw
    }
    finally {
        Remove-Item Env:PRODUCER_OS_SMOKE_TEST -ErrorAction SilentlyContinue
        Remove-Item Env:PRODUCER_OS_SMOKE_TEST_MS -ErrorAction SilentlyContinue
    }
    Write-Host "Smoke test passed."

    $signScript = ".github\scripts\sign_windows_artifacts.ps1"
    if (Test-Path $signScript) {
        & $signScript -Paths @($exePath)
    }

    if (-not [string]::IsNullOrWhiteSpace($ZipOutput)) {
        if (Test-Path $ZipOutput) { Remove-Item $ZipOutput -Force }
        Compress-Archive -Path "$distBundle\*" -DestinationPath $ZipOutput
        Write-Host "Created ZIP: $ZipOutput"
    }
}
finally {
    Pop-Location
}
