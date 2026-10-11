# Changelog

All notable changes to CCSwitcherX. Dates are in ISO format.

## 1.1.0 (2026-10-11)

- **Usage for every account.** The window, the VS Code status bar and `claude_accounts.py usage` show the 5-hour
  session and 7-day weekly usage with reset times. The active account uses Claude Code's own cache; other accounts are
  read only while their saved access token is still valid, otherwise the last known numbers are shown with an "as of" age.
  A saved login is never refreshed just to read usage.
- `Refresh usage` button, `usage` command and `Refresh usage` entry in the status-bar picker.
- `--version` flag.
- Packaged as a repository: install and uninstall scripts, one-command tests, website, interactive demo and docs.
- Saved logins now always live in `~/.claude-accounts`, never next to the program files.

## 1.0.0 (2026-10-11)

- First release: the Claude Accounts window, saved logins encrypted with Windows DPAPI, one-click switching under
  Claude Code's own lock files, backup before every switch, crash-recovery journal, rollback on a failed write, Add
  account with restore-on-cancel, the VS Code status-bar extension, the command line, and the `doctor` self-check.
