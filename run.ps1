$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$needsSetup = $false

if (-not (Test-Path $venvPython)) {
    $needsSetup = $true
} else {
    try {
        & $venvPython -c "import requests; from PIL import Image, ImageTk" | Out-Null
    } catch {
        $needsSetup = $true
    }
}

if ($needsSetup) {
    Write-Host "Preparing virtual environment and dependencies..."
    if (-not (Test-Path $venvPython)) {
        python -m venv .venv
    }
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
}

& $venvPython nagme_desktop.py
