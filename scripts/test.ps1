<#
.SYNOPSIS
  Runs every test. Nothing touches your real Claude login: the tests use throw-away folders and fake accounts, and no
  window is opened (the extension is exercised against a mocked VS Code API).
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$sandbox = Join-Path $env:TEMP ("ccsx-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
$failed = $false
try {
  Write-Host '== engine tests (python)' -ForegroundColor Cyan
  python -I (Join-Path $repo 'tests\test_core.py'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  python -I (Join-Path $repo 'tests\test_providers.py'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  python -I (Join-Path $repo 'tests\test_transcripts.py'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  python -I (Join-Path $repo 'tests\test_switch_e2e.py'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  if (Test-Path (Join-Path $repo 'tests\test_gui_smoke.py')) {
    Write-Host '== window tests (python, tkinter, window hidden)' -ForegroundColor Cyan
    python -I (Join-Path $repo 'tests\test_gui_smoke.py'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  }

  if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host '== extension tests (node, mocked VS Code)' -ForegroundColor Cyan
    python -I (Join-Path $repo 'tests\make_sandbox.py') $sandbox | Out-Null
    $env:CA_CLAUDE_DIR = Join-Path $sandbox 'claude'
    $env:CA_STORE = Join-Path $sandbox 'store'
    node (Join-Path $repo 'tests\test_extension.js'); if ($LASTEXITCODE -ne 0) { $failed = $true }
  } else {
    Write-Host '== extension tests skipped (Node.js not found)' -ForegroundColor Yellow
  }
} finally {
  Remove-Item Env:\CA_CLAUDE_DIR, Env:\CA_STORE -ErrorAction SilentlyContinue
  Remove-Item $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}
if ($failed) { Write-Host 'TESTS FAILED' -ForegroundColor Red; exit 1 }
Write-Host 'ALL TESTS PASSED' -ForegroundColor Green
