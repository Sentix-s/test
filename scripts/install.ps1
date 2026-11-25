<#
.SYNOPSIS
  JARVIS installer for Windows 11 (Phase 1).

.DESCRIPTION
  1. Finds Python 3.12 (or 3.11) (via the py launcher) and creates .venv
  2. Installs JARVIS and its dependencies into .venv
  3. Checks Ollama (offers to install it with winget) and starts the server
  4. Downloads the language model (default qwen3:8b, ~5.2 GB)
  5. Runs `jarvis doctor`

  Nothing is installed system-wide without asking; no admin rights needed
  except for whatever winget itself asks for.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Model qwen2.5:7b-instruct -Yes
#>
param(
    [string]$Model = "",
    [switch]$Yes,          # answer "yes" to install prompts (winget Python / Ollama)
    [switch]$SkipModel     # do not download the model now
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Info($m) { Write-Host "[JARVIS] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[JARVIS] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[JARVIS] $m" -ForegroundColor Red; exit 1 }

function Confirm-Step($question) {
    if ($Yes) { return $true }
    $a = Read-Host "$question [y/N]"
    return ($a -match '^(y|yes)$')
}

function Find-Python {
    foreach ($v in @("3.12", "3.11")) {
        try {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch { }
    }
    return $null
}

# ---------------------------------------------------------------- Python
$py = Find-Python
if (-not $py) {
    Warn "Python 3.12/3.11 not found (py launcher)."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm-Step "Install Python 3.12 with winget?")) {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $py = Find-Python
    }
    if (-not $py) { Fail "Install Python 3.12 from https://www.python.org/downloads/ and re-run this script." }
}
Info "Python: $py"

# ---------------------------------------------------------------- venv
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Info "Creating virtual environment .venv"
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
}
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
Info "Installing dependencies"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

# ---------------------------------------------------------------- Ollama
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Warn "Ollama is not installed."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm-Step "Install Ollama with winget?")) {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    }
    if (-not $ollama) { Fail "Install Ollama from https://ollama.com/download/windows and re-run this script." }
}

function Test-Ollama {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3 | Out-Null; return $true }
    catch { return $false }
}
if (-not (Test-Ollama)) {
    Info "Starting Ollama server"
    Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-Ollama)) { Fail "Ollama did not start. Launch the Ollama app manually and re-run." }
}
Info ("Ollama " + (Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version").version)

# ---------------------------------------------------------------- model
if (-not $SkipModel) {
    $setupArgs = @("-m", "jarvis", "setup")
    if ($Model) { $setupArgs += @("--model", $Model) }
    & $venvPy @setupArgs
    if ($LASTEXITCODE -ne 0) { Fail "Model download failed" }
}

# ---------------------------------------------------------------- check
& $venvPy -m jarvis doctor
Info "Done. Start JARVIS with:  jarvis.bat"
