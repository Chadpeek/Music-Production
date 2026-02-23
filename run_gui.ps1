$ErrorActionPreference="Stop"
$env:PYTHONPATH = (Resolve-Path ".\src").Path
python -m producer_os.gui
