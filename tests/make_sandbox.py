"""Create a sandbox Claude login + saved accounts (all fake) for testing the VS Code extension safely."""
import json, os, sys, time
from pathlib import Path

sb = Path(sys.argv[1])
(sb / "claude").mkdir(parents=True, exist_ok=True)
os.environ["CA_CLAUDE_DIR"] = str(sb / "claude")
os.environ["CA_STORE"] = str(sb / "store")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import claude_accounts_core as core


def acct(n, kind, org, email):
    return {"accountUuid": f"00000000-0000-0000-0000-00000000000{n}", "organizationUuid": f"1111111{n}-0000-0000-0000-000000000000",
            "emailAddress": email, "organizationType": kind, "organizationName": org,
            "organizationRateLimitTier": "default_claude_max_5x", "displayName": f"Fake {n}"}


def oauth(tag, days):
    now = time.time() * 1000
    return {"accessToken": f"fake-at-{tag}", "refreshToken": f"fake-rt-{tag}", "expiresAt": now + 6 * 3600_000,
            "refreshTokenExpiresAt": now + days * 86400_000, "scopes": ["user:inference", "user:profile"],
            "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"}


personal = {"claudeAiOauth": oauth("p", 25), "oauthAccount": acct(1, "claude_max", "x", "alex@example.invalid")}
team = {"claudeAiOauth": oauth("t", 20), "oauthAccount": acct(2, "claude_team", "Acme Inc", "alex@acme.example.invalid")}
soon = {"claudeAiOauth": oauth("s", 3), "oauthAccount": acct(3, "claude_pro", "x", "client@example.invalid")}
core.save_profile(personal, "Personal")
core.save_profile(team, "Team")
if len(sys.argv) > 2 and sys.argv[2] == "with-expiring":
    core.save_profile(soon, "Client B")
core.CREDS.write_text(json.dumps({"claudeAiOauth": personal["claudeAiOauth"]}), encoding="utf-8")
core.CLAUDE_JSON.write_text(json.dumps({"numStartups": 1, "oauthAccount": personal["oauthAccount"]}), encoding="utf-8")
(sb / "workspace").mkdir(exist_ok=True)
(sb / "workspace" / "hello.txt").write_text("sandbox workspace\n", encoding="utf-8")
print("sandbox ready:", sb)
