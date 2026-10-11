<#
.SYNOPSIS
  Installs CCSwitcherX for the current Windows user: the engine, a Start Menu shortcut and the VS Code extension.
.DESCRIPTION
  Copies the program files (and only those) into ~/.claude-accounts, so your saved logins, which live in the same
  folder, are never touched. Safe to run again to update.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipExtension
#>
param(
  [string]$AppDir = (Join-Path $env:USERPROFILE '.claude-accounts'),
  [switch]$SkipShortcut,
  [switch]$SkipExtension,
  [switch]$NoSelfCheck
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
function Step($m) { Write-Host "-> $m" -ForegroundColor Cyan }

# 1. Python 3.9+ with tkinter
Step 'Checking Python'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python 3.9 or newer is required (https://www.python.org/downloads/). Install it, tick "Add python.exe to PATH", and run this again.' }
& $python.Source -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Python 3.9+ with tkinter is required. Re-run the Python installer and keep "tcl/tk and IDLE" ticked.' }
$pythonw = Join-Path (Split-Path $python.Source) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe was not found next to $($python.Source)." }

# 2. Program files only. profiles/, backups/, usage.json and the other saved data are never copied over or removed.
Step "Copying program files to $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
foreach ($f in 'claude_accounts.py', 'claude_accounts_core.py', 'claude_accounts_providers.py', 'claude_accounts_gui.py', 'icon.ico', 'icon.png') {
  Copy-Item -LiteralPath (Join-Path $repo "src\$f") -Destination $AppDir -Force
}

# 3. Start Menu shortcut (so "Claude Accounts" is found by Windows search)
if (-not $SkipShortcut) {
  Step 'Creating the Start Menu shortcut'
  $dir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $dir 'Claude Accounts.lnk'))
  $s.TargetPath = $pythonw
  $s.Arguments = '"' + (Join-Path $AppDir 'claude_accounts.py') + '"'
  $s.WorkingDirectory = $AppDir
  $s.IconLocation = (Join-Path $AppDir 'icon.ico') + ',0'
  $s.Description = 'Switch the Claude Code login between your saved accounts'
  $s.Save()
}

# 4. VS Code extension
if (-not $SkipExtension) {
  $code = Get-Command code -ErrorAction SilentlyContinue
  if (-not $code) {
    Write-Host '   VS Code command "code" not found, skipping the extension. (Install it from the Releases page later.)' -ForegroundColor Yellow
  } else {
    Step 'Installing the VS Code extension'
    $vsix = Get-ChildItem (Join-Path $repo 'dist') -Filter '*.vsix' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $vsix) { & (Join-Path $PSScriptRoot 'build-vsix.ps1'); $vsix = Get-ChildItem (Join-Path $repo 'dist') -Filter '*.vsix' | Select-Object -First 1 }
    & $code.Source --install-extension $vsix.FullName --force
    if ($AppDir -ne (Join-Path $env:USERPROFILE '.claude-accounts')) {
      Write-Host "   You used a custom folder: set the VS Code setting claudeAccounts.appDir to $AppDir" -ForegroundColor Yellow
    }
  }
}

# 5. Self-check
if (-not $NoSelfCheck) {
  Step 'Running the self-check'
  & $python.Source -I (Join-Path $AppDir 'claude_accounts.py') doctor
}
Write-Host "`nDone. Open 'Claude Accounts' from the Start Menu, or click the account name in VS Code's status bar." -ForegroundColor Green
