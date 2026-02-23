# Patch-RunLog.ps1 (copy/paste and run in PowerShell)

$engine = "C:\producer_os_project\src\producer_os\engine.py"
if (!(Test-Path $engine)) { throw "engine.py not found at $engine" }

# Backup
$backup = "$engine.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item $engine $backup -Force
Write-Host "Backup created: $backup"

# Load file
$lines = Get-Content $engine -Encoding UTF8

function Insert-AfterFirstMatch {
    param(
        [string[]]$Lines,
        [string]$Pattern,
        [string[]]$InsertLines
    )
    $idx = ($Lines | Select-String -Pattern $Pattern | Select-Object -First 1).LineNumber
    if (-not $idx) { throw "Pattern not found: $Pattern" }
    $i = $idx # LineNumber is 1-based; insert after this line -> index $i in 0-based insertion
    return @($Lines[0..($i-1)] + $InsertLines + $Lines[$i..($Lines.Length-1)])
}

function Insert-BeforeFirstMatch {
    param(
        [string[]]$Lines,
        [string]$Pattern,
        [string[]]$InsertLines
    )
    $idx = ($Lines | Select-String -Pattern $Pattern | Select-Object -First 1).LineNumber
    if (-not $idx) { throw "Pattern not found: $Pattern" }
    $i = $idx - 1 # insert before match
    if ($i -le 0) { return @($InsertLines + $Lines) }
    return @($Lines[0..($i-1)] + $InsertLines + $Lines[$i..($Lines.Length-1)])
}

# 1) Insert header logs after the _log function ends (right before "try:")
# We target the first "try:" that follows the _log definition in run().
# This insertion assumes your run() has:
#   def _log(...):
#       ...
#   try:
$header = @(
'        _log(f"Producer OS run_id={run_id} mode={mode}")',
'        _log(f"Hub: {self.hub_dir}")',
'        _log(f"Packs discovered: {len(packs)}")'
)

# Insert header just BEFORE the first "try:" after _log
$lines = Insert-BeforeFirstMatch -Lines $lines -Pattern '^\s*try:\s*$' -InsertLines $header

# 2) Insert "Processing pack" right after the first "for pack_dir in packs:"
$processing = @(
'                _log(f"Processing pack: {pack_dir.name}")'
)
$lines = Insert-AfterFirstMatch -Lines $lines -Pattern '^\s*for\s+pack_dir\s+in\s+packs:\s*$' -InsertLines $processing

# 3) Insert "Finished pack" right BEFORE the line that appends the pack report (the first occurrence)
$finished = @(
'                _log(f"Finished pack: {pack_dir.name} files={len(pack_report[\"files\"])})"'
)
$lines = Insert-BeforeFirstMatch -Lines $lines -Pattern '^\s*report\["packs"\]\.append\(pack_report\)\s*$' -InsertLines $finished

# 4) Insert final summary log right before the final "return report" (the LAST occurrence)
# We'll do a manual search for the last matching line.
$returnIdx = ($lines | Select-String -Pattern '^\s*return\s+report\s*$' | Select-Object -Last 1).LineNumber
if (-not $returnIdx) { throw "Could not find final 'return report'" }
$done = @(
'        _log(f"Done. processed={report[\"files_processed\"]} copied={report[\"files_copied\"]} moved={report[\"files_moved\"]} failed={report[\"failed\"]} unsorted={report[\"unsorted\"]} skipped={report[\"skipped_existing\"]}")'
)
$insertPos = $returnIdx - 1
$lines = @($lines[0..($insertPos-1)] + $done + $lines[$insertPos..($lines.Length-1)])

# Save
Set-Content -Path $engine -Value $lines -Encoding UTF8
Write-Host "Patched: $engine"

# Compile check
python -m py_compile $engine
if ($LASTEXITCODE -ne 0) {
    Write-Host "Compile failed. Restoring backup..."
    Copy-Item $backup $engine -Force
    throw "Patch rolled back due to syntax error."
}

Write-Host "Compile OK. run_log.txt should now contain output."