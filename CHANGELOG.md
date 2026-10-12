# Changelog

All notable changes to CCSwitcherX. Dates are in ISO format.

## 1.3.0 (2026-10-13)

- **A redesigned window.** The account in use gets a large panel with its session and weekly usage in big numbers,
  reset times and model limits. Every other account is a compact row sorted by how much room it has left, with a clear
  status: "Room left", "Full until 1:00 AM", "Session fresh" or "Sign in again". Rows keep a fixed column grid, long
  names end in "…", numbers older than 15 minutes are dimmed and labelled with their age, and both light and dark
  themes were redone.
- **Live usage for accounts you are not using.** When a saved account's short-lived access token has expired, the app
  renews it the way Claude Code does and saves the new tokens back into that saved account, so its numbers are current
  instead of hours old. The account in use is left to Claude Code. If the server refuses a saved sign-in, the account
  shows "Sign in again".
- **Plan changes show up by themselves.** An upgrade (for example Pro to Max 5x) appears on the card after the next
  usage refresh, without signing in again.
- **Safer switching.** Switching now reads the saved account under Claude Code's locks, so a switch that happens while
  the app is renewing that same account always uses the newest sign-in. A new end-to-end test with a fake token server
  checks this, along with rotation, fast repeated switching and switching back.
- The VS Code extension also says "Sign in again" when a saved sign-in was refused.

## 1.2.0 (2026-10-12)

- **Switching back to a Claude account keeps old chats working.** Chats that ran on a provider's model carried fields Anthropic's API rejects (`tool_use.signature: Extra inputs are not permitted`, `Invalid signature in thinking block`). Every switch to an account (and `provider off`) now repairs those chats first: only blocks written by non-Claude models are touched, originals are kept in `transcript-backups/`, and a repair problem never blocks the switch.
- **Providers: use other models, one click.** Save a gateway (address, API key, which model answers for Opus, Sonnet, Haiku
  and subagents) and switch Claude Code to it from the window, the VS Code status bar or `provider use NAME`; switch back by
  picking an account. A switch reaches Claude Code from its next message (about two seconds), with no restart.
- The API key is never written to Claude Code's `settings.json`: it stays DPAPI-encrypted in the store and an `apiKeyHelper`
  command hands it to Claude Code on demand. Only six keys of `settings.json` are ever touched, their previous values are
  restored on the way back, the base URL and the key helper are written together, every change has an encrypted backup, is
  verified and rolled back on failure, and a `settings.json` that is not valid JSON is never written.
- **Test connection** checks every model with a tool call followed by a second step (where gateways that mishandle thinking
  blocks fail), and reports a readable result per model.
- `doctor` also checks the settings file, stray credentials that would override a provider's key, and the key helper.
- New command group `provider list | add | edit | use | off | test | remove`; keys are accepted only through `--key-env` or `--key-stdin`.
- **Reset times read the right minute.** Anthropic reports resets a fraction of a second before the hour, so a reset at
  1:00 AM showed as "12:59 AM". Times are now rounded to the nearest minute.

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
