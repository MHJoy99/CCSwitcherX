## What this changes

<!-- A few sentences: what, and why. Name the files or functions you touched, for example `src/claude_accounts_core.py: apply_login`. -->

## How it was tested

<!-- Commands you ran and what they showed. For a change to switching, saved logins or usage, say which sandbox you used (CA_CLAUDE_DIR and CA_STORE pointing to a temporary folder). -->

## Checklist

- [ ] `powershell -ExecutionPolicy Bypass -File scripts	est.ps1` passes on my machine.
- [ ] No secrets: no tokens, no `.dat` files, no real `.credentials.json` or `.claude.json`, and no real email addresses in code, tests, fixtures, docs or screenshots.
- [ ] Docs are updated where behaviour changed: `docs/ARCHITECTURE.md`, `docs/FAQ.md`, or `CONTRIBUTING.md`. Or this change does not change behaviour.
- [ ] No new dependencies, and the standard library is still the only Python dependency.
- [ ] If an SVG or the website changed: `python -I scripts/check-svg.py` passes, and I looked at a rendered PNG.
- [ ] I did not add GitHub Actions workflows or other CI automation.
