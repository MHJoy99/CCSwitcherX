"""Sandbox tests for the core. Never touches the real login: everything lives in a temp folder."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="ca-test-"))
os.environ["CA_CLAUDE_DIR"] = str(SANDBOX / "claude")
os.environ["CA_STORE"] = str(SANDBOX / "store")
os.environ["CA_NO_NETWORK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import claude_accounts_core as core  # noqa: E402


def acct(n, kind, org="Org", tier="default_claude_max_5x"):
    return {"accountUuid": f"acc-{n}", "organizationUuid": f"org-{n}", "emailAddress": f"u{n}@example.com",
            "organizationType": kind, "organizationName": org, "organizationRateLimitTier": tier,
            "displayName": f"User {n}"}


def oauth(tag, days=20):
    return {"accessToken": f"at-{tag}", "refreshToken": f"rt-{tag}", "expiresAt": int(time.time() * 1000) + 3600_000,
            "refreshTokenExpiresAt": int(time.time() * 1000) + days * 86400_000, "scopes": ["user:inference"],
            "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"}


class CoreTests(unittest.TestCase):
    def setUp(self):
        for p in (core.PROFILES, core.BACKUPS):
            if p.exists():
                for f in p.glob("*"):
                    f.unlink()
        core.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        core.JOURNAL.unlink(missing_ok=True)
        core.USAGE_FILE.unlink(missing_ok=True)
        for d in (core.CLAUDE_DIR / ".oauth_refresh.lock", core.CLAUDE_DIR.parent / (core.CLAUDE_DIR.name + ".lock")):
            if d.is_dir():
                d.rmdir()
        core.CREDS.write_text(json.dumps({"claudeAiOauth": oauth("p1"), "mcpOAuth": {"srv": {"token": "keep-me"}}}))
        core.CLAUDE_JSON.write_text(json.dumps({
            "numStartups": 7, "projects": {"C:\\x": {"history": ["\u0985\u09a8\u09c1"]}},
            "oauthAccount": acct(1, "claude_max"), "cachedExtraUsageDisabledReason": "stale"}, indent=2))

    def test_encryption_roundtrip_and_not_plaintext(self):
        raw = core._encode({"secret": "hunter2"})
        self.assertNotIn(b"hunter2", raw)
        self.assertEqual(core._decode(raw), {"secret": "hunter2"})

    def test_save_list_rename_delete(self):
        live = core.read_live()
        p = core.save_profile(live)
        self.assertEqual(p["name"], "Personal")
        self.assertEqual(len(core.list_profiles()), 1)
        core.rename_profile(p["id"], "Joy personal")
        self.assertEqual(core.find_profile("joy PERSONAL")["id"], p["id"])
        core.delete_profile(p["id"])
        self.assertEqual(core.list_profiles(), [])
        # nothing readable on disk
        self.assertFalse(any(b"rt-p1" in f.read_bytes() for f in core.PROFILES.glob("*")))

    def test_switch_roundtrip_preserves_everything_else(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team", "Acme")})
        self.assertEqual(team["name"], "Team")

        r = core.switch_to(team["id"])
        self.assertTrue(r["changed"])
        creds = json.loads(core.CREDS.read_text(encoding='utf-8'))
        cfg = json.loads(core.CLAUDE_JSON.read_text(encoding='utf-8'))
        self.assertEqual(creds["claudeAiOauth"]["refreshToken"], "rt-t1")
        self.assertEqual(creds["mcpOAuth"], {"srv": {"token": "keep-me"}})
        self.assertEqual(cfg["oauthAccount"]["emailAddress"], "u2@example.com")
        self.assertEqual(cfg["numStartups"], 7)
        self.assertEqual(cfg["projects"]["C:\\x"]["history"], ["\u0985\u09a8\u09c1"])
        self.assertNotIn("cachedExtraUsageDisabledReason", cfg)

        core.switch_to(personal["id"])
        self.assertEqual(json.loads(core.CREDS.read_text(encoding='utf-8'))["claudeAiOauth"]["refreshToken"], "rt-p1")
        self.assertEqual(json.loads(core.CLAUDE_JSON.read_text(encoding='utf-8'))["oauthAccount"]["emailAddress"], "u1@example.com")

    def test_rotated_tokens_are_carried_back(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team", "Acme")})
        # Claude Code rotates the personal refresh token while it is active
        creds = json.loads(core.CREDS.read_text(encoding='utf-8'))
        creds["claudeAiOauth"]["refreshToken"] = "rt-p1-rotated"
        core.CREDS.write_text(json.dumps(creds))
        core.switch_to(team["id"])
        self.assertEqual(core.get_profile(personal["id"])["claudeAiOauth"]["refreshToken"], "rt-p1-rotated")
        core.switch_to(personal["id"])
        self.assertEqual(json.loads(core.CREDS.read_text(encoding='utf-8'))["claudeAiOauth"]["refreshToken"], "rt-p1-rotated")

    def test_unsaved_current_login_is_never_lost(self):
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team", "Acme")})
        self.assertEqual(core.list_profiles()[0]["id"], team["id"])  # personal not saved yet
        r = core.switch_to(team["id"])
        self.assertEqual(r["autosaved"], "Personal")
        self.assertEqual(len(core.list_profiles()), 2)
        self.assertEqual(len(list(core.BACKUPS.glob("*.dat"))), 1)

    def test_switch_to_active_account_is_a_noop(self):
        personal = core.save_profile(core.read_live())
        before = core.CLAUDE_JSON.read_text(encoding='utf-8')
        r = core.switch_to(personal["id"])
        self.assertFalse(r["changed"])
        self.assertEqual(core.CLAUDE_JSON.read_text(encoding='utf-8'), before)

    def test_expired_sign_in_is_refused_and_nothing_changes(self):
        core.save_profile(core.read_live())
        stale = core.save_profile({"claudeAiOauth": oauth("t1", days=-2), "oauthAccount": acct(2, "claude_team")})
        creds_before = core.CREDS.read_text(encoding='utf-8')
        with self.assertRaises(core.SwitchError):
            core.switch_to(stale["id"])
        self.assertEqual(core.CREDS.read_text(encoding='utf-8'), creds_before)

    def test_failed_second_write_rolls_back_credentials(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        creds_before = core.CREDS.read_text(encoding='utf-8')
        real = core.write_json

        def boom(path, obj, like_text=None):
            if path == core.CLAUDE_JSON:
                raise OSError("disk says no")
            return real(path, obj, like_text)

        core.write_json = boom
        try:
            with self.assertRaises(OSError):
                core.apply_login(team["claudeAiOauth"], team["oauthAccount"])
        finally:
            core.write_json = real
        self.assertEqual(json.loads(core.CREDS.read_text(encoding='utf-8')), json.loads(creds_before))

    def test_names_must_be_unique(self):
        core.save_profile(core.read_live())
        with self.assertRaises(core.SwitchError):
            core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")}, name="personal")

    # ---- Claude Code's locks
    def test_swap_takes_and_releases_claude_codes_locks(self):
        seen = {}
        real = core.write_json

        def spy(path, obj, like_text=None):
            if path == core.CREDS:
                seen["oauth_refresh"] = (core.CLAUDE_DIR / ".oauth_refresh.lock").is_dir()
                seen["legacy"] = (core.CLAUDE_DIR.parent / (core.CLAUDE_DIR.name + ".lock")).is_dir()
                seen["config"] = (core.CLAUDE_JSON.parent / (core.CLAUDE_JSON.name + ".lock")).is_dir()
            return real(path, obj, like_text)

        core.write_json = spy
        try:
            core.save_profile(core.read_live())
            team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
            core.switch_to(team["id"])
        finally:
            core.write_json = real
        self.assertEqual(seen, {"oauth_refresh": True, "legacy": True, "config": True})
        self.assertEqual([p.name for p in core.CLAUDE_DIR.glob("*.lock")], [])
        self.assertFalse((core.CLAUDE_DIR / ".oauth_refresh.lock").exists())

    def test_a_held_lock_blocks_the_swap_and_nothing_changes(self):
        core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        held = core.CLAUDE_DIR / ".oauth_refresh.lock"
        held.mkdir()  # pretend Claude Code is refreshing right now
        wait, core.LOCK_WAIT_S = core.LOCK_WAIT_S, 0.6
        before = core.CREDS.read_text(encoding="utf-8")
        try:
            with self.assertRaises(core.SwitchError):
                core.switch_to(team["id"])
        finally:
            core.LOCK_WAIT_S = wait
            held.rmdir()
        self.assertEqual(core.CREDS.read_text(encoding="utf-8"), before)

    def test_a_dead_holders_stale_lock_is_taken_over(self):
        core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        stale = core.CLAUDE_DIR / ".oauth_refresh.lock"
        stale.mkdir()
        old = time.time() - 120
        os.utime(stale, (old, old))
        core.switch_to(team["id"])
        self.assertEqual(json.loads(core.CREDS.read_text(encoding="utf-8"))["claudeAiOauth"]["refreshToken"], "rt-t1")

    # ---- crash safety
    def test_interrupted_switch_is_finished_by_recover(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        # simulate a crash after the credentials were written but before .claude.json was
        creds = json.loads(core.CREDS.read_text(encoding="utf-8"))
        creds["claudeAiOauth"] = team["claudeAiOauth"]
        core.CREDS.write_text(json.dumps(creds), encoding="utf-8")
        core.JOURNAL.write_text(json.dumps({"target": team["id"], "at": time.time() - 60}), encoding="utf-8")
        self.assertEqual(core.recover(), "Team")
        self.assertEqual(json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))["oauthAccount"]["emailAddress"],
                         "u2@example.com")
        self.assertFalse(core.JOURNAL.exists())
        self.assertIsNone(core.recover())

    def test_recent_journal_is_left_alone(self):
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        core.JOURNAL.write_text(json.dumps({"target": team["id"], "at": time.time()}), encoding="utf-8")
        self.assertIsNone(core.recover())
        self.assertTrue(core.JOURNAL.exists())
        core.JOURNAL.unlink()

    def test_clean_failure_leaves_no_journal(self):
        core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        real = core.write_json
        core.write_json = lambda p, o, like_text=None: (_ for _ in ()).throw(OSError("nope")) if p == core.CLAUDE_JSON else real(p, o, like_text)
        try:
            with self.assertRaises(OSError):
                core.switch_to(team["id"])
        finally:
            core.write_json = real
        self.assertFalse(core.JOURNAL.exists())
        self.assertIsNone(core.recover())

    def test_settle_check_repairs_an_overwritten_account(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        core.switch_to(team["id"])
        cfg = json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))
        cfg["oauthAccount"] = acct(1, "claude_max")  # a running session rewrote its old account back
        core.CLAUDE_JSON.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertTrue(core.settle_check(team["id"]))
        self.assertEqual(json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))["oauthAccount"]["emailAddress"],
                         "u2@example.com")

    # ---- what the UIs see
    def test_state_has_no_secrets(self):
        core.save_profile(core.read_live())
        core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team", "Acme")})
        text = json.dumps(core.state())
        for secret in ("rt-p1", "at-p1", "rt-t1", "at-t1"):
            self.assertNotIn(secret, text)
        st = core.state()
        self.assertEqual([p["name"] for p in st["profiles"]], ["Personal", "Team"])
        self.assertEqual([p["active"] for p in st["profiles"]], [True, False])
        self.assertIsNone(st["unsaved"])

    def test_unsaved_live_login_is_reported(self):
        st = core.state()
        self.assertEqual(st["profiles"], [])
        self.assertEqual(st["unsaved"]["email"], "u1@example.com")

    # ---- add account (browser sign-in), with Claude Code's sign-in faked
    def _fake_login(self, finish_as=None, hang=False, code=0):
        class Proc:
            returncode = None
            pid = 0
            def __init__(s):
                s.t0 = time.time()
            def poll(s):
                if hang:
                    return None
                if time.time() - s.t0 > 0.4:
                    if finish_as:
                        core.CREDS.write_text(json.dumps({"claudeAiOauth": finish_as[0], "mcpOAuth": {}}), encoding="utf-8")
                        cfg = json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))
                        cfg["oauthAccount"] = finish_as[1]
                        core.CLAUDE_JSON.write_text(json.dumps(cfg), encoding="utf-8")
                    s.returncode = code
                    return code
                return None
        log = core.STORE / "login.log"
        core.STORE.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        self._real = (core.start_login, core.kill_tree)
        core.start_login = lambda email=None: (Proc(), log)
        core.kill_tree = lambda proc: setattr(proc, "poll", lambda: -1) or setattr(proc, "returncode", -1)

    def tearDown(self):
        if hasattr(self, "_real"):
            core.start_login, core.kill_tree = self._real
            del self._real

    def test_add_login_new_account_is_saved_and_old_one_kept(self):
        self._fake_login(finish_as=(oauth("t1"), acct(2, "claude_team", "Acme")))
        res = core.add_login()
        self.assertEqual((res["status"], res["name"]), ("new", "Team"))
        names = sorted(p["name"] for p in core.list_profiles())
        self.assertEqual(names, ["Personal", "Team"])  # the previous login was saved before signing in
        self.assertEqual(core.state()["active"], res["id"])

    def test_add_login_same_account_is_reported(self):
        self._fake_login(finish_as=(oauth("p1-new"), acct(1, "claude_max")))
        res = core.add_login()
        self.assertEqual(res["status"], "same")
        self.assertEqual(len(core.list_profiles()), 1)

    def test_cancelled_sign_in_restores_the_previous_login(self):
        self._fake_login(finish_as=(oauth("t1"), acct(2, "claude_team")), hang=True)
        res = core.add_login(cancelled=lambda: True)
        self.assertEqual(res["status"], "cancelled")
        self.assertEqual(core.read_live()["oauthAccount"]["emailAddress"], "u1@example.com")

    def test_failed_sign_in_half_written_is_rolled_back(self):
        self._fake_login(finish_as=(oauth("t1"), acct(2, "claude_team")), code=1)
        res = core.add_login()
        self.assertEqual(res["status"], "failed")
        live = core.read_live()
        self.assertEqual(live["oauthAccount"]["emailAddress"], "u1@example.com")
        self.assertEqual(live["claudeAiOauth"]["refreshToken"], "rt-p1")

    def test_doctor_reports_checks(self):
        checks = {c["name"]: c["ok"] for c in core.doctor()}
        self.assertTrue(checks["Login file found"] and checks["Account record found"] and checks["Saved logins encrypted"])

    # ---- usage
    def _payload(self, session=64, week=34, hours=3):
        iso = lambda h: datetime.fromtimestamp(time.time() + h * 3600).astimezone().isoformat()
        return {"five_hour": {"utilization": session, "resets_at": iso(hours)},
                "seven_day": {"utilization": week, "resets_at": iso(70)},
                "limits": [{"kind": "weekly_scoped", "percent": 12, "resets_at": iso(70),
                            "scope": {"model": {"display_name": "Fable"}}}]}

    def _put_claude_cache(self, uuid, payload, age=5):
        cfg = json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))
        cfg["cachedUsageUtilization"] = {"fetchedAtMs": int((time.time() - age) * 1000), "accountUuid": uuid,
                                         "utilization": payload}
        core.CLAUDE_JSON.write_text(json.dumps(cfg), encoding="utf-8")

    def test_parse_usage(self):
        u = core.parse_usage(self._payload())
        self.assertEqual((u["session"]["pct"], u["week"]["pct"], u["models"][0]["name"]), (64.0, 34.0, "Fable"))
        self.assertIsNone(core.parse_usage({"five_hour": None, "seven_day": None}))
        self.assertIsNone(core.parse_usage(None))

    def test_active_account_usage_comes_from_claude_codes_cache_without_network(self):
        personal = core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        self.real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self.fail("must not use the network")
        try:
            self.assertEqual(core.refresh_usage()[personal["id"]], "claude-cache")
        finally:
            core._fetch_usage = self.real_fetch
        view = next(p for p in core.state()["profiles"] if p["id"] == personal["id"])["usage"]
        self.assertEqual(view["text"], "Session 64% · Week 34% · Fable 12%")
        self.assertIn("Session resets in", view["resets"])
        self.assertFalse(view["stale"])

    def test_state_alone_adopts_the_cache_and_keeps_it_when_the_account_goes_idle(self):
        personal = core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        self._put_claude_cache("acc-1", self._payload(session=40))
        core.state()                       # adopts Personal's numbers
        core.switch_to(team["id"])
        view = next(p for p in core.state()["profiles"] if p["id"] == personal["id"])["usage"]
        self.assertTrue(view["text"].startswith("Session 40%"))

    def test_idle_account_with_valid_token_is_fetched_and_stored_without_tokens(self):
        core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        self._put_claude_cache("acc-1", self._payload())  # a running Claude Code keeps the active account's numbers
        seen = []
        real, core._fetch_usage = core._fetch_usage, lambda tok: (seen.append(tok), self._payload(session=22))[1]
        try:
            report = core.refresh_usage()
            core.refresh_usage()           # second call inside the minimum gap
        finally:
            core._fetch_usage = real
        self.assertEqual(report[team["id"]], "fetched")
        self.assertEqual(seen, ["at-t1"])  # exactly one request, with that account's own token
        self.assertNotIn("at-t1", core.USAGE_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("rt-t1", core.USAGE_FILE.read_text(encoding="utf-8"))

    def test_expired_access_token_is_never_refreshed_and_last_known_is_kept(self):
        core.save_profile(core.read_live())
        old = oauth("t1")
        old["expiresAt"] = int(time.time() * 1000) - 1000
        team = core.save_profile({"claudeAiOauth": old, "oauthAccount": acct(2, "claude_team")})
        core._store_usage(team["id"], {**core.parse_usage(self._payload(session=70)), "fetchedAt": time.time() - 7200, "source": "api"})
        real, core._fetch_usage = core._fetch_usage, lambda t: self.fail("expired token must not be used")
        try:
            self.assertEqual(core.refresh_usage()[team["id"]], "token-expired")
        finally:
            core._fetch_usage = real
        view = next(p for p in core.state()["profiles"] if p["id"] == team["id"])["usage"]
        self.assertEqual((view["session"]["pct"], view["stale"], view["note"]), (70, True, "as of 2 h ago"))

    def test_rate_limit_backs_off(self):
        core.save_profile(core.read_live())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        self._put_claude_cache("acc-1", self._payload())
        calls = []

        def limited(tok):
            calls.append(tok)
            raise urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "900"}, None)

        real, core._fetch_usage = core._fetch_usage, limited
        try:
            self.assertEqual(core.refresh_usage(force=True)[team["id"]], "http-429")
            self.assertEqual(core.refresh_usage(force=True)[team["id"]], "backoff")
        finally:
            core._fetch_usage = real
        self.assertEqual(len(calls), 1)

    def test_a_rolled_over_window_is_shown_as_reset_not_as_old_percentage(self):
        u = core.parse_usage(self._payload(session=90, hours=-1))
        view = core.usage_view({**u, "fetchedAt": time.time() - 60, "source": "api"})
        self.assertEqual(view["session"], {"pct": None, "resetsAt": view["session"]["resetsAt"], "over": True})
        self.assertTrue(view["text"].startswith("Session reset"))

    def test_deleting_an_account_forgets_its_usage(self):
        personal = core.save_profile(core.read_live())
        core._store_usage(personal["id"], {"session": {"pct": 1.0, "resetsAt": None}, "fetchedAt": time.time()})
        core.delete_profile(personal["id"])
        self.assertNotIn(personal["id"], core._read_usage_file())

    def test_labels(self):
        d = core.describe({"claudeAiOauth": oauth("x"), "oauthAccount": acct(2, "claude_team", "Acme")})
        self.assertEqual((d["plan"], d["detail"], d["team"]), ("Team", "Team \u00b7 Acme", True))
        d = core.describe({"claudeAiOauth": oauth("x"), "oauthAccount": acct(1, "claude_max", tier="default_claude_max_20x")})
        self.assertEqual((d["plan"], d["team"]), ("Max 20x", False))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        import shutil
        shutil.rmtree(SANDBOX, ignore_errors=True)
