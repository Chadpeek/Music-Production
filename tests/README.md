# Test Instructions for Producer OS v2

This directory contains scripts and instructions to exercise the core
behaviour of Producer OS v2.  The tests are designed to be run on
Windows using PowerShell, but can be adapted to other platforms.

## Preparing Test Data

Run the test data generator to create a clean `inbox` and `hub`
environment:

```powershell
python tests/generate_test_data.py --output .\test_env
```

This will create a directory `test_env` containing `inbox` and `hub`.

## 1. NFO Placement Rules

Verify that `.nfo` files are created only at the category, bucket and
pack levels and never beside individual samples.

```powershell
# Run a copy operation to generate the hub structure
python -m producer_os.cli copy .\test_env\inbox .\test_env\hub

# List all .nfo files and ensure none live next to WAV files
Get-ChildItem -Path .\test_env\hub -Filter *.nfo -Recurse | ForEach-Object {
    $nfo = $_
    $folder = $nfo.DirectoryName
    $basename = $nfo.BaseName
    $targetFolder = Join-Path $folder $basename
    # The .nfo should be next to a directory of the same name
    if (-not (Test-Path $targetFolder -PathType Container)) {
        Write-Host "Orphan nfo found: $($nfo.FullName)" -ForegroundColor Red
    }
}
```

Expected output: no lines printed in red; `.nfo` files exist only beside
folders.

## 2. No Per‑WAV `.nfo`

Check that no `.nfo` exists next to individual WAV files in the hub:

```powershell
Get-ChildItem -Path .\test_env\hub -Recurse -File -Filter *.nfo | Where-Object {
    $_.Directory.GetFiles('*.wav').Count -gt 0
} | Should -BeNullOrEmpty
```

The command should return nothing.

## 3. Idempotency

Running the same command twice should not move or copy additional
files, nor should it rewrite identical `.nfo` files.  Verify by
recording timestamps:

```powershell
python -m producer_os.cli move .\test_env\inbox .\test_env\hub
# Capture timestamps
Get-ChildItem -Path .\test_env\hub -Recurse | Select-Object FullName, LastWriteTime | Out-File before.txt

python -m producer_os.cli move .\test_env\inbox .\test_env\hub
Get-ChildItem -Path .\test_env\hub -Recurse | Select-Object FullName, LastWriteTime | Out-File after.txt

Compare-Object (Get-Content before.txt) (Get-Content after.txt) | Should -BeNull
```

No differences should be reported.

## 4. Style Fallback

Delete a bucket style from `bucket_styles.json` and ensure that
folders still get a neutral style instead of aborting.  For example,
remove the entry for `Percs` and run:

```powershell
$styles = Get-Content .\producer_os_project\bucket_styles.json | ConvertFrom-Json
$styles.buckets.Remove('Percs')
$styles | ConvertTo-Json -Depth 10 | Out-File .\producer_os_project\bucket_styles.json

python -m producer_os.cli copy .\test_env\inbox .\test_env\hub
```

The command should log a warning once about the missing style and
continue.  The `Percs` folder’s `.nfo` will contain the default
colour and icon.

## 5. Undo Last Run

After performing a move run, call `undo-last-run` and verify that
files return to the inbox:

```powershell
python -m producer_os.cli move .\test_env\inbox .\test_env\hub

# Check that the inbox is now empty
Get-ChildItem .\test_env\inbox -Recurse | Should -BeNullOrEmpty

# Undo the last run
python -m producer_os.cli undo-last-run .\test_env\inbox .\test_env\hub

# Verify that files have been restored
Get-ChildItem .\test_env\inbox -Recurse | Where-Object { $_.PSIsContainer -eq $false } | Should -Not -BeNullOrEmpty
```

## 6. Repair Styles Correction

Simulate a broken hub by deleting some `.nfo` files and creating
orphan `.nfo` files in random locations, then run the repair
command:

```powershell
Remove-Item .\test_env\hub\Samples.nfo
New-Item -Path .\test_env\hub\Cymbals -Name "orphan.nfo" -ItemType File

python -m producer_os.cli repair-styles .\test_env\inbox .\test_env\hub

# Check that missing .nfos were recreated and the orphan was removed
Test-Path .\test_env\hub\Samples.nfo | Should -BeTrue
Test-Path .\test_env\hub\Cymbals\orphan.nfo | Should -BeFalse
```

## 7. Portable Mode Detection

Verify that the presence of a `portable.flag` in the hub directory
forces configuration files to reside there.  Create the flag and
check that settings persist locally:

```powershell
New-Item -Path .\test_env\hub\portable.flag -ItemType File
python -m producer_os.cli analyze .\test_env\inbox .\test_env\hub --portable

# Config file should now be written to the hub directory
Test-Path .\test_env\hub\config.json | Should -BeTrue
```

The absence of the flag should cause the config to be stored in
``%APPDATA%\ProducerOS`` or the platform equivalent.