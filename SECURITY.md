# Security Policy

## Supported versions

Only the latest release receives security fixes. The current version is 1.1.0 (`__version__` in `src/claude_accounts_core.py`; `python claude_accounts.py --version` prints it). Update by running `git pull` and then `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` again.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through a GitHub security advisory:

**https://github.com/MHJoy99/CCSwitcherX/security/advisories/new**

Include:

- what you found and where (file and function, if you know them, for example `claude_accounts_core.py: apply_login`);
- steps to reproduce, ideally against fake accounts from `tests/make_sandbox.py`;
- what an attacker would gain, and what they need first (for example, a second user on the same PC).

Please do not include real tokens, real `.dat` files, or your real `.credentials.json` or `.claude.json` in the report. Describe their shape instead, or use the sandbox.

### What to expect

- We will reply through the advisory. No response time is promised.
- We will confirm whether we can reproduce the problem, and talk about a fix and its release.
- Please give us a reasonable time to fix a problem before you publish details. We are happy to credit you in the release notes if you want that.
- If a report turns out not to be a security issue, we will say so and move it to a normal issue if it is a bug.

## Threat model

This section says what CCSwitcherX protects and what it does not. It is written from the code in `src/claude_accounts_core.py`.

### What is on your disk

| Data | Where | Protected? |
|---|---|---|
| The **active** login (OAuth tokens) | `~/.claude/.credentials.json`, in Claude Code's own file | **No.** Claude Code writes it as plain JSON, and CCSwitcherX reads and writes it as plain JSON (`read_live`, `apply_login`). |
| Saved logins for your other accounts | `~/.claude-accounts/profiles/*.dat` | **Yes**, encrypted with Windows DPAPI (`protect`, `unprotect`) |
| Backups of logins that were active before a switch | `~/.claude-accounts/backups/*.dat` | **Yes**, same encryption. The newest 10 are kept. |
| Switch journal | `~/.claude-accounts/switch.journal` | Not encrypted. It holds only a profile id and a time, no tokens. |
| Usage numbers | `~/.claude-accounts/usage.json` | Not encrypted. Percentages and times only, no tokens. |

### What DPAPI protects against

DPAPI is called with the default user scope (`CryptProtectData` with flags 0 and no entropy). The saved `.dat` files and backups can be read only by the Windows user account that wrote them, on that PC. This protects them:

- from other users of the same PC;
- from copies of the app folder moved to another computer, or restored from a backup of files alone;
- from someone who reads the files without logging in as you.

### What DPAPI does not protect against

- **Malware, or any program running as the same Windows user.** Such a program can read `~/.claude/.credentials.json` directly, and it can call the same decryption function that CCSwitcherX uses to read the saved logins. Encryption does not help against code that already runs as you.
- **Someone with access to your unlocked session or your profile folder.** The active login is always in plain text in Claude Code's file, whether or not CCSwitcherX is installed.
- **Cloud sync of your profile folder.** If `~/.claude` or `~/.claude-accounts` is synced to a cloud service, the synced copy of the plain-text active login, and of any file that is not encrypted, goes with it.
- **Old backups.** Copies of earlier logins stay in `backups/` until they are rotated out (the newest 10 are kept). Removing an account does not delete them.
- **The sign-in log.** During Add account, the output of `claude auth login` is written to `~/.claude-accounts/login.log`, in plain text. It is deleted when the sign-in ends, but a hard kill of the process can leave it behind.

### Network

CCSwitcherX has no server and no telemetry. It sends data over the network in two cases only:

1. **Sign-in.** It starts `claude auth login --claudeai`, which is Claude Code's own browser sign-in. Claude Code does the network work.
2. **Usage.** It makes a read-only `GET` to `https://api.anthropic.com/api/oauth/usage` for an account, using that account's own saved access token, and only while the token is still valid. It never refreshes a saved token to do this (`refresh_usage`, `_fetch_usage`). Set `CA_NO_NETWORK` to turn this off.

It does not log tokens, print them, or write them into `usage.json`.

### Things we are not claiming

- We have not had the code audited by a third party.
- Claude Code may change where it stores logins. The self-check (`doctor`) looks for the lock names this tool relies on, but it does not check every storage detail.

## If you think a login has leaked

Act quickly. A stolen refresh token can be used until it expires, so do all of these:

1. **Sign out of that account on claude.ai**, and sign in again on claude.ai with a fresh session.
2. **Sign in again with Add account**, so that the saved login holds new tokens.
3. **Remove the old saved copy** from the window (the ⋯ menu on the card, then Remove from list) and add the account again, if you want a clean start.
4. **Delete the files in `~/.claude-accounts/backups`** if they may contain the leaked login. Those are plain backup files of earlier logins, and deleting them is safe.
5. If the leak is through a program on your PC, deal with that first. Remove it, scan the machine, and then do steps 1 to 4.

If you think the problem is in CCSwitcherX itself rather than your account, report it as described above.

## Disclosure

We ask for coordinated disclosure: report privately, let us fix it, and then publish. We will tell you when a fix is released, and we will credit you if you want credit.
