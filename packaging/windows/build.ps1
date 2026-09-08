# AI Ecosystem production build (Gate 44 + review fix 10).
# Operator-run: checks toolchains, installs the React UI reproducibly,
# verifies the Python backend, compiles the Tauri shell, migrates a seed
# database, and stages ONE canonical installer input tree in dist/.
# The installer (installer.iss) reads only from dist/ -- nothing else.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required toolchain missing: $Name"
    }
}

function Require-MinimumPython {
    $version = cmd /c "python --version 2>&1"
    if ($version -notmatch "Python (\d+)\.(\d+)") {
        throw "Cannot determine Python version (got: $version)"
    }
    if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 10)) {
        throw "Python 3.10+ required (got: $version)"
    }
    Write-Host "Toolchain: $version"
}

function Require-File($Path, $Why) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Build verification failed: missing $Path ($Why)"
    }
}

Require-Command python
Require-Command npm
Require-Command cargo
Require-MinimumPython

$Out = Join-Path $Root "dist"
$BundleOut = Join-Path $Out "bundle"
New-Item -ItemType Directory -Path $BundleOut -Force | Out-Null

# 1. Reproducible UI build (lockfile, not floating installs).
Push-Location (Join-Path $Root "desktop")
try {
    npm ci
    npm run build
} finally {
    Pop-Location
}
Require-File (Join-Path $Root "desktop\dist\index.html") "vite build produced no output"

# 2. Backend package present, importable, CLI working.
python -m pip install -e (Join-Path $Root ".")
cmd /c "python -c ""import ai_ecosystem.interface.serve, ai_ecosystem.core.persistence.sqlite; print('backend import ok')"""
if ($LASTEXITCODE -ne 0) { throw "Backend import check failed" }
cmd /c "ai-ecosystem-serve --help"
if ($LASTEXITCODE -ne 0) { throw "Backend console script (ai-ecosystem-serve) is not on PATH" }

# 3. Seed database migrates to the current schema version.
$env:AI_ECO_DB_PATH = Join-Path $Out "seed.db"
if (Test-Path -LiteralPath $env:AI_ECO_DB_PATH) { Remove-Item -LiteralPath $env:AI_ECO_DB_PATH -Force }
python -c "from ai_ecosystem.core.persistence import Database; v = Database(r'$env:AI_ECO_DB_PATH').migrate(); assert v >= 2, v; print(f'seed schema v{v}')"
if ($LASTEXITCODE -ne 0) { throw "Seed database migration failed" }

# 4. Tauri shell + installers.
Push-Location (Join-Path $Root "desktop")
try {
    cmd /c "npm run tauri build"
} finally {
    Pop-Location
}

# 5. Stage the canonical installer input tree (installer.iss reads this).
$TauriBundle = Join-Path $Root "desktop\src-tauri\target\release\bundle"
Require-File (Join-Path $TauriBundle "msi\AI Ecosystem_0.1.0_x64_en-US.msi") "Tauri MSI bundle missing"
Require-File (Join-Path $TauriBundle "nsis\AI Ecosystem_0.1.0_x64-setup.exe") "Tauri NSIS bundle missing"
Copy-Item (Join-Path $TauriBundle "msi\*") $BundleOut -Force
Copy-Item (Join-Path $TauriBundle "nsis\*") $BundleOut -Force
$Exe = Join-Path $Root "desktop\src-tauri\target\release\ai-ecosystem-desktop.exe"
Require-File $Exe "release executable missing"
Copy-Item $Exe $BundleOut -Force

$Commit = cmd /c "git -C ""$Root"" rev-parse --short HEAD 2>NUL"
$BuildInfo = "version=0.1.0`ncommit=$Commit`npython=$(cmd /c 'python --version 2>&1')`nbuilt=$(Get-Date -Format o)"
Set-Content -LiteralPath (Join-Path $Out "BUILD-INFO.txt") -Value $BuildInfo

Write-Host "Staged installer input in $Out"
Write-Host "NOTE: target machines need Python 3.10+ with the ai-ecosystem package installed;"
Write-Host "the installer does not yet embed a Python runtime (see installer.iss)."
