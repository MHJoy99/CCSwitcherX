# FAQ

Short answers about CCSwitcherX. The first 13 questions are the canonical set and their answers are kept word for word; the last six are answered from the code (version 1.2.0).

## What is CCSwitcherX?

CCSwitcherX is a free, open-source (MIT) Windows tool that switches the Claude Code login between your own saved accounts, for example a personal Max plan and a work Team plan, in one click. It has a small window and a VS Code status-bar extension, and it needs nothing beyond Python 3.9 or newer.

## How do I switch between a personal and a Team Claude account in VS Code?

Install CCSwitcherX, sign in to each account once with Add account, then click the account name in the VS Code status bar and pick the other account. The swap takes a fraction of a second and VS Code does not need a restart. Open Claude Code chats pick up the new account on their next message; if one does not, start a new chat.

## Do I have to log in again every time I switch?

No. You sign in once per account. CCSwitcherX keeps each login encrypted on your PC and puts the right one back when you switch. A saved login stays valid for about 28 days from the last time it was refreshed, so use each account at least once in that time. The window warns you when a saved login is within 7 days of expiring.

## Is it safe? Where are my logins stored?

Saved logins are encrypted with Windows DPAPI, so only your Windows user account on that PC can read them. They never leave your computer: CCSwitcherX has no server and no telemetry. Before every switch it backs up the current login, keeps a recovery journal in case of a crash, and rolls back if a file write fails.

## Does switching change my Claude Code settings, skills, memory or chat history?

No. Only the login is swapped: the claudeAiOauth entry in ~/.claude/.credentials.json and the oauthAccount entry in ~/.claude.json (plus one cached flag that Claude Code recreates by itself). Skills, CLAUDE.md, memory, MCP logins and chat history are shared by every account.

## Can I see usage for each account?

Yes. The window and the status bar show the 5-hour session and 7-day weekly usage with reset times. The active account's numbers come from Claude Code itself. Other accounts are read live while their saved sign-in is still fresh (about 8 hours after you last used them), otherwise you see the last known numbers marked "as of".

## Which platforms and versions does it support?

Windows 10 and 11 with Python 3.9 or newer (tested on Python 3.11). It works with the Claude Code command line and the Claude Code VS Code extension. macOS and Linux are not supported yet because the encryption uses Windows DPAPI.

## Is CCSwitcherX an official Anthropic tool?

No. CCSwitcherX is independent and unofficial. Claude and Claude Code are trademarks of Anthropic. It only moves your own login between your own accounts on your own computer; check Anthropic's terms for your plan.

## Does it work with Claude Pro, Max and Team accounts?

It is built for claude.ai subscription logins (Pro, Max and Team) and does not manage API keys. It has been tested with real Max and Pro accounts; Team accounts use the same login format.

## How do I add an account?

Open Claude Accounts and click Add account, then sign in with the account in the browser tab that opens (choose Switch account on that page if it shows the wrong one) and give it a name. Your previous login is saved automatically first, and cancelling puts it back.

## How is CCSwitcherX different from claude-swap and CCSwitcher?

They use the same basic technique: back up each account's login and swap it in. CCSwitcherX is Windows-first with a graphical window and a VS Code status bar, encrypts saved logins with Windows DPAPI, shows usage for each account and ships with a self-check. claude-swap is cross-platform with terminal tooling and automatic rotation, and CCSwitcher is a macOS menu-bar app.

## Does it work with CCUsageX?

Yes. CCUsageX reads your Claude Code chat history and Claude Code's own usage report, and both follow the active account, so its plan-limit card shows the account you switched to. Its cost and token totals combine all accounts, because chat history does not record which account was used.

## What happens if I stop using CCSwitcherX?

Nothing breaks. Claude Code keeps using whichever account was active last. Uninstall with scripts/uninstall.ps1; your saved logins are kept unless you ask to remove them.

## Where are my saved logins stored?

Each saved login is one file in `~/.claude-accounts/profiles`, named by a 12-character id (`profile_id` in `src/claude_accounts_core.py`). The files are encrypted with Windows DPAPI for your Windows user (`protect` and `unprotect` in the same file). A copy of the login that was active before each switch is kept in `~/.claude-accounts/backups`; the newest 10 are kept. The folder is `%USERPROFILE%\.claude-accounts` unless the `CA_STORE` variable is set. Removing an account deletes its profile file, but not the backup copies, which go once they are rotated out. See [ARCHITECTURE.md](ARCHITECTURE.md#saved-login-storage).

## What does Refresh usage do?

It updates the usage numbers in the window and in the status bar. For the active account it reads Claude Code's own cached numbers if they are less than 10 minutes old, and makes no network call. For each other account it makes one read-only request to `api.anthropic.com`, and only while that account's saved access token is still valid. It never refreshes a saved token to do this. Normal refreshes wait at least 3 minutes per account; the Refresh usage button skips that wait but still respects the 10-minute backoff after an error. Results go to `usage.json`, which holds percentages and times, not tokens. See `refresh_usage` in `src/claude_accounts_core.py`.

## Why does a card say "Sign-in expired" and what should I do?

The saved sign-in's refresh token has passed its expiry date (`refreshTokenExpiresAt`, checked by `refresh_expired`). CCSwitcherX cannot switch to that account, so its Switch button is disabled and a switch is refused with a message. To fix it, click Add account and sign in with the same account in the browser. The saved copy is replaced with the new sign-in and keeps its name. A card with fewer than 7 days left shows "Saved sign-in expires in N days" instead (`extension.js` and `claude_accounts_gui.py` both show this warning for saved accounts under 7 days).

## How do I remove an account?

In the window, click the ⋯ button on its card and choose "Remove from list…". In VS Code, open the picker and click the trash button on its row. From a terminal, run `python claude_accounts.py remove NAME`. This deletes the saved copy and its usage numbers. It does not sign you out: if that account is active, Claude Code stays signed in with it. Copies in the `backups` folder are not deleted by this; they are rotated out after the newest 10 are kept. See `delete_profile` in `src/claude_accounts_core.py`.

## Does it work with the Claude desktop app or claude.ai?

No. CCSwitcherX manages only Claude Code's login. It reads and writes only `~/.claude/.credentials.json` and `~/.claude.json`, and nothing in the code touches the Claude desktop app or claude.ai. Those keep their own sessions. A switch never runs a logout, so it does not sign you out of anything else.

## How do I run the tests safely?

Run `powershell -ExecutionPolicy Bypass -File scripts\test.ps1` from the repository root. The engine tests set `CA_CLAUDE_DIR`, `CA_STORE` and `CA_NO_NETWORK` to temporary folders and fake accounts before the engine is imported, so they never read or write your real login and make no network call. The extension tests use a temporary sandbox and a mocked VS Code API, and open no window. See [CONTRIBUTING.md](../CONTRIBUTING.md) for doing this by hand.
