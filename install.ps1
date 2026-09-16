<#
.SYNOPSIS
    Install the math-rigor suite into ZCode's user scope.

.DESCRIPTION
    Creates a virtualenv, installs the Python dependencies, then registers the MCP
    server, the skill, and the slash commands.  Safe to re-run.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root "venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

Write-Host "math-rigor installer" -ForegroundColor Cyan
Write-Host "repository root: $Root"
Write-Host ""

# --- 1. locate a Python interpreter ---------------------------------------- #
if (-not (Test-Path $VenvPython)) {
    $systemPython = $null
    foreach ($candidate in @("py -3", "python", "python3")) {
        $parts = $candidate.Split(" ")
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if ($exe) {
            try {
                $version = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
                if ($version) {
                    $systemPython = $candidate
                    Write-Host "found $candidate (Python $version)"
                    break
                }
            } catch { }
        }
    }
    if (-not $systemPython) {
        Write-Host "ERROR no Python 3 interpreter found on PATH." -ForegroundColor Red
        Write-Host "      Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
        exit 1
    }

    Write-Host ""
    Write-Host "creating the virtualenv at $Venv"
    $parts = $systemPython.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv $Venv
    if (-not (Test-Path $VenvPython)) {
        Write-Host "ERROR the virtualenv was not created." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "installing dependencies (this downloads sympy and z3, ~60 MB)"
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR dependency installation failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "reusing the existing virtualenv at $Venv"
}

# --- 2. prove the server actually starts before touching any config --------- #
Write-Host ""
Write-Host "checking that the server starts"
& $VenvPython (Join-Path $Root "tools\smoke_test.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR the server did not respond; not modifying your configuration." -ForegroundColor Red
    exit 1
}

# --- 3. install into ZCode -------------------------------------------------- #
Write-Host ""
Write-Host "installing into ZCode"
& $VenvPython (Join-Path $Root "tools\install.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR installation reported problems." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "done." -ForegroundColor Green
