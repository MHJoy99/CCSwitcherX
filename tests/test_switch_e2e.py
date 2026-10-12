"""End-to-end switch test in a sandbox: never touches the real login.

A fake token server rotates refresh tokens like Anthropic's (the old one dies on use). After every step we
check that every login (live + saved) still holds a refresh token the server would accept, and that the
live login is the account we expect.
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path

SB = Path(tempfile.mkdtemp(prefix="ca-switch-e2e-"))
os.environ["CA_CLAUDE_DIR"] = str(SB / "claude")
os.environ["CA_STORE"] = str(SB / "store")
os.environ["CA_NO_NETWORK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import claude_accounts_core as core  # noqa: E402

NOW = lambda: int(time.time() * 1000)
VALID = {}          # account -> the one refresh token the server accepts right now
GEN = {}
SLOW = {"s": 0.0}
CALLS = []


def acct(n, kind):
    return {"accountUuid": f"acc-{n}", "organizationUuid": f"org-{n}", "emailAddress": f"{n}@example.invalid",
            "organizationType": kind, "organizationRateLimitTier": "default_claude_ai", "displayName": n}


def tokens(n, expired=False):
    GEN[n] = GEN.get(n, 0) + 1
    rt = f"rt-{n}-{GEN[n]}"
    VALID[n] = rt
    return {"accessToken": f"at-{n}-{GEN[n]}", "refreshToken": rt,
            "expiresAt": NOW() + (-3600_000 if expired else 3600_000),
            "refreshTokenExpiresAt": NOW() + 20 * 86400_000, "scopes": ["user:inference", "user:profile"],
            "subscriptionType": "pro", "rateLimitTier": "default_claude_ai"}


def fake_post(body):
    CALLS.append(body["refresh_token"])
    time.sleep(SLOW["s"])
    owner = next((a for a, rt in VALID.items() if rt == body["refresh_token"]), None)
    if owner is None:
        import urllib.error
        raise urllib.error.HTTPError(core.TOKEN_URL, 400, "invalid_grant (spent token)", {}, None)
    GEN[owner] += 1
    VALID[owner] = f"rt-{owner}-{GEN[owner]}"
    return {"access_token": f"at-{owner}-{GEN[owner]}", "refresh_token": VALID[owner], "expires_in": 28800}


core._post_token = fake_post
core._fetch_usage = lambda at: {"five_hour": {"utilization": 10, "resets_at": None},
                                "seven_day": {"utilization": 20, "resets_at": None}}
core._fetch_profile = lambda at: {"organization": {"organization_type": "claude_pro", "rate_limit_tier": "default_claude_ai"}}

results = []


def check(step, expect_live):
    live = core.read_live()
    live_name = live["oauthAccount"]["displayName"] if live else None
    problems = []
    if live_name != expect_live:
        problems.append(f"live is {live_name}, expected {expect_live}")
    held = {}
    if live:
        held[live_name] = [("live", live["claudeAiOauth"]["refreshToken"])]
    for p in core.list_profiles():
        n = p["oauthAccount"]["displayName"]
        if n == live_name:
            continue  # the saved copy of the live account is refreshed from live on the next sync
        held.setdefault(n, []).append(("saved", p["claudeAiOauth"]["refreshToken"]))
    for n, lst in held.items():
        for where, rt in lst:
            if rt != VALID[n]:
                problems.append(f"{n} {where} holds spent token {rt} (server wants {VALID[n]})")
    cfg = json.loads(core.CLAUDE_JSON.read_text(encoding="utf-8"))
    creds = json.loads(core.CREDS.read_text(encoding="utf-8"))
    if cfg.get("projects") != {"C:\\x": {"history": ["keep"]}} or creds.get("mcpOAuth") != {"srv": "keep"}:
        problems.append("other Claude Code data was not preserved")
    results.append((step, not problems, problems))
    print(("PASS " if not problems else "FAIL ") + step + ("" if not problems else "  -> " + "; ".join(problems)))


# --- setup: A is live, B and C saved; B and C have expired access tokens (like the real 17 h case)
core.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
core.CREDS.write_text(json.dumps({"claudeAiOauth": tokens("A"), "mcpOAuth": {"srv": "keep"}}))
core.CLAUDE_JSON.write_text(json.dumps({"projects": {"C:\\x": {"history": ["keep"]}}, "oauthAccount": acct("A", "claude_pro")}))
idA = core.save_profile(core.read_live(), "A")["id"]
idB = core.save_profile({"claudeAiOauth": tokens("B", expired=True), "oauthAccount": acct("B", "claude_pro")}, "B")["id"]
idC = core.save_profile({"claudeAiOauth": tokens("C", expired=True), "oauthAccount": acct("C", "claude_pro")}, "C")["id"]
check("1 setup", "A")

r = core.refresh_usage(force=True)
assert r[idB] == "fetched" and r[idC] == "fetched", r
check("2 usage refresh renews B and C (tokens rotated on the fake server)", "A")

# Claude Code rotates A's own token while A is in use
c = json.loads(core.CREDS.read_text()); c["claudeAiOauth"] = tokens("A"); core.CREDS.write_text(json.dumps(c))
core.switch_to(idB)
check("3 switch A -> B (B gets its renewed token, A's latest token saved)", "B")

# Claude Code rotates B's token while B is live; a usage refresh must not touch live B
c = json.loads(core.CREDS.read_text()); c["claudeAiOauth"] = tokens("B"); core.CREDS.write_text(json.dumps(c))
n_calls = len(CALLS)
core.refresh_usage(force=True)
assert not any(t.startswith("rt-B") for t in CALLS[n_calls:]), "live account was renewed by us"
check("4 refresh while B is live leaves B to Claude Code", "B")

core.switch_to(idC)
check("5 switch B -> C", "C")
core.switch_to(idA)
check("6 switch C -> A (back to the first account)", "A")

# 7 race: a slow renewal of B is in flight while the user clicks Switch to B
p = core.get_profile(idB); p["claudeAiOauth"]["expiresAt"] = NOW() - 1000
core._write_bytes(core.PROFILES / f"{idB}.dat", core._encode({k: v for k, v in p.items() if k != "id"}))
SLOW["s"] = 2.0
t = threading.Thread(target=core.refresh_usage, kwargs={"force": True}); t.start()
time.sleep(0.4)  # renewal now holds the locks and has spent B's old token on the server
core.switch_to(idB)
t.join(); SLOW["s"] = 0.0
check("7 switch to B DURING a slow renewal of B (race)", "B")

for i in range(10):
    core.switch_to([idA, idC, idB][i % 3])
check("8 ten fast switches A/C/B", ["A", "C", "B"][9 % 3])

core.switch_to(idA)
check("9 final switch back to A", "A")

print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} steps passed; sandbox {SB}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
