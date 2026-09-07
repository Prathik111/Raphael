# AI Ecosystem production build (Gate 44).
# Operator-run: checks toolchains, vendors the Python runtime side,
# builds the React UI, compiles the Tauri shell, migrates a seed
# database, and stages the installer input tree.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required toolchain missing: $Name"
    }
}

Require-Command python
Require-Command npm
Require-Command cargo

$Out = Join-Path $Root "dist"
New-Item -ItemType Directory -Path $Out -Force | Out-Null

Push-Location (Join-Path $Root "desktop")
try {
    npm install
    npm run build
} finally {
    Pop-Location
}

python -m pip install --upgrade pip
python -m pip install -e (Join-Path $Root ".")

$env:AI_ECO_DB_PATH = Join-Path $Out "seed.db"
python -c "from ai_ecosystem.core.persistence import Database; Database(r'$env:AI_ECO_DB_PATH').migrate()"

Push-Location (Join-Path $Root "desktop")
try {
    cargo tauri build
} finally {
    Pop-Location
}

Write-Host "Staged installer input in $Out"
