<#
.SYNOPSIS
    Bootstrap push-to-talk dictation on a machine that has no Python.

.DESCRIPTION
    This script does exactly three things, because they are the only three that cannot be
    done from inside the app:

        1. install uv,
        2. `uv sync` -- which fetches CPython 3.13 and every dependency,
        3. hand over to `ptt setup`, which does the rest.

    Everything else -- the model, the icons, the GPU check, the microphone, the PATH entry,
    autostart -- lives in pushtotalk/setup.py, where it can reuse what the app already
    knows and where the test suite covers it. Adding project knowledge to this file is
    almost always the wrong place for it.

    Safe to re-run: every step checks for what it is about to do.

.PARAMETER SetupArgs
    Passed straight through to `ptt setup`. See `ptt setup --help`; the useful ones are
    --add-to-path, --autostart, --elevated, --start, --skip-model.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\Setup.ps1 -- --add-to-path --autostart --start

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\Setup.ps1 -- --skip-model
#>

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SetupArgs = @()
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Say([string]$Message, [string]$Colour = 'Gray') {
    Write-Host "  $Message" -ForegroundColor $Colour
}

Write-Host ''
Write-Host '  PushToTalk bootstrap' -ForegroundColor White
Say "target: $Root"

# `-- --add-to-path` is the documented form; PowerShell hands the bare `--` through and
# python's argparse would reject it.
$SetupArgs = @($SetupArgs | Where-Object { $_ -ne '--' })

# ------------------------------------------------------------------ 1. uv
Write-Host ''
Say '1. uv' 'Cyan'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Say "already installed: $(uv --version)" 'Green'
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id astral-sh.uv -e --source winget `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget install uv failed (exit $LASTEXITCODE)" }
} else {
    Say 'no winget; using the official installer from astral.sh'
    Invoke-Expression (Invoke-RestMethod 'https://astral.sh/uv/install.ps1')
}

# The installer puts uv on the PATH of *future* shells; this one still needs telling.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is still not on the PATH -- open a new PowerShell window and re-run this'
}
Say "$(uv --version)" 'Green'

# ------------------------------------------------------------------ 2. venv
Write-Host ''
Say '2. python and dependencies' 'Cyan'
Push-Location $Root
try {
    # Reads .python-version, fetches CPython 3.13 if the machine has none, and resolves
    # pyproject.toml against the pinned uv.lock. About 2.2 GB, mostly the pip CUDA
    # libraries -- cublasLt64_12.dll alone is over 500 MB.
    uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)" }
    Say "environment ready at $Root\.venv" 'Green'

    # -------------------------------------------------------------- 3. hand over
    Write-Host ''
    Say '3. handing over to `ptt setup`' 'Cyan'
    & "$Root\.venv\Scripts\python.exe" -m pushtotalk setup @SetupArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $code
