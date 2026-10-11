"""Sandbox tests for providers (custom gateway URL + key + model map). Never touches the real settings or logins."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="ca-prov-test-"))
os.environ["CA_CLAUDE_DIR"] = str(SANDBOX / "claude")
os.environ["CA_STORE"] = str(SANDBOX / "store")
os.environ["CA_NO_NETWORK"] = "1"
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
import claude_accounts as cli  # noqa: E402
import claude_accounts_core as core  # noqa: E402
import claude_accounts_providers as prov  # noqa: E402

KEY = "sk-test-SECRET-1234567890"
MODELS = {"opus": "muse-x", "sonnet": "muse-x", "haiku": "gem-y"}
BASE = "https://gw.example.com"


def acct(n, kind="claude_max"):
    return {"accountUuid": f"acc-{n}", "organizationUuid": f"org-{n}", "emailAddress": f"u{n}@example.com",
            "organizationType": kind, "organizationName": "Org", "organizationRateLimitTier": "default_claude_max_5x"}


def oauth(tag):
    return {"accessToken": f"at-{tag}", "refreshToken": f"rt-{tag}", "expiresAt": int(time.time() * 1000) + 3600_000,
            "refreshTokenExpiresAt": int(time.time() * 1000) + 20 * 86400_000, "subscriptionType": "max"}


def run_cli(*argv, env=None):
    """Run the in-process CLI with --json; returns (exit code, parsed output)."""
    out, err = io.StringIO(), io.StringIO()
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--json", *argv])
    finally:
        os.environ.clear()
        os.environ.update(old)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else {"error": err.getvalue()})


class ProviderTests(unittest.TestCase):
    def setUp(self):
        for folder in (prov.PROVIDERS, prov.SETTINGS_BACKUPS, core.PROFILES, core.BACKUPS):
            if folder.exists():
                for f in folder.glob("*"):
                    f.unlink()
        prov.ROUTE_FILE.unlink(missing_ok=True)
        prov.SETTINGS.unlink(missing_ok=True)
        core.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        core.JOURNAL.unlink(missing_ok=True)
        for d in (core.CLAUDE_DIR / ".oauth_refresh.lock", core.CLAUDE_DIR.parent / (core.CLAUDE_DIR.name + ".lock"),
                  prov.ROUTE_LOCK):
            if d.is_dir():
                d.rmdir()
        core.CREDS.write_text(json.dumps({"claudeAiOauth": oauth("p1")}))
        core.CLAUDE_JSON.write_text(json.dumps({"oauthAccount": acct(1)}, indent=2))

    # ---- helpers
    def settings(self):
        return json.loads(prov.SETTINGS.read_text(encoding="utf-8"))

    def write_settings(self, obj, indent=2):
        prov.SETTINGS.write_text(json.dumps(obj, indent=indent), encoding="utf-8")

    def make(self, name="BDX Muse", **kw):
        return prov.add_provider(name, kw.get("url", BASE), kw.get("key", KEY), kw.get("models", MODELS))

    # ---- saving
    def test_add_list_hides_key_and_encrypts_it_on_disk(self):
        made = self.make()
        self.assertNotIn("apiKey", made)
        self.assertTrue(made["keyOk"])
        self.assertEqual(made["models"]["subagent"], "gem-y")          # subagents default to the small model
        self.assertFalse(any(KEY.encode() in f.read_bytes() for f in prov.PROVIDERS.glob("*")))
        self.assertEqual(prov.get_provider(made["id"])["apiKey"], KEY)
        self.assertNotIn(KEY, json.dumps(prov.full_state()))

    def test_address_and_model_validation(self):
        self.assertEqual(prov._normalize_url(" https://gw.example.com/v1/ "), "https://gw.example.com")
        self.assertEqual(prov._normalize_url("http://localhost:8080"), "http://localhost:8080")
        for bad in ("http://gw.example.com", "gw.example.com", "https://user:pw@gw.example.com", "https://a b.com",
                    "https://gw.example.com/x?key=1", ""):
            with self.assertRaises(core.SwitchError, msg=bad):
                prov._normalize_url(bad)
        with self.assertRaises(core.SwitchError):
            prov.add_provider("A", BASE, KEY, {"opus": "m", "sonnet": "", "haiku": "h"})
        with self.assertRaises(core.SwitchError):
            prov.add_provider("A", BASE, KEY, {"opus": "bad name", "sonnet": "m", "haiku": "h"})
        with self.assertRaises(core.SwitchError):
            prov.add_provider("A", BASE, "   ", MODELS)
        self.make("Same")
        with self.assertRaises(core.SwitchError):
            self.make("same")                                           # names are unique, case-insensitively

    # ---- using a provider
    def test_activate_writes_only_its_keys_and_never_the_key(self):
        original = {"$schema": "x", "model": "sonnet", "permissions": {"allow": ["Read"]}, "statusLine": {"type": "command"},
                    "env": {"MY_OWN": "1"}}
        self.write_settings(original)
        made = self.make()
        res = prov.activate(made["id"])
        self.assertEqual(res["settleSeconds"], prov.SETTLE_SECONDS)
        now = self.settings()
        self.assertEqual(now["env"]["ANTHROPIC_BASE_URL"], BASE)
        self.assertEqual(now["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "muse-x")
        self.assertEqual(now["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "gem-y")
        self.assertEqual(now["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "gem-y")
        self.assertEqual(now["env"]["MY_OWN"], "1")
        for k in ("$schema", "model", "permissions", "statusLine"):
            self.assertEqual(now[k], original[k])
        self.assertNotIn(KEY, prov.SETTINGS.read_text(encoding="utf-8"))   # the key is fetched at run time instead
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", now["env"])
        self.assertRegex(now["apiKeyHelper"], r'key ' + made["id"] + r'$')
        self.assertEqual(prov.current_route(), {"kind": "provider", "id": made["id"], "name": "BDX Muse", "baseUrl": BASE,
                                                "missing": False})
        self.assertEqual(len(list(prov.SETTINGS_BACKUPS.glob("*.dat"))), 1)
        self.assertFalse(any(b"muse-x" in f.read_bytes() or b"Read" in f.read_bytes() for f in prov.SETTINGS_BACKUPS.glob("*")))

    def test_base_url_and_credential_helper_always_arrive_together(self):
        made = self.make()
        prov.activate(made["id"])
        s = self.settings()
        self.assertTrue(s["env"]["ANTHROPIC_BASE_URL"] and s["apiKeyHelper"])   # one atomic file replace, never one without the other

    def test_deactivate_restores_the_users_own_values_exactly(self):
        original = {"model": "sonnet", "apiKeyHelper": "my-own-helper", "env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5-5",
                                                                                "KEEP": "me"}}
        self.write_settings(original)
        made = self.make()
        prov.activate(made["id"])
        self.assertEqual(prov.deactivate(), "BDX Muse")
        self.assertEqual(self.settings(), original)
        self.assertFalse(prov.ROUTE_FILE.exists())
        self.assertIsNone(prov.deactivate())                             # second call: nothing to do

    def test_activate_without_settings_file_and_back_leaves_it_empty(self):
        made = self.make()
        prov.activate(made["id"])
        prov.deactivate()
        self.assertEqual(self.settings(), {})

    def test_switching_provider_to_provider_remembers_the_originals(self):
        original = {"env": {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5"}}
        self.write_settings(original)
        a, b = self.make("A"), self.make("B", url="https://other.example.com", models={"opus": "o2", "sonnet": "s2", "haiku": "h2"})
        prov.activate(a["id"])
        prov.activate(b["id"])
        s = self.settings()
        self.assertEqual((s["env"]["ANTHROPIC_BASE_URL"], s["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"]), ("https://other.example.com", "o2"))
        prov.deactivate()
        self.assertEqual(self.settings(), original)

    def test_refuses_when_another_credential_would_override_the_helper(self):
        original = {"env": {"ANTHROPIC_AUTH_TOKEN": "someone-elses"}}
        self.write_settings(original)
        with self.assertRaises(core.SwitchError) as cm:
            prov.activate(self.make()["id"])
        self.assertIn("ANTHROPIC_AUTH_TOKEN", str(cm.exception))
        self.assertEqual(self.settings(), original)

    def test_broken_settings_file_is_never_written(self):
        prov.SETTINGS.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(core.SwitchError):
            prov.activate(self.make()["id"])
        self.assertEqual(prov.SETTINGS.read_text(encoding="utf-8"), "{ not json")
        prov.SETTINGS.write_text("[1,2]", encoding="utf-8")
        with self.assertRaises(core.SwitchError):
            prov.activate(self.make("Other")["id"])

    def test_a_write_that_does_not_verify_is_rolled_back(self):
        original = {"model": "sonnet"}
        self.write_settings(original)
        made = self.make()
        real = core.write_json
        core.write_json = lambda path, obj, like=None: Path(path).write_text("garbage", encoding="utf-8")
        try:
            with self.assertRaises(core.SwitchError) as cm:
                prov.activate(made["id"])
        finally:
            core.write_json = real
        self.assertIn("restored", str(cm.exception))
        self.assertEqual(self.settings(), original)

    def test_deactivate_leaves_a_value_somebody_changed_since(self):
        made = self.make()
        prov.activate(made["id"])
        s = self.settings()
        s["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = "hand-edited"
        self.write_settings(s)
        prov.deactivate()
        self.assertEqual(self.settings(), {"env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "hand-edited"}})

    def test_someone_elses_base_url_is_reported_and_left_alone(self):
        original = {"env": {"ANTHROPIC_BASE_URL": "https://their.example.com"}}
        self.write_settings(original)
        self.assertEqual(prov.current_route(), {"kind": "custom", "baseUrl": "https://their.example.com"})
        self.assertIsNone(prov.deactivate())
        self.assertEqual(self.settings(), original)

    def test_editing_or_deleting_the_active_provider(self):
        made = self.make()
        prov.activate(made["id"])
        prov.update_provider(made["id"], base_url="https://new.example.com", models={**MODELS, "sonnet": "other"})
        s = self.settings()
        self.assertEqual((s["env"]["ANTHROPIC_BASE_URL"], s["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"]), ("https://new.example.com", "other"))
        self.assertEqual(prov.get_provider(made["id"])["apiKey"], KEY)   # blank key keeps the saved one
        prov.delete_provider(made["id"])
        self.assertEqual(prov.current_route()["kind"], "account")
        self.assertNotIn("apiKeyHelper", self.settings())

    def test_switching_to_an_account_leaves_the_provider(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        made = self.make()
        prov.activate(made["id"])
        st = prov.full_state()
        self.assertFalse(any(p["inUse"] for p in st["profiles"]))        # a provider is in use, not an account
        self.assertTrue(st["providers"][0]["active"])
        res = prov.use_account(personal["id"])                            # already the live login, but a provider was in use
        self.assertEqual(res["leftProvider"], "BDX Muse")
        self.assertTrue(res["changed"])
        self.assertEqual(prov.current_route()["kind"], "account")
        self.assertTrue(next(p for p in prov.full_state()["profiles"] if p["id"] == personal["id"])["inUse"])
        prov.activate(made["id"])
        prov.use_account(team["id"])
        self.assertEqual(core.read_live()["claudeAiOauth"]["refreshToken"], "rt-t1")
        self.assertEqual(prov.current_route()["kind"], "account")

    # ---- the key helper
    def test_helper_command_is_recognised_and_prints_only_the_key(self):
        made = self.make()
        self.assertRegex(prov.helper_command(made["id"]), prov.HELPER_RE.pattern)
        env = {**os.environ, "CA_STORE": str(core.STORE), "CA_CLAUDE_DIR": str(core.CLAUDE_DIR)}
        done = subprocess.run([sys.executable, "-I", str(SRC / "claude_accounts.py"), "key", made["id"]],
                              capture_output=True, text=True, env=env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, KEY, ""))
        bad = subprocess.run([sys.executable, "-I", str(SRC / "claude_accounts.py"), "key", "000000000000"],
                             capture_output=True, text=True, env=env)
        self.assertEqual((bad.returncode, bad.stdout), (1, ""))

    # ---- testing a provider
    def test_connection_test_catches_the_thinking_replay_bug(self):
        made = self.make()
        calls = []

        def fake(url, key, body, timeout=90):
            calls.append(body["model"])
            if len(body["messages"]) == 1:   # step 1: a tool call with a thinking block
                return 200, {"content": [{"type": "thinking", "thinking": "..", "signature": "s"},
                                         {"type": "tool_use", "id": "t1", "name": "get_weather", "input": {}}]}, 0.5
            if body["model"] == "muse-x":    # step 2 fails for this model, like the gateway did before the fix
                return 400, {"error": {"message": "`input[1]` missing required field `summary`"}}, 0.2
            return 200, {"content": [{"type": "text", "text": "sunny"}]}, 0.4

        real, prov._http = prov._http, fake
        try:
            rows = prov.test_provider(made["id"])
        finally:
            prov._http = real
        by = {r["model"]: r for r in rows}
        self.assertEqual(sorted(by), ["gem-y", "muse-x"])                # each distinct model once
        self.assertTrue(by["gem-y"]["ok"])
        self.assertFalse(by["muse-x"]["ok"])
        self.assertIn("fails after a tool call", by["muse-x"]["detail"])
        self.assertIn("summary", by["muse-x"]["detail"])
        self.assertEqual(by["muse-x"]["tiers"], ["opus", "sonnet"])
        self.assertNotIn(KEY, json.dumps(rows))

    def test_connection_test_flags_a_model_that_never_uses_tools_and_http_errors(self):
        made = self.make()
        real = prov._http
        prov._http = lambda url, key, body, timeout=90: (200, {"content": [{"type": "text", "text": "I won't"}]}, 0.1)
        try:
            self.assertIn("did not use the tool", prov.test_provider(made["id"])[0]["detail"])
            prov._http = lambda url, key, body, timeout=90: (401, {"error": {"message": "invalid key"}}, 0.1)
            row = prov.test_provider(made["id"], deep=False)[0]
            self.assertEqual((row["ok"], row["detail"]), (False, "HTTP 401: invalid key"))
        finally:
            prov._http = real

    # ---- the command line
    def test_cli_add_use_off_without_the_key_on_the_command_line(self):
        code, res = run_cli("provider", "add", "BDX", "--url", BASE + "/v1", "--key-env", "TEST_GW_KEY",
                            "--opus", "m1", "--sonnet", "m1", "--haiku", "h1", env={"TEST_GW_KEY": KEY})
        self.assertEqual((code, res["ok"], res["baseUrl"]), (0, True, BASE))
        self.assertNotIn(KEY, json.dumps(res))
        code, res = run_cli("provider", "add", "X", "--url", BASE, "--key-env", "NOT_SET_ANYWHERE",
                            "--opus", "m", "--sonnet", "m", "--haiku", "h")
        self.assertEqual(code, 1)
        code, res = run_cli("provider", "use", "bdx")
        self.assertEqual((code, res["ok"]), (0, True))
        code, res = run_cli("status")
        self.assertEqual(res["route"]["kind"], "provider")
        code, res = run_cli("state")
        self.assertEqual([p["active"] for p in res["providers"]], [True])
        code, res = run_cli("provider", "edit", "BDX", "--haiku", "h2")
        self.assertEqual(self.settings()["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "h2")
        code, res = run_cli("provider", "off")
        self.assertEqual((code, res["left"]), (0, "BDX"))
        code, res = run_cli("provider", "remove", "BDX")
        self.assertEqual((code, run_cli("provider", "list")[1]["providers"]), (0, []))

    def test_doctor_notices_a_missing_helper_and_a_stray_credential(self):
        made = self.make()
        prov.activate(made["id"])
        self.assertTrue(all(c["ok"] for c in prov.doctor_checks()))
        s = self.settings()
        s["env"]["ANTHROPIC_API_KEY"] = "x"
        s["apiKeyHelper"] = '"C:\\gone\\python.exe" -I "C:\\gone\\claude_accounts.py" key ' + made["id"]
        self.write_settings(s)
        failed = [c["name"] for c in prov.doctor_checks() if not c["ok"]]
        self.assertIn("No other credential in settings.json", failed)
        self.assertIn("Key helper script exists", failed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
