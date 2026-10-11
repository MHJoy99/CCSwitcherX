# Contributing to CCSwitcherX

Thanks for looking at CCSwitcherX. It is a small Windows tool with no third-party dependencies, so most changes are easy to review. Please read the safety notes below before you run anything: several commands read and write the Claude Code login on your machine.

## Dev setup

You need:

- Windows 10 or 11
- Python 3.9 or newer, installed with **tcl/tk and IDLE** ticked (the window uses tkinter). The project is tested on Python 3.11.
- Windows PowerShell 5.1 (built in) or PowerShell 7 for the scripts in `scripts/`
- Node.js, only if you want to run the extension tests or build the VS Code package. Without it, `scripts/test.ps1` skips the extension tests and says so.
- Git

Clone and look around:

```powershell
git clone https://github.com/MHJoy99/CCSwitcherX
cd CCSwitcherX
```

Layout:

| Path | What is in it |
|---|---|
| `src/claude_accounts_core.py` | The engine: encryption, saved accounts, switching, usage, doctor |
| `src/claude_accounts.py` | The command line, which the VS Code extension also calls |
| `src/claude_accounts_gui.py` | The Claude Accounts window (tkinter) |
| `extension/` | The VS Code status-bar extension (`extension.js`, `package.json`) |
| `tests/` | Engine tests, extension tests, and the sandbox helper |
| `scripts/` | Install, uninstall, test, build, and asset helpers |
| `docs/` | The website, the demo, and the documentation pages |

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the pieces fit together, file by file.

## Work without touching your real login

The engine reads two environment variables when it is imported. Set them before you run any command, and every path the engine uses moves into a folder you choose:

| Variable | Effect |
|---|---|
| `CA_CLAUDE_DIR` | Replaces `~/.claude` as Claude Code's folder. The account record then lives at `<CA_CLAUDE_DIR>\.claude.json`. (`CLAUDE_CONFIG_DIR` is also honoured when `CA_CLAUDE_DIR` is not set.) |
| `CA_STORE` | Replaces `~/.claude-accounts` as the app folder: saved logins, backups, `usage.json`, and the journal |
| `CA_NO_NETWORK` | When set to a non-empty value, the usage request is never sent. The engine fails at once and treats that as an error (`_fetch_usage`). |

Example for trying the CLI on fake accounts:

```powershell
$sb = Join-Path $env:TEMP 'ca-dev'
$env:CA_CLAUDE_DIR = Join-Path $sb 'claude'
$env:CA_STORE      = Join-Path $sb 'store'
$env:CA_NO_NETWORK = '1'
python -I tests\make_sandbox.py $sb          # creates fake "Personal" and "Team" accounts in the sandbox
python -I src\claude_accounts.py list
python -I src\claude_accounts.py doctor
```

`tests/make_sandbox.py` writes fake tokens and example addresses only. Pass a second argument, `with-expiring`, to add a third account whose sign-in expires in 3 days.

**Warning:** if you run `src\claude_accounts.py` (or the window) without these variables, it works on your real `~/.claude` and `~/.claude-accounts`. Even `list`, `status` and `doctor` write to that folder: `state` refreshes the saved copy of the active login, and `doctor` writes its cache file. `switch`, `add`, `save`, `remove`, `rename` and `recover` change your real login or saved accounts. Do not run any of them without the sandbox set.

## Running the tests

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

This runs the engine tests (`tests/test_core.py`, which makes its own temporary folders before it imports the engine) and, if Node.js is installed, the extension tests (`tests/test_extension.js`). The extension tests run the real `extension/extension.js` against a mocked VS Code API and a sandbox made by `tests/make_sandbox.py`. No window opens. The script ends with `ALL TESTS PASSED` or `TESTS FAILED`.

To run one suite on its own:

```powershell
python -I tests\test_core.py
```

Running the extension tests by hand needs the sandbox variables from the section above, and Python on `PATH`, because the extension calls the engine as a subprocess.

## Code style

- **Match the code around your change.** Names, layout, comments and error wording should look like the rest of the file. Read the neighbouring functions first.
- **Standard library only.** Python code uses only the standard library. The extension has no `dependencies` in `extension/package.json`, and it should stay that way. Do not add a package, a bundler or a build step without a discussion first.
- **Windows-first and explicit.** The engine uses `ctypes` for DPAPI and for the Windows calls it needs. Keep those calls in the places that already use them.
- **Messages for people.** A `SwitchError` text is shown to users as it is, so write it as a short, plain sentence that says what to do.
- **Never print or log a token.** Logs and JSON output carry names, emails and percentages only.
- **Keep the data formats.** `usage.json`, the saved `.dat` files, and the `--json` output are read by other parts of the program (and by the extension). Change them only together with the code that reads them, and say so in the pull request.
- Comments explain why, not what. The lock protocol comments in `claude_accounts_core.py` are an example.

## Pull request checklist

Before you open a pull request, check these items. The template in `.github/PULL_REQUEST_TEMPLATE.md` repeats them.

- [ ] `powershell -ExecutionPolicy Bypass -File scripts\test.ps1` passes on your machine.
- [ ] No secrets: no tokens, no `.dat` files, no `.credentials.json` or `.claude.json` from a real login, no real email addresses in code, tests, fixtures or screenshots. Test data uses `example.com`, `example.invalid` or the names `Personal` and `Team`.
- [ ] Docs are updated where behaviour changed: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the switch, storage, usage or `--json` contract; [docs/FAQ.md](docs/FAQ.md) for questions users ask; this file for process.
- [ ] If you changed an SVG or the website, `python -I scripts/check-svg.py` passes and you looked at a rendered PNG (see below).
- [ ] No new dependencies, and no new files that are not needed.
- [ ] No GitHub Actions workflow or other automation added (see the next section).

## No CI and no workflows

This repository does not use GitHub Actions or any other CI, and pull requests must not add `.github/workflows/` files, scheduled jobs, or automatic dependency-update configuration. Run the tests on your own machine with `powershell -ExecutionPolicy Bypass -File scripts\test.ps1`, and attach the result to your pull request when that helps a reviewer.

## Rendering and checking assets

| Task | Command |
|---|---|
| Check an SVG for safety and README use | `python -I scripts/check-svg.py docs/assets` (a file or a folder). Prints `ok`, `warn` or `FAIL` for each file, and exits with 1 when any file has an error. Warnings do not fail the run. |
| Render an HTML or SVG page to PNG | `pwsh scripts/shot.ps1 <file> <out.png> -Width 1280 -Height 720 -WaitMs 2500`. It uses headless Microsoft Edge and shows nothing on screen. Add `-Inline` to let an SVG's animation advance to `-WaitMs`, `-Dark` for dark mode, or `-Bg '#0d1117'` for a dark page behind an SVG. |
| Check the website files | `python -I scripts/check-site.py` (see the header of that script for what it checks) |
| Build the VS Code package | `powershell -ExecutionPolicy Bypass -File scripts\build-vsix.ps1` (needs Node.js; writes `dist/claude-accounts-<version>.vsix`) |
| Install on your machine | `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`. It copies only the program files into `~/.claude-accounts`, so your saved logins are kept. |

Look at what you render. A check that passes does not show whether text is cut off or a layout is ugly.

## Reporting bugs and security problems

- Bugs and feature ideas: open an issue with the forms in `.github/ISSUE_TEMPLATE/`. Include the output of `python claude_accounts.py doctor` with email addresses removed.
- Security problems: do not open a public issue. Use a private security advisory, as described in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is released under the MIT licence in [LICENSE](LICENSE). Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).
