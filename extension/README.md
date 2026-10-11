# CCSwitcherX / Claude Accounts

A Windows tool that switches the Claude Code login between your own saved accounts, for example Personal and Team.

- The status bar shows the active account and its session usage; click it to switch, add, rename or remove an account.
- Switching swaps only the login. Skills, memory, CLAUDE.md, MCP logins and chat history stay shared.
- The engine lives in ~/.claude-accounts (`claude_accounts.py`); this extension only shows state and asks it to act. No secrets pass through the extension.

## Providers

A provider is another model gateway (its address, an API key and which model answers for Opus, Sonnet, Haiku and subagents).

- While one is in use the status bar shows a plug icon and its name, for example `BDX Muse`, and the tooltip says which models answer. Your Claude subscription is not used until you switch back. A `Custom URL` warning means Claude Code is pointed at an address that was set outside this app.
- Click the status bar: your accounts come first, then a **Providers** group. Pick a provider to use it, or pick an account to switch back. **Test provider…** checks each model of a provider with a real request and reports one line per model.
- Providers are added and edited in the Claude Accounts window (**Add provider…** opens it), because the address, key and model map need a proper form.
- A switch takes effect from your next message in Claude Code (about two seconds after you pick it). No restart is needed.

Docs and source: https://github.com/MHJoy99/CCSwitcherX
