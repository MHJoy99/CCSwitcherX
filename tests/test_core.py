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

    def test_reset_clock_rounds_to_the_nearest_minute(self):
        base = time.mktime((2026, 10, 15, 17, 0, 0, 0, 0, -1))
        for offset in (-0.5, -0.2, 0, 0.3):         # Anthropic's reset times land a hair either side of the hour
            self.assertIn("5:00 PM", core.fmt_clock(base + offset))
        self.assertIn("4:59 PM", core.fmt_clock(base - 60))

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

    def test_expired_access_token_offline_keeps_last_known(self):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        old = oauth("t1")
        old["expiresAt"] = int(time.time() * 1000) - 1000
        team = core.save_profile({"claudeAiOauth": old, "oauthAccount": acct(2, "claude_team")})
        core._store_usage(team["id"], {**core.parse_usage(self._payload(session=70)), "fetchedAt": time.time() - 7200, "source": "api"})
        def offline(body):
            raise OSError("offline")

        calls, real_post = self._with_token_endpoint(offline)
        real, core._fetch_usage = core._fetch_usage, lambda t: self.fail("expired token must not be used")
        try:
            self.assertEqual(core.refresh_usage()[team["id"]], "OSError")  # renewal tried, network down, nothing used
        finally:
            core._fetch_usage, core._post_token = real, real_post
        self.assertEqual(len(calls), 1)
        view = next(p for p in core.state()["profiles"] if p["id"] == team["id"])["usage"]
        self.assertEqual((view["session"]["pct"], view["stale"], view["note"]), (70, True, "as of 2 h ago"))

    # ---- renewing a saved (not live) account's expired access token
    def _expired_saved(self, tag="t1", refresh_days=20, scopes=("user:inference", "user:profile")):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())  # the live account is read from Claude Code's cache
        old = oauth(tag, days=refresh_days)
        old["expiresAt"] = int(time.time() * 1000) - 1000
        old["scopes"] = list(scopes)
        return core.save_profile({"claudeAiOauth": old, "oauthAccount": acct(2, "claude_team")})

    def _with_token_endpoint(self, fake):
        calls = []

        def post(body):
            calls.append(body)
            return fake(body)

        real, core._post_token = core._post_token, post
        return calls, real

    def test_saved_expired_access_token_is_renewed_and_saved_back(self):
        team = self._expired_saved()
        now = time.time()
        calls, real = self._with_token_endpoint(lambda b: {"access_token": "at-new", "refresh_token": "rt-new",
                                                           "expires_in": 3600, "refresh_token_expires_in": 30 * 86400,
                                                           "scope": "user:inference user:profile"})
        seen = []
        real_fetch, core._fetch_usage = core._fetch_usage, lambda tok: (seen.append(tok), self._payload(session=31))[1]
        try:
            report = core.refresh_usage()
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(report[team["id"]], "fetched")
        self.assertEqual(calls, [{"grant_type": "refresh_token", "refresh_token": "rt-t1",
                                  "client_id": core.CLIENT_ID, "scope": "user:inference user:profile"}])
        self.assertEqual(seen, ["at-new"])
        saved = core.get_profile(team["id"])["claudeAiOauth"]
        self.assertEqual((saved["accessToken"], saved["refreshToken"]), ("at-new", "rt-new"))
        self.assertLess(abs(saved["expiresAt"] - (now + 3600) * 1000), 5000)
        self.assertLess(abs(saved["refreshTokenExpiresAt"] - (now + 30 * 86400) * 1000), 5000)
        self.assertEqual(core._read_usage_file()[team["id"]]["session"]["pct"], 31.0)

    def test_renewal_without_a_new_refresh_token_keeps_the_old_one(self):
        team = self._expired_saved()
        calls, real = self._with_token_endpoint(lambda b: {"access_token": "at-new", "expires_in": 3600})
        real_fetch, core._fetch_usage = core._fetch_usage, lambda tok: self._payload()
        try:
            self.assertEqual(core.refresh_usage()[team["id"]], "fetched")
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(len(calls), 1)
        saved = core.get_profile(team["id"])["claudeAiOauth"]
        self.assertEqual((saved["accessToken"], saved["refreshToken"]), ("at-new", "rt-t1"))

    def test_live_account_with_expired_token_is_left_to_claude_code(self):
        personal = core.save_profile(core.read_live())
        creds = json.loads(core.CREDS.read_text(encoding="utf-8"))
        creds["claudeAiOauth"]["expiresAt"] = int(time.time() * 1000) - 1000
        core.CREDS.write_text(json.dumps(creds))
        calls, real = self._with_token_endpoint(lambda b: self.fail("live token must not be renewed"))
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self.fail("expired token must not be used")
        try:
            self.assertEqual(core.refresh_usage(force=True)[personal["id"]], "token-expired")
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(core.CREDS.read_text(encoding="utf-8"))["claudeAiOauth"]["refreshToken"], "rt-p1")

    def test_refused_sign_in_is_flagged_and_backs_off_for_six_hours(self):
        team = self._expired_saved()
        before = core.get_profile(team["id"])["claudeAiOauth"]
        core._store_usage(team["id"], {**core.parse_usage(self._payload(session=70)),
                                       "fetchedAt": time.time() - 7200, "source": "api"})

        def refused(body):
            raise urllib.error.HTTPError("u", 400, "invalid_grant", {}, None)

        calls, real = self._with_token_endpoint(refused)
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self.fail("refused sign-in must not fetch")
        try:
            now = time.time()
            self.assertEqual(core.refresh_usage(force=True)[team["id"]], "signin-rejected")
            entry = core._read_usage_file()[team["id"]]
            self.assertEqual(entry["error"], "signin-rejected")
            self.assertGreater(entry["retryAt"], now + 6 * 3600 - 5)
            self.assertTrue(core.usage_view(entry)["signInRejected"])
            self.assertEqual(core.refresh_usage(force=True)[team["id"]], "backoff")
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(len(calls), 1)
        after = core.get_profile(team["id"])["claudeAiOauth"]
        self.assertEqual((after["accessToken"], after["refreshToken"]), (before["accessToken"], before["refreshToken"]))

    def test_server_error_during_renewal_is_a_normal_backoff(self):
        team = self._expired_saved()
        before = core.get_profile(team["id"])["claudeAiOauth"]

        def broken(body):
            raise urllib.error.HTTPError("u", 500, "Server Error", {}, None)

        calls, real = self._with_token_endpoint(broken)
        try:
            now = time.time()
            self.assertEqual(core.refresh_usage()[team["id"]], "http-500")
            entry = core._read_usage_file()[team["id"]]
            self.assertLess(abs(entry["retryAt"] - (now + core.USAGE_BACKOFF_S)), 5)
            self.assertNotEqual(entry.get("error"), "signin-rejected")
        finally:
            core._post_token = real
        self.assertEqual(len(calls), 1)
        after = core.get_profile(team["id"])["claudeAiOauth"]
        self.assertEqual((after["accessToken"], after["refreshToken"]), (before["accessToken"], before["refreshToken"]))

    def test_expired_refresh_token_is_not_renewed(self):
        team = self._expired_saved(refresh_days=-2)
        calls, real = self._with_token_endpoint(lambda b: self.fail("dead sign-in must not be sent to the server"))
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self.fail("expired token must not be used")
        try:
            self.assertEqual(core.refresh_usage()[team["id"]], "token-expired")
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(calls, [])

    def test_valid_access_token_is_never_renewed(self):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        team = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_team")})
        calls, real = self._with_token_endpoint(lambda b: self.fail("valid token must not be renewed"))
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self._payload()
        try:
            self.assertEqual(core.refresh_usage()[team["id"]], "fetched")
        finally:
            core._post_token, core._fetch_usage = real, real_fetch
        self.assertEqual(calls, [])

    # ---- plan re-check (/api/oauth/profile)
    def _profile_reply(self, kind, tier):
        return {"organization": {"organization_type": kind, "rate_limit_tier": tier}}

    def _plan_of(self, pid):
        return next(p for p in core.state()["profiles"] if p["id"] == pid)["plan"]

    def test_saved_pro_account_shows_max_5x_after_a_forced_read(self):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        pro = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_pro")})
        self.assertEqual(self._plan_of(pro["id"]), "Pro")
        seen = []
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self._payload()
        real_profile, core._fetch_profile = core._fetch_profile, lambda t: (seen.append(t), self._profile_reply(
            "claude_max", "default_claude_max_5x"))[1]
        try:
            self.assertEqual(core.refresh_usage(force=True)[pro["id"]], "fetched")
        finally:
            core._fetch_usage, core._fetch_profile = real_fetch, real_profile
        self.assertEqual(seen, ["at-t1"])
        self.assertEqual(self._plan_of(pro["id"]), "Max 5x")

    def test_live_account_plan_is_never_rewritten_from_the_profile(self):
        personal = core.save_profile(core.read_live())  # live Max, no cache: read with its own valid token
        self.assertEqual(self._plan_of(personal["id"]), "Max 5x")
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self._payload()
        real_profile, core._fetch_profile = core._fetch_profile, lambda t: self.fail("live plan must not be re-checked")
        try:
            self.assertEqual(core.refresh_usage(force=True)[personal["id"]], "fetched")
        finally:
            core._fetch_usage, core._fetch_profile = real_fetch, real_profile
        self.assertEqual(self._plan_of(personal["id"]), "Max 5x")

        # a live account whose numbers come from Claude Code's cache is never asked either
        self._put_claude_cache("acc-1", self._payload())
        real_profile, core._fetch_profile = core._fetch_profile, lambda t: self.fail("live plan must not be re-checked")
        try:
            self.assertEqual(core.refresh_usage(force=True)[personal["id"]], "claude-cache")
        finally:
            core._fetch_profile = real_profile

    def test_plan_is_not_rechecked_within_thirty_minutes(self):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        pro = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_pro")})
        calls = []
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self._payload()
        real_profile, core._fetch_profile = core._fetch_profile, lambda t: (calls.append(t), self._profile_reply(
            "claude_pro", "default_claude_pro"))[1]
        try:
            self.assertEqual(core.refresh_usage(force=True)[pro["id"]], "fetched")
            self.assertEqual(len(calls), 1)
            # make the usage stale enough to be read again, but keep the plan check time
            entry = core._read_usage_file()[pro["id"]]
            core._store_usage(pro["id"], {**entry, "fetchedAt": time.time() - 3600})
            self.assertEqual(core.refresh_usage()[pro["id"]], "fetched")
        finally:
            core._fetch_usage, core._fetch_profile = real_fetch, real_profile
        self.assertEqual(len(calls), 1)  # not asked again inside PLAN_CHECK_S

    def test_profile_failure_does_not_break_usage(self):
        core.save_profile(core.read_live())
        self._put_claude_cache("acc-1", self._payload())
        pro = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_pro")})
        real_fetch, core._fetch_usage = core._fetch_usage, lambda t: self._payload(session=45)
        real_profile, core._fetch_profile = core._fetch_profile, lambda t: (_ for _ in ()).throw(OSError("offline"))
        try:
            self.assertEqual(core.refresh_usage(force=True)[pro["id"]], "fetched")
        finally:
            core._fetch_usage, core._fetch_profile = real_fetch, real_profile
        self.assertEqual(core._read_usage_file()[pro["id"]]["session"]["pct"], 45.0)
        self.assertEqual(self._plan_of(pro["id"]), "Pro")

    def test_update_saved_plan_reports_no_change(self):
        core.save_profile(core.read_live())
        pro = core.save_profile({"claudeAiOauth": oauth("t1"), "oauthAccount": acct(2, "claude_pro")})
        self.assertTrue(core.update_saved_plan(pro["id"], self._profile_reply("claude_max", "default_claude_max_5x")))
        self.assertFalse(core.update_saved_plan(pro["id"], self._profile_reply("claude_max", "default_claude_max_5x")))
        self.assertFalse(core.update_saved_plan(pro["id"], {}))  # no organization in the reply
        self.assertEqual(self._plan_of(pro["id"]), "Max 5x")

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
        outcome = unittest.main(verbosity=2, exit=False).result
    finally:
        import shutil
        shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(0 if outcome.wasSuccessful() else 1)
