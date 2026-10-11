"""Claude Accounts: switch the Claude Code login between saved accounts.

No arguments opens the window. Everything else is for scripts, Claude itself and the VS Code extension
(add --json for machine-readable output):

  state | list | status | save [NAME] | switch NAME | settle NAME | rename NAME NEW | remove NAME
  add [--cancel-file F] | doctor | recover
"""
import argparse
import ctypes
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `python -I claude_accounts.py` works too
import claude_accounts_core as core  # noqa: E402


def build_parser():
    ap = argparse.ArgumentParser(prog="claude_accounts", description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", action="version", version=f"CCSwitcherX {core.__version__}")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("gui", "list", "status", "state", "doctor", "recover"):
        sub.add_parser(name)
    usage = sub.add_parser("usage")
    usage.add_argument("--force", action="store_true", help="ignore the minimum gap between requests")
    save = sub.add_parser("save")
    save.add_argument("name", nargs="?")
    for name in ("switch", "remove", "settle"):
        sub.add_parser(name).add_argument("name")
    rename = sub.add_parser("rename")
    rename.add_argument("name")
    rename.add_argument("new_name")
    add = sub.add_parser("add")
    add.add_argument("--cancel-file", help="the sign-in is aborted when this file appears")
    return ap


def run_gui():
    mutex = "Local\\ClaudeAccountsGUI-" + hashlib.sha1(str(core.STORE).encode()).hexdigest()[:8]
    keep = ctypes.windll.kernel32.CreateMutexW(None, False, mutex)  # noqa: F841 (handle must stay alive)
    if ctypes.windll.kernel32.GetLastError() == 183:  # already open
        return 0
    from claude_accounts_gui import App  # imported late so the command line never loads tkinter
    App().run()
    return 0


def main(argv):
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "gui"):
        return run_gui()

    def out(obj, text=None):
        if args.json:
            print(json.dumps(obj))
        else:
            print(text if text is not None else json.dumps(obj, indent=2))

    def find(name):
        target = core.find_profile(name)
        if not target:
            raise core.SwitchError(f'No saved account named "{name}". Try the list command.')
        return target

    try:
        if args.cmd != "recover":
            core.recover()  # finish any interrupted switch before doing anything else

        if args.cmd == "state":
            out(core.state())

        elif args.cmd == "list":
            st = core.state()
            lines = [f'{"*" if p["active"] else " "} {p["name"]:<16} {p["email"]:<34} {p["detail"]}'
                     for p in st["profiles"]]
            if st["unsaved"]:
                lines.append(f'! (not saved)       {st["unsaved"]["email"]:<34} {st["unsaved"]["detail"]}')
            out(st, "\n".join(lines) or "No accounts saved.")

        elif args.cmd == "status":
            st = core.state()
            now = next((p for p in st["profiles"] if p["active"]), None)
            if now:
                text = f'{now["name"]}: {now["email"]} · {now["detail"]}'
            else:
                text = "Signed in, but this login is not saved." if st["signedIn"] else "Not signed in."
            out({"active": now, "unsaved": st["unsaved"], "signedIn": st["signedIn"]}, text)

        elif args.cmd == "save":
            live = core.read_live()
            if not live:
                raise core.SwitchError("Not signed in, nothing to save.")
            saved = core.save_profile(live, args.name)
            out({"ok": True, "id": saved["id"], "name": saved["name"]}, f'Saved "{saved["name"]}".')

        elif args.cmd == "switch":
            target = find(args.name)
            res = core.switch_to(target["id"])
            res.update(ok=True, id=target["id"])
            if args.json or not res["changed"]:
                out(res, f'"{res["name"]}" is already active.')
            else:  # people at a terminal get the confirmation too
                stuck = core.settle_check(target["id"])
                confirmed = core.verify_active(target["oauthAccount"]) if stuck else False
                print(f'Now using "{res["name"]}"' + {True: " (verified).", False: " (NOT confirmed).",
                                                      None: "."}[confirmed])

        elif args.cmd == "settle":
            target = find(args.name)
            stuck = core.settle_check(target["id"])
            verified = core.verify_active(target["oauthAccount"]) if stuck else False
            out({"ok": stuck, "verified": verified}, f"stuck={stuck} verified={verified}")

        elif args.cmd == "rename":
            target = find(args.name)
            core.rename_profile(target["id"], args.new_name)
            out({"ok": True, "name": args.new_name.strip()}, f'Renamed to "{args.new_name.strip()}".')

        elif args.cmd == "remove":
            target = find(args.name)
            core.delete_profile(target["id"])
            out({"ok": True}, f'Removed "{target["name"]}".')

        elif args.cmd == "add":
            cancel_file = args.cancel_file
            res = core.add_login(cancelled=lambda: bool(cancel_file) and os.path.exists(cancel_file))
            if cancel_file and os.path.exists(cancel_file):
                os.unlink(cancel_file)
            res["ok"] = res["status"] in ("new", "known", "same")
            out(res, f'{res["status"]}: {res.get("name", "")}'.strip())

        elif args.cmd == "usage":
            report = core.refresh_usage(force=args.force)
            profiles = core.state()["profiles"]
            lines = []
            for p in profiles:
                u = p["usage"]
                body = "no usage known yet" if not u else \
                    u["text"] + (f' ({u["resets"]})' if u["resets"] else "") + (f' [{u["note"]}]' if u["note"] else "")
                lines.append(f'{"*" if p["active"] else " "} {p["name"]:<16} {body}')
            out({"ok": True, "report": report, "usage": {p["id"]: p["usage"] for p in profiles}},
                "\n".join(lines) or "No accounts saved.")

        elif args.cmd == "doctor":
            checks = core.doctor()
            out({"ok": all(c["ok"] for c in checks), "checks": checks},
                "\n".join(f'{"ok  " if c["ok"] else "FAIL"} {c["name"]}: {c["detail"]}' for c in checks))

        elif args.cmd == "recover":
            name = core.recover()
            out({"ok": True, "recovered": name},
                f'Finished interrupted switch to "{name}".' if name else "Nothing to recover.")

    except core.SwitchError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
