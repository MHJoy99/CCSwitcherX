"""Smoke test for the Claude Accounts window. Sandbox only: never touches the real logins or settings.
The Tk root is withdrawn right away, so nothing is shown on screen. Skipped when there is no display."""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SANDBOX = Path(tempfile.mkdtemp(prefix="ca-gui-test-"))
os.environ["CA_CLAUDE_DIR"] = str(SANDBOX / "claude")
os.environ["CA_STORE"] = str(SANDBOX / "store")
os.environ["CA_NO_NETWORK"] = "1"
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tkinter as tk  # noqa: E402

import claude_accounts_core as core  # noqa: E402
import claude_accounts_gui as gui  # noqa: E402
import claude_accounts_providers as prov  # noqa: E402

KEY = "sk-gui-SECRET-9876543210"


def acct(n, kind="claude_max"):
    return {"accountUuid": f"00000000-0000-0000-0000-{n:012d}", "organizationUuid": f"1111{n:04d}-0000-0000-0000-000000000000",
            "emailAddress": f"user{n}@example.invalid", "organizationType": kind, "organizationName": "Org",
            "organizationRateLimitTier": "default_claude_max_5x", "displayName": f"Fake {n}"}


def oauth(tag, days=25):
    now = time.time() * 1000
    return {"accessToken": f"fake-at-{tag}", "refreshToken": f"fake-rt-{tag}", "expiresAt": now + 6 * 3600_000,
            "refreshTokenExpiresAt": now + days * 86400_000, "scopes": ["user:inference"], "subscriptionType": "max"}


def entry(n, kind="claude_max"):
    return {"claudeAiOauth": oauth(n), "oauthAccount": acct(n, kind)}


def set_live(n):
    e = entry(n)
    core.CREDS.write_text(json.dumps({"claudeAiOauth": e["claudeAiOauth"]}), encoding="utf-8")
    core.CLAUDE_JSON.write_text(json.dumps({"oauthAccount": e["oauthAccount"]}), encoding="utf-8")


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def texts(widget):
    return [w.cget("text") for w in walk(widget) if isinstance(w, tk.Label) and w.cget("text")]


def buttons(widget, text):
    return [w for w in walk(widget) if isinstance(w, gui.Btn) and w.cget("text") == text]


def plain(label):
    return label.replace(" ", "")


class GuiTests(unittest.TestCase):
    def setUp(self):
        for folder in (core.STORE, core.CLAUDE_DIR):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.root.withdraw()
        self.addCleanup(self.close)
        self.gate = None
        for n, kind in ((1, "claude_max"), (2, "claude_team")):
            core.save_profile(entry(n, kind), ["Personal", "Team"][n - 1])
        set_live(1)
        self.a = prov.add_provider("BDX Muse", "https://gpt.example.com", KEY,
                                   {"opus": "go-muse-spark-1.3-contributor", "sonnet": "go-muse-spark-1.3-contributor",
                                    "haiku": "gemini-3.8-flash-tiered"})
        self.b = prov.add_provider("Local Gateway", "http://localhost:4000", "key-two",
                                   {"opus": "big", "sonnet": "mid", "haiku": "small", "subagent": "tiny"})
        self.app = gui.App(self.root)

    def close(self):
        if self.gate:
            self.gate.set()
        try:
            for child in self.root.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(after_id)   # the app's timers must not fire into a later test
            self.root.destroy()
        except tk.TclError:
            pass

    # ---- helpers
    def pump(self, cond=lambda: True, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def settings(self):
        return json.loads(prov.SETTINGS.read_text(encoding="utf-8"))

    def route(self):
        return prov.current_route()

    def refresh(self):
        self.app.refresh(force=True)
        self.root.update_idletasks()

    def in_use_badges(self):
        return [t for t in texts(self.app.list_host) if t == "IN USE"]

    def card_of(self, name):
        for w in walk(self.app.list_host):
            if isinstance(w, tk.Label) and w.cget("text") == name and str(w.cget("font")).find("bold") >= 0:
                return w.master
        self.fail(f"no card named {name}")

    # ---- layout
    def test_layout_labels_badges_and_switch_buttons(self):
        prov.activate(self.a["id"])
        self.refresh()
        host = self.app.list_host
        labels = [plain(t) for t in texts(host)]
        self.assertIn("ACCOUNTS", labels)
        self.assertIn("PROVIDERS", labels)
        for name in ("Personal", "Team", "BDX Muse", "Local Gateway"):
            self.assertIn(name, labels)
        self.assertEqual(len(self.in_use_badges()), 1)
        self.assertTrue([t for t in texts(self.card_of("BDX Muse")) if t == "IN USE"])
        # both accounts offer Switch (the live one only leaves the provider), plus the idle provider
        self.assertEqual(len(buttons(self.card_of("Personal"), "Switch")), 1)
        self.assertEqual(len(buttons(self.card_of("Team"), "Switch")), 1)
        self.assertEqual(len(buttons(self.card_of("Local Gateway"), "Switch")), 1)
        self.assertEqual(len(buttons(self.card_of("BDX Muse"), "Switch")), 0)
        self.assertTrue(any("Using BDX Muse (provider)" in t for t in texts(self.app.banner_host)))
        # model map grouped by shared model, host without scheme
        card_texts = texts(self.card_of("BDX Muse"))
        self.assertIn("gpt.example.com", card_texts)
        self.assertIn("Opus, Sonnet → go-muse-spark-1.3-contributor", card_texts)
        self.assertIn("Haiku, subagents → gemini-3.8-flash-tiered", card_texts)
        # back on the account: Personal is IN USE, still exactly one badge
        prov.deactivate()
        self.refresh()
        self.assertEqual(len(self.in_use_badges()), 1)
        self.assertTrue([t for t in texts(self.card_of("Personal")) if t == "IN USE"])
        self.assertEqual(self.app.banner_host.winfo_children(), [])
        self.assertEqual(len(buttons(self.app.list_host, "Switch")), 3)

    def test_no_providers_shows_hint(self):
        prov.delete_provider(self.a["id"])
        prov.delete_provider(self.b["id"])
        self.refresh()
        self.assertIn("Use other models, for example your own gateway. Click Add provider.", texts(self.app.list_host))
        self.assertIn("PROVIDERS", [plain(t) for t in texts(self.app.list_host)])

    def test_banners_missing_and_custom(self):
        prov.activate(self.a["id"])
        (prov.PROVIDERS / f'{self.a["id"]}.dat').unlink()
        self.refresh()
        self.assertIn("The provider in use was removed.", texts(self.app.banner_host))
        back = buttons(self.app.banner_host, "Switch back")
        self.assertEqual(len(back), 1)
        back[0].command()
        self.assertTrue(self.pump(lambda: not self.app.busy and self.route()["kind"] == "account"))
        self.assertNotIn("ANTHROPIC_BASE_URL", self.settings().get("env", {}))
        prov.SETTINGS.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example.com"}}))
        self.refresh()
        self.assertIn("Claude Code is pointed at https://elsewhere.example.com, set outside this app.",
                      texts(self.app.banner_host))

    def test_poll_detects_outside_change(self):
        self.refresh()
        self.assertEqual(len(self.in_use_badges()), 1)
        prov.activate(self.b["id"])   # as if the command line did it
        self.app.refresh()            # no force: the signature must have changed
        self.assertTrue([t for t in texts(self.card_of("Local Gateway")) if t == "IN USE"])

    def test_long_model_names_are_shortened(self):
        long_name = "vendor/some-very-long-model-name-with-many-parts-and-versions-2026-10-12-preview-extended-context"
        prov.update_provider(self.b["id"], models={"opus": long_name, "sonnet": long_name, "haiku": "small"})
        self.refresh()
        lines = [t for t in texts(self.card_of("Local Gateway")) if "→" in t]
        self.assertTrue(any(t.endswith("…") for t in lines), lines)
        small = self.app.font(9)
        self.assertTrue(all(small.measure(t) <= gui.px(366) for t in lines))

    def test_helpers(self):
        self.assertEqual(gui.model_groups({"opus": "a", "sonnet": "a", "haiku": "b", "subagent": "b"}),
                         [("Opus, Sonnet", "a"), ("Haiku, subagents", "b")])
        self.assertEqual(gui.model_groups({"opus": "a", "sonnet": "b", "haiku": "c", "subagent": "d"})[3], ("subagents", "d"))
        self.assertEqual(gui.model_groups({"opus": "a", "sonnet": "a", "haiku": "a", "subagent": "a"}), [("All models", "a")])

    # ---- sizing
    def test_footer_fits_and_height_is_limited(self):
        self.root.update_idletasks()
        footer_w = sum(b.winfo_reqwidth() for b in (self.app.add_btn, self.app.provider_btn, self.app.usage_btn)) + 2 * gui.px(8)
        self.assertLessEqual(footer_w, gui.px(456))
        width = self.root.winfo_reqwidth()
        for n in range(3, 9):
            core.save_profile(entry(n), f"Account {n}")
        for n in range(4):
            prov.add_provider(f"Extra {n}", f"https://extra{n}.example.com", "k", {"opus": "o", "sonnet": "s", "haiku": "h"})
        self.refresh()
        cards = [w for w in self.app.list_host.winfo_children() if isinstance(w, tk.Frame)]
        self.assertGreaterEqual(len(cards), 12)
        screen = self.root.winfo_screenheight()
        self.assertLessEqual(self.root.winfo_reqheight(), 0.8 * screen)
        # on a short screen the list scrolls instead
        with mock.patch.object(self.app, "max_height", return_value=600):
            self.refresh()
            self.assertTrue(self.app.scrollable)
            self.assertLessEqual(self.root.winfo_reqheight(), 600)
            self.assertEqual(self.root.winfo_reqwidth(), width)
            self.app.on_wheel(mock.Mock(widget=self.app.list_host, delta=-120))
            self.assertGreater(self.app.list_canvas.yview()[0], 0)
            moved = self.app.list_canvas.yview()[0]
            self.refresh()   # a re-render keeps the scroll position
            self.assertAlmostEqual(self.app.list_canvas.yview()[0], moved, places=2)
        self.refresh()
        self.root.update_idletasks()
        self.assertLessEqual(self.root.winfo_reqheight(), 0.8 * screen)

    def test_few_cards_do_not_scroll(self):
        self.refresh()
        self.assertFalse(self.app.scrollable)

    # ---- provider dialog
    def fill(self, dlg, name="New One", url="https://new.example.com/v1", key=KEY, opus="o1", sonnet="s1", haiku="h1", sub=""):
        for entry_, value in ((dlg.name, name), (dlg.url, url), (dlg.key, key), (dlg.models["opus"], opus),
                              (dlg.models["sonnet"], sonnet), (dlg.models["haiku"], haiku), (dlg.models["subagent"], sub)):
            entry_.put(value)

    def test_dialog_validation_keeps_dialog_open(self):
        before = len(prov.list_providers())
        dlg = gui.ProviderDialog(self.app)
        dlg.save()                                                    # everything empty
        self.assertTrue(dlg.error.cget("text"))
        self.fill(dlg, url="not a url")
        dlg.save()
        self.assertIn("address", dlg.error.cget("text").lower())
        self.fill(dlg, key="")
        dlg.save()
        self.assertIn("API key", dlg.error.cget("text"))
        self.fill(dlg, opus="")
        dlg.save()
        self.assertIn("Opus", dlg.error.cget("text"))
        self.fill(dlg, name="BDX Muse")
        dlg.save()
        self.assertIn("already a provider", dlg.error.cget("text"))
        self.assertTrue(dlg.alive())
        self.assertEqual(len(prov.list_providers()), before)
        self.fill(dlg)                                                # valid, with a pasted /v1
        dlg.save()
        self.assertFalse(dlg.alive())
        saved = next(p for p in prov.list_providers() if p["name"] == "New One")
        self.assertEqual(saved["baseUrl"], "https://new.example.com")
        self.assertEqual(saved["models"]["subagent"], "h1")
        self.assertIn("New One", [plain(t) for t in texts(self.app.list_host)])

    def test_dialog_edit_keeps_key_and_masks_it(self):
        dlg = gui.ProviderDialog(self.app, {**self.a, "active": False})
        self.assertEqual(dlg.name.value(), "BDX Muse")
        self.assertEqual(dlg.key.value(), "")
        self.assertEqual(dlg.key.get(), "saved, leave blank to keep")
        dlg.key.put("typed-secret")
        self.assertEqual(dlg.key.cget("show"), "•")
        dlg.toggle_key()
        self.assertEqual(dlg.key.cget("show"), "")
        dlg.key.put("")
        dlg.models["opus"].put("changed-model")
        dlg.save()
        self.assertFalse(dlg.alive())
        got = prov.get_provider(self.a["id"])
        self.assertEqual(got["apiKey"], KEY)              # blank key keeps the saved one
        self.assertEqual(got["models"]["opus"], "changed-model")

    def test_dialog_test_connection_shows_results_and_never_the_key(self):
        calls = []

        def fake(base_url, key, model, deep=True):
            calls.append((base_url, key, model, deep))
            if model == "bad-model":
                return {"model": model, "ok": False, "seconds": 0.4, "detail": "HTTP 404: model not found"}
            return {"model": model, "ok": True, "seconds": 3.2, "detail": "tool use and follow-up step work"}

        with mock.patch.object(prov, "check_model", fake):
            dlg = gui.ProviderDialog(self.app, {**self.a, "active": False})       # editing: key field blank
            dlg.models["opus"].put("good-model")
            dlg.models["sonnet"].put("good-model")
            dlg.models["haiku"].put("bad-model")
            dlg.test()
            self.assertTrue(self.pump(lambda: not dlg.testing))
            lines = texts(dlg.results)
            self.assertEqual(lines, ["✓  good-model   3.2 s   tool use and follow-up step work",
                                     "✗  bad-model   HTTP 404: model not found"])
            colors = [w.cget("fg") for w in dlg.results.winfo_children()]
            self.assertEqual(colors, [gui.PAL["good"], gui.PAL["bad"]])
            self.assertEqual([(c[2], c[3]) for c in calls], [("good-model", True), ("bad-model", True)])
            self.assertTrue(all(c[1] == KEY and c[0] == "https://gpt.example.com" for c in calls))   # saved key used
            self.assertNotIn(KEY, " ".join(texts(dlg.top)))
            dlg.top.destroy()

    def test_dialog_save_stays_usable_while_testing(self):
        self.gate = gate = threading.Event()

        def slow(base_url, key, model, deep=True):
            gate.wait(10)
            return {"model": model, "ok": True, "seconds": 1.0, "detail": "answers"}

        with mock.patch.object(prov, "check_model", slow):
            dlg = gui.ProviderDialog(self.app)
            self.fill(dlg)
            dlg.test()
            self.assertTrue(dlg.testing)
            self.assertFalse(dlg.test_btn.enabled)
            dlg.save()
            self.assertFalse(dlg.alive())
            self.assertIn("New One", [p["name"] for p in prov.list_providers()])
            gate.set()
            self.pump(timeout=0.5)

    def test_dialog_test_needs_valid_values(self):
        dlg = gui.ProviderDialog(self.app)
        dlg.test()
        self.assertTrue(dlg.error.cget("text"))
        self.assertFalse(dlg.testing)
        dlg.top.destroy()

    # ---- actions
    def test_switch_provider_and_back_changes_settings(self):
        with mock.patch.object(prov, "SETTLE_SECONDS", 0.1):
            self.app.switch_provider(self.a["id"])
            self.assertTrue(self.pump(lambda: not self.app.busy and self.route()["kind"] == "provider"))
            env = self.settings()["env"]
            self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://gpt.example.com")
            self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "gemini-3.8-flash-tiered")
            self.assertNotIn(KEY, prov.SETTINGS.read_text(encoding="utf-8"))
            # SETTLE_SECONDS is 0.1 here, so the "Ready" line may already have replaced the first message
            self.assertTrue(self.app.status.cget("text").startswith(("Now using BDX Muse. Chats you already have open",
                                                                     "Ready: your next message uses BDX Muse.")))
            self.assertTrue(self.pump(lambda: self.app.status.cget("text") == "Ready: your next message uses BDX Muse."))
            self.assertTrue([t for t in texts(self.card_of("BDX Muse")) if t == "IN USE"])
            # account Switch on the live account: only leaves the provider, no confirm flow
            with mock.patch.object(self.app, "confirm_in_background") as confirm:
                self.app.switch(core.profile_id(acct(1)))
                self.assertTrue(self.pump(lambda: not self.app.busy and self.route()["kind"] == "account"))
                confirm.assert_not_called()
            self.assertEqual(self.app.status.cget("text"), "Back on Personal. Your next message uses it.")
            self.assertNotIn("ANTHROPIC_BASE_URL", self.settings().get("env", {}))
            # provider again, then a different account: changes the login and runs the confirm flow
            self.app.switch_provider(self.b["id"])
            self.assertTrue(self.pump(lambda: not self.app.busy and self.route()["kind"] == "provider"))
            with mock.patch.object(self.app, "confirm_in_background") as confirm:
                self.app.switch(core.profile_id(acct(2, "claude_team")))
                self.assertTrue(self.pump(lambda: not self.app.busy and self.route()["kind"] == "account"))
                confirm.assert_called_once()
            self.assertEqual(core.read_live()["oauthAccount"]["emailAddress"], "user2@example.invalid")

    def test_remove_provider_confirms_and_leaves_it_first(self):
        prov.activate(self.a["id"])
        self.refresh()
        with mock.patch.object(gui.messagebox, "askyesno", return_value=False) as ask:
            self.app.remove_provider(self.a["id"], "BDX Muse", True)
            self.assertIn('Remove provider "BDX Muse"? Its saved key is deleted.', ask.call_args[0][1])
            self.assertIn("switches back to your Claude account first", ask.call_args[0][1])
        self.assertIsNotNone(prov.get_provider(self.a["id"]))
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
            self.app.remove_provider(self.a["id"], "BDX Muse", True)
        self.assertIsNone(prov.get_provider(self.a["id"]))
        self.assertEqual(self.route()["kind"], "account")
        self.assertNotIn("BDX Muse", [plain(t) for t in texts(self.app.list_host)])

    def test_card_menu_test_connection_shows_results_dialog(self):
        def fake(base_url, key, model, deep=True):
            ok = model != "gemini-3.8-flash-tiered"
            return {"model": model, "ok": ok, "seconds": 2.5, "detail": "tool use and follow-up step work" if ok else "HTTP 500: boom"}

        with mock.patch.object(prov, "check_model", fake):
            self.app.check_provider(self.a["id"], "BDX Muse")
            self.assertEqual(self.app.status.cget("text"), "Testing BDX Muse…")
            tops = lambda: [c for c in self.root.winfo_children() if isinstance(c, tk.Toplevel)]  # noqa: E731
            self.assertTrue(self.pump(lambda: bool(tops())))
            lines = texts(tops()[0])
            self.assertIn("✓  go-muse-spark-1.3-contributor   2.5 s   tool use and follow-up step work", lines)
            self.assertIn("✗  gemini-3.8-flash-tiered   HTTP 500: boom", lines)
            self.assertIn("Used for Opus, Sonnet", lines)
            self.assertIn("Used for Haiku, subagents", lines)
            self.assertNotIn(KEY, " ".join(lines))
            self.assertIn("did not pass", self.app.status.cget("text"))
            tops()[0].destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
