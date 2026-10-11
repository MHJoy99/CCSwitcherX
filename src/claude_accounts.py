"""Claude Accounts: switch the Claude Code login between saved accounts.

No arguments opens the window. Everything else is for scripts, Claude itself and the VS Code extension
(add --json for machine-readable output):

  state | list | status | save [NAME] | switch NAME | settle NAME | rename NAME NEW | remove NAME
  add [--cancel-file F] | doctor | recover

Providers (another model gateway: custom URL + key + model map):
  provider list | add NAME --url U (--key-env VAR | --key-stdin) --opus M --sonnet M --haiku M [--subagent M]
  provider edit NAME [--rename N] [--url U] [--key-env VAR | --key-stdin] [--opus M] ... | remove NAME
  provider use NAME | off | test NAME [--quick]
"""
import argparse
import ctypes
import hashlib
import json
import os
import sys
import time

sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))  # so `python -I claude_accounts.py` works too
import claude_accounts_core as core  # noqa: E402
import claude_accounts_providers as providers  # noqa: E402


def _provider_fields(p, required):
    p.add_argument("--url", required=required, help="gateway address, e.g. https://gpt.bdx.market")
    keys = p.add_mutually_exclusive_group(required=required)
    keys.add_argument("--key-env", help="read the API key from this environment variable")
    keys.add_argument("--key-stdin", action="store_true", help="read the API key from the first line of standard input")
    for tier in ("opus", "sonnet", "haiku", "subagent"):
        p.add_argument(f"--{tier}", required=required and tier != "subagent", help=f"model that answers for {tier}")


def _provider_key(args):
    """The key from --key-env / --key-stdin (never from the command line itself, which others can see)."""
    if args.key_env:
        value = os.environ.get(args.key_env, "").strip()
        if not value:
            raise core.SwitchError(f"The environment variable {args.key_env} is empty or not set.")
        return value
    if args.key_stdin:
        return sys.stdin.readline().strip()
    return None


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
    sub.add_parser("key", help=argparse.SUPPRESS).add_argument("id")   # used by Claude Code as apiKeyHelper
    prov = sub.add_parser("provider", help="another model gateway: custom URL, key and model map")
    psub = prov.add_subparsers(dest="pcmd", required=True)
    psub.add_parser("list")
    padd = psub.add_parser("add")
    padd.add_argument("name")
    _provider_fields(padd, required=True)
    pedit = psub.add_parser("edit")
    pedit.add_argument("name")
    pedit.add_argument("--rename")
    _provider_fields(pedit, required=False)
    for name in ("remove", "use"):
        psub.add_parser(name).add_argument("name")
    psub.add_parser("off")
    ptest = psub.add_parser("test")
    ptest.add_argument("name")
    ptest.add_argument("--quick", action="store_true", help="one plain request per model, no tool check")
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
        if args.cmd == "key":   # Claude Code runs this often and wants only the key on stdout
            if sys.stdout.isatty():
                raise core.SwitchError("This prints a secret and is meant to be run by Claude Code, not typed.")
            sys.stdout.write(providers.key_for(args.id))
            return 0

        if args.cmd != "recover":
            core.recover()  # finish any interrupted switch before doing anything else

        if args.cmd == "state":
            out(providers.full_state())

        elif args.cmd == "list":
            st = providers.full_state()
            lines = [f'{"*" if p["inUse"] else " "} {p["name"]:<16} {p["email"]:<34} {p["detail"]}'
                     for p in st["profiles"]]
            if st["unsaved"]:
                lines.append(f'! (not saved)       {st["unsaved"]["email"]:<34} {st["unsaved"]["detail"]}')
            lines += [f'{"*" if p["active"] else " "} {p["name"]:<16} {p["host"]:<34} provider' for p in st["providers"]]
            out(st, "\n".join(lines) or "No accounts saved.")

        elif args.cmd == "status":
            st = providers.full_state()
            now = next((p for p in st["profiles"] if p["inUse"]), None)
            route = st["route"]
            if route["kind"] == "provider":
                text = f'Provider "{route.get("name") or "unknown"}" ({route.get("baseUrl")}) is in use.'
            elif route["kind"] == "custom":
                text = f'Claude Code is pointed at {route.get("baseUrl")} (set outside this app).'
            elif now:
                text = f'{now["name"]}: {now["email"]} · {now["detail"]}'
            else:
                text = "Signed in, but this login is not saved." if st["signedIn"] else "Not signed in."
            out({"active": now, "unsaved": st["unsaved"], "signedIn": st["signedIn"], "route": route}, text)

        elif args.cmd == "save":
            live = core.read_live()
            if not live:
                raise core.SwitchError("Not signed in, nothing to save.")
            saved = core.save_profile(live, args.name)
            out({"ok": True, "id": saved["id"], "name": saved["name"]}, f'Saved "{saved["name"]}".')

        elif args.cmd == "switch":
            target = find(args.name)
            res = providers.use_account(target["id"])   # also leaves a provider if one was in use
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
            checks = core.doctor() + providers.doctor_checks()
            out({"ok": all(c["ok"] for c in checks), "checks": checks},
                "\n".join(f'{"ok  " if c["ok"] else "FAIL"} {c["name"]}: {c["detail"]}' for c in checks))

        elif args.cmd == "recover":
            name = core.recover()
            out({"ok": True, "recovered": name},
                f'Finished interrupted switch to "{name}".' if name else "Nothing to recover.")

        elif args.cmd == "provider":
            def pfind(name):
                target = providers.find_provider(name)
                if not target:
                    raise core.SwitchError(f'No saved provider named "{name}". Try: provider list')
                return target

            models = lambda a: {t: getattr(a, t) for t in ("opus", "sonnet", "haiku", "subagent")}  # noqa: E731
            if args.pcmd == "list":
                st = providers.full_state()
                lines = [f'{"*" if p["active"] else " "} {p["name"]:<16} {p["host"]:<30} '
                         f'opus={p["models"]["opus"]} sonnet={p["models"]["sonnet"]} haiku={p["models"]["haiku"]}'
                         for p in st["providers"]]
                out({"ok": True, "route": st["route"], "providers": st["providers"]}, "\n".join(lines) or "No providers saved.")

            elif args.pcmd == "add":
                made = providers.add_provider(args.name, args.url, _provider_key(args), models(args))
                out({"ok": True, **made}, f'Saved provider "{made["name"]}" ({made["host"]}). Use it with: provider use "{made["name"]}"')

            elif args.pcmd == "edit":
                target = pfind(args.name)
                changed = {t: v for t, v in models(args).items() if v}
                merged = {**target["models"], **changed} if changed else None
                made = providers.update_provider(target["id"], name=args.rename, base_url=args.url,
                                                 api_key=_provider_key(args), models=merged)
                out({"ok": True, **made}, f'Updated provider "{made["name"]}".')

            elif args.pcmd == "remove":
                target = pfind(args.name)
                providers.delete_provider(target["id"])
                out({"ok": True}, f'Removed provider "{target["name"]}".')

            elif args.pcmd == "use":
                target = pfind(args.name)
                res = providers.activate(target["id"])
                if not args.json:
                    time.sleep(res["settleSeconds"])   # a running Claude Code picks the change up about 1.5 s later
                out({"ok": True, **res}, f'Claude Code now uses "{res["name"]}" from its next message.')

            elif args.pcmd == "off":
                left = providers.deactivate()
                out({"ok": True, "left": left}, f'Left provider "{left}"; Claude Code uses your Claude account again.'
                    if left else "No provider was in use.")

            elif args.pcmd == "test":
                target = pfind(args.name)
                rows = providers.test_provider(target, deep=not args.quick)
                lines = [f'{"ok  " if r["ok"] else "FAIL"} {r["model"]:<34} {r["seconds"]:>5}s  {r["detail"]}' for r in rows]
                out({"ok": all(r["ok"] for r in rows), "results": rows}, "\n".join(lines))
                return 0 if all(r["ok"] for r in rows) else 1

    except core.SwitchError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
