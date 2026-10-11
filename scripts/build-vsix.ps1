<#
.SYNOPSIS
  Packages the VS Code extension into dist/claude-accounts-<version>.vsix (needs Node.js; downloads @vscode/vsce on first use).
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$ext = Join-Path $repo 'extension'
$version = (Get-Content (Join-Path $ext 'package.json') -Raw | ConvertFrom-Json).version
$dist = Join-Path $repo 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Push-Location $ext
try {
  npx --yes @vscode/vsce package --allow-missing-repository --skip-license --no-dependencies -o (Join-Path $dist "claude-accounts-$version.vsix")
  if ($LASTEXITCODE -ne 0) { throw 'vsce failed' }
} finally { Pop-Location }
Write-Host "Built $(Join-Path $dist "claude-accounts-$version.vsix")"
