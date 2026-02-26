param(
    [string]$ZipOutput = "",
    [int]$SmokeTestTimeoutSeconds = 20,
    [int]$TinyAnalyzeSmokeTimeoutSeconds = 45,
    [ValidateSet("dev", "release")]
    [string]$BuildProfile = "release",
    [string]$RepoRoot = "",
    [string]$BuildInfoOutput = "",
    [string]$TimingOutput = "",
    [int]$NuitkaJobs = 0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
} else {
    (Resolve-Path $RepoRoot).Path
}
Push-Location $repoRoot
try {
    # Fast-fail for local runs or misconfigured CI shells before Nuitka spends time analyzing.
    $clCommand = Get-Command cl.exe -ErrorAction SilentlyContinue
    if ($null -eq $clCommand) {
        $hint = @(
            "MSVC C/C++ compiler (cl.exe) was not found on PATH.",
            "Open a Visual Studio Developer Command Prompt/PowerShell, or run VsDevCmd.bat before invoking this script.",
            "Required Visual Studio component: Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
        ) -join " "
        throw $hint
    }
    Write-Host "MSVC compiler found: $($clCommand.Source)"

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
        "--include-module=packaging",
        "--include-package=qdarktheme",
        "--include-package-data=qdarktheme",
        "--module-parameter=numba-disable-jit=yes",
        # Avoid recursively compiling large upstream test suites in standalone builds.
        "--nofollow-import-to=numba.tests",
        "--nofollow-import-to=llvmlite.tests",
        "--nofollow-import-to=scipy.tests",
        "--nofollow-import-to=sklearn.tests",
        "--nofollow-import-to=librosa.tests",
        "--nofollow-import-to=qdarktheme.tests",
        "--nofollow-import-to=packaging.tests",
        "--nofollow-import-to=numpy.tests",
        "--nofollow-import-to=joblib.test",
        "--nofollow-import-to=joblib.testing",
        "--nofollow-import-to=sklearn.externals._numpydoc"
    )
    switch ($BuildProfile) {
        "release" {
            # Trial: rely on runtime smoke coverage instead of forcing sklearn/joblib.
            # This significantly reduces standalone compile graph size when those paths
            # are not needed by current runtime features.
        }
        "dev" {
            # Faster local/CI dev builds: same dependency exclusions as release, with
            # runtime smoke coverage to validate packaged startup + tiny analyze.
        }
        default {
            throw "Unsupported BuildProfile: $BuildProfile"
        }
    }
    if (Test-Path "assets\app_icon.ico") {
        $nuitkaArgs += "--windows-icon-from-ico=assets/app_icon.ico"
    }

    $resolvedNuitkaJobs = 0
    if ($NuitkaJobs -gt 0) {
        $resolvedNuitkaJobs = $NuitkaJobs
    }
    elseif ($env:NUMBER_OF_PROCESSORS) {
        $parsedJobs = 0
        if ([int]::TryParse($env:NUMBER_OF_PROCESSORS, [ref]$parsedJobs)) {
            $resolvedNuitkaJobs = [Math]::Max(1, $parsedJobs)
        }
    }
    if ($resolvedNuitkaJobs -gt 0) {
        $nuitkaArgs += "--jobs=$resolvedNuitkaJobs"
    }

    $nuitkaArgs += "build_gui_entry.py"

    $gitSha = ""
    $gitRef = ""
    try { $gitSha = (git rev-parse HEAD 2>$null).Trim() } catch {}
    try { $gitRef = (git describe --tags --always --dirty 2>$null).Trim() } catch {}

    $scriptSource = (Resolve-Path $PSCommandPath).Path
    Write-Host "Build profile: $BuildProfile"
    if ($gitSha) { Write-Host "Source commit: $gitSha" }
    if ($gitRef) { Write-Host "Source ref: $gitRef" }
    Write-Host "Build script source: $scriptSource"
    Write-Host "Repo root: $repoRoot"
    if ($resolvedNuitkaJobs -gt 0) { Write-Host "Nuitka parallel jobs: $resolvedNuitkaJobs" }
    Write-Host "Nuitka args count: $($nuitkaArgs.Count)"

    if (-not [string]::IsNullOrWhiteSpace($BuildInfoOutput)) {
        $buildInfoPath = if ([System.IO.Path]::IsPathRooted($BuildInfoOutput)) {
            $BuildInfoOutput
        } else {
            Join-Path $repoRoot $BuildInfoOutput
        }
        $buildInfoDir = Split-Path -Parent $buildInfoPath
        if ($buildInfoDir -and -not (Test-Path $buildInfoDir)) {
            New-Item -ItemType Directory -Force -Path $buildInfoDir | Out-Null
        }
        $buildInfoLines = @(
            "producer_os_build_context",
            "profile`t$BuildProfile",
            "repo_root`t$repoRoot",
            "script_source`t$scriptSource",
            "git_sha`t$gitSha",
            "git_ref`t$gitRef",
            "timestamp_utc`t$([DateTime]::UtcNow.ToString('o'))",
            "nuitka_args`t$($nuitkaArgs -join ' ')"
        )
        Set-Content -Path $buildInfoPath -Value $buildInfoLines -Encoding ascii
        Write-Host "Wrote build info: $buildInfoPath"
    }

    $resolvedTimingPath = $null
    if (-not [string]::IsNullOrWhiteSpace($TimingOutput)) {
        $resolvedTimingPath = if ([System.IO.Path]::IsPathRooted($TimingOutput)) {
            $TimingOutput
        } else {
            Join-Path $repoRoot $TimingOutput
        }
        $timingDir = Split-Path -Parent $resolvedTimingPath
        if ($timingDir -and -not (Test-Path $timingDir)) {
            New-Item -ItemType Directory -Force -Path $timingDir | Out-Null
        }
    }

    $nuitkaStartUtc = [DateTime]::UtcNow
    $markerTimes = @{}
    python -m nuitka @nuitkaArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        $nowUtc = [DateTime]::UtcNow

        if (-not $markerTimes.ContainsKey("python_compile_done") -and $line -like "*Completed Python level compilation and optimization.*") {
            $markerTimes["python_compile_done"] = $nowUtc
        }
        if (-not $markerTimes.ContainsKey("data_composer_start") -and $line -like "*Running data composer tool*") {
            $markerTimes["data_composer_start"] = $nowUtc
        }
        if (-not $markerTimes.ContainsKey("c_compile_start") -and $line -like "*Running C compilation via Scons.*") {
            $markerTimes["c_compile_start"] = $nowUtc
        }
        if (-not $markerTimes.ContainsKey("c_backend_seen") -and $line -like "*Nuitka-Scons: Backend C compiler:*") {
            $markerTimes["c_backend_seen"] = $nowUtc
        }
    }
    $nuitkaExitCode = $LASTEXITCODE
    $nuitkaEndUtc = [DateTime]::UtcNow
    if ($nuitkaExitCode -ne 0) {
        throw "Nuitka failed with exit code $nuitkaExitCode"
    }

    if ($resolvedTimingPath) {
        $seconds = {
            param([DateTime]$a, [DateTime]$b)
            [Math]::Round(($b - $a).TotalSeconds, 3)
        }

        $timingLines = @(
            "producer_os_nuitka_timing",
            "profile`t$BuildProfile",
            "git_sha`t$gitSha",
            "git_ref`t$gitRef",
            "nuitka_jobs`t$resolvedNuitkaJobs",
            "nuitka_total_seconds`t$(& $seconds $nuitkaStartUtc $nuitkaEndUtc)",
            "python_compile_done_seen`t$($markerTimes.ContainsKey('python_compile_done'))",
            "c_compile_start_seen`t$($markerTimes.ContainsKey('c_compile_start'))"
        )
        if ($markerTimes.ContainsKey("python_compile_done")) {
            $timingLines += "python_level_compile_seconds`t$(& $seconds $nuitkaStartUtc $markerTimes['python_compile_done'])"
        }
        if ($markerTimes.ContainsKey("data_composer_start")) {
            $timingLines += "data_composer_start_after_seconds`t$(& $seconds $nuitkaStartUtc $markerTimes['data_composer_start'])"
        }
        if ($markerTimes.ContainsKey("c_compile_start")) {
            $timingLines += "c_compile_start_after_seconds`t$(& $seconds $nuitkaStartUtc $markerTimes['c_compile_start'])"
            $timingLines += "scons_c_compile_and_link_seconds`t$(& $seconds $markerTimes['c_compile_start'] $nuitkaEndUtc)"
        }
        if ($markerTimes.ContainsKey("python_compile_done") -and $markerTimes.ContainsKey("c_compile_start")) {
            $timingLines += "between_python_done_and_c_compile_start_seconds`t$(& $seconds $markerTimes['python_compile_done'] $markerTimes['c_compile_start'])"
        }
        Set-Content -Path $resolvedTimingPath -Value $timingLines -Encoding ascii
        Write-Host "Wrote Nuitka timing: $resolvedTimingPath"
        Get-Content $resolvedTimingPath | ForEach-Object { Write-Host $_ }
    }

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

    $smokeInbox = Join-Path $repoRoot "examples\synthetic_corpus"
    if (!(Test-Path $smokeInbox)) {
        throw "Synthetic corpus missing for packaged tiny-analyze smoke: $smokeInbox"
    }
    $smokeHub = Join-Path $repoRoot "dist\smoke_tiny_analyze_hub"
    $smokeOut = Join-Path $repoRoot "dist\SMOKE_TINY_ANALYZE.json"
    if (Test-Path $smokeHub) { Remove-Item -Recurse -Force $smokeHub }
    if (Test-Path $smokeOut) { Remove-Item -Force $smokeOut }
    New-Item -ItemType Directory -Force -Path $smokeHub | Out-Null

    Write-Host "Running packaged tiny-analyze smoke test..."
    $env:PRODUCER_OS_SMOKE_TINY_ANALYZE = "1"
    $env:PRODUCER_OS_SMOKE_INBOX = $smokeInbox
    $env:PRODUCER_OS_SMOKE_HUB = $smokeHub
    $env:PRODUCER_OS_SMOKE_OUT = $smokeOut
    $analyzeProc = Start-Process -FilePath $exePath -PassThru
    try {
        Wait-Process -Id $analyzeProc.Id -Timeout $TinyAnalyzeSmokeTimeoutSeconds
        $analyzeProc.Refresh()
        if ($analyzeProc.ExitCode -ne 0) {
            if (Test-Path $smokeOut) {
                Write-Host "Tiny-analyze smoke output (failure):"
                Get-Content -Path $smokeOut | ForEach-Object { Write-Host $_ }
            }
            throw "Tiny-analyze smoke failed with exit code $($analyzeProc.ExitCode)"
        }
        if (!(Test-Path $smokeOut)) {
            throw "Tiny-analyze smoke output missing: $smokeOut"
        }
        $smokeJson = Get-Content -Path $smokeOut -Raw | ConvertFrom-Json
        if (-not $smokeJson.ok) {
            throw "Tiny-analyze smoke output reported failure: $($smokeJson | ConvertTo-Json -Depth 5 -Compress)"
        }
        if ([int]$smokeJson.files_processed -le 0) {
            throw "Tiny-analyze smoke processed 0 files."
        }
        Write-Host "Tiny-analyze smoke passed. files_processed=$($smokeJson.files_processed) packs=$($smokeJson.packs)"
    }
    catch {
        if (Test-Path $smokeOut) {
            Write-Host "Tiny-analyze smoke output (catch):"
            Get-Content -Path $smokeOut | ForEach-Object { Write-Host $_ }
        }
        try {
            if (-not $analyzeProc.HasExited) {
                Stop-Process -Id $analyzeProc.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {}
        throw
    }
    finally {
        Remove-Item Env:PRODUCER_OS_SMOKE_TINY_ANALYZE -ErrorAction SilentlyContinue
        Remove-Item Env:PRODUCER_OS_SMOKE_INBOX -ErrorAction SilentlyContinue
        Remove-Item Env:PRODUCER_OS_SMOKE_HUB -ErrorAction SilentlyContinue
        Remove-Item Env:PRODUCER_OS_SMOKE_OUT -ErrorAction SilentlyContinue
    }

    $signScript = Join-Path $PSScriptRoot "sign_windows_artifacts.ps1"
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
