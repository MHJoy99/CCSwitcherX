<#
.SYNOPSIS
  Removes CCSwitcherX's program files, Start Menu shortcut and VS Code extension.
.DESCRIPTION
  Your saved logins are kept unless you pass -RemoveSavedLogins. Removing the program never signs you out of Claude Code.
#>
param(
  [string]$AppDir = (Join-Path $env:USERPROFILE '.claude-accounts'),
  [switch]$RemoveSavedLogins
)
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Claude Accounts.lnk'
Remove-Item $lnk -Force -ErrorAction SilentlyContinue
$code = Get-Command code -ErrorAction SilentlyContinue
if ($code) { & $code.Source --uninstall-extension local.claude-accounts 2>$null }
foreach ($f in 'claude_accounts.py', 'claude_accounts_core.py', 'claude_accounts_providers.py', 'claude_accounts_transcripts.py', 'claude_accounts_gui.py', 'icon.ico', 'icon.png') {
  Remove-Item (Join-Path $AppDir $f) -Force -ErrorAction SilentlyContinue
}
if ($RemoveSavedLogins) {
  foreach ($d in 'profiles', 'backups') { Remove-Item (Join-Path $AppDir $d) -Recurse -Force -ErrorAction SilentlyContinue }
  foreach ($f in 'usage.json', 'switch.journal', 'doctor-cache.json', 'login.log') { Remove-Item (Join-Path $AppDir $f) -Force -ErrorAction SilentlyContinue }
  Write-Host 'Saved logins removed.'
} else {
  Write-Host "Saved logins kept in $AppDir (profiles, backups). Re-run with -RemoveSavedLogins to delete them too."
}
