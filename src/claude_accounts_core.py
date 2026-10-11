"""Core logic for Claude Accounts: save, list and switch the Claude Code login.

What a "login" is on this machine:
  <claude dir>/.credentials.json  ->  key "claudeAiOauth"  (OAuth tokens)
  ~/.claude.json                  ->  key "oauthAccount"   (who the tokens belong to)
Switching accounts means replacing exactly those two values. Everything else
(skills, memory, CLAUDE.md, history, MCP logins) stays shared.

Saved logins are encrypted with Windows DPAPI (only this Windows user can read them).
Paths can be redirected for testing with CA_CLAUDE_DIR and CA_STORE.
"""
import copy
import ctypes
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

__version__ = "1.1.0"

APP_DIR = Path(__file__).resolve().parent
HOME = Path(os.environ.get("USERPROFILE") or Path.home())

_override = os.environ.get("CA_CLAUDE_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
CLAUDE_DIR = Path(_override) if _override else HOME / ".claude"
# Same rule Claude Code (and claude-swap) use: a legacy <config dir>/.config.json wins; otherwise
# CLAUDE_CONFIG_DIR/.claude.json when that variable is set, else ~/.claude.json.
if (CLAUDE_DIR / ".config.json").exists():
    CLAUDE_JSON = CLAUDE_DIR / ".config.json"
else:
    CLAUDE_JSON = (CLAUDE_DIR / ".claude.json") if _override else (HOME / ".claude.json")
CREDS = CLAUDE_DIR / ".credentials.json"

STORE = Path(os.environ.get("CA_STORE") or HOME / ".claude-accounts")  # saved logins live here, never next to the code
PROFILES = STORE / "profiles"
BACKUPS = STORE / "backups"
JOURNAL = STORE / "switch.journal"
KEEP_BACKUPS = 10

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


class SwitchError(Exception):
    """A problem the user can read as-is."""


# --------------------------------------------------------------------------- DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32
_crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _blob_in(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(data: bytes) -> bytes:
    src, keep = _blob_in(data)
    out = _BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(src), "ClaudeAccounts", None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def unprotect(data: bytes) -> bytes:
    src, keep = _blob_in(data)
    out = _BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _encode(obj) -> bytes:
    return protect(json.dumps(obj).encode("utf-8"))


def _decode(raw: bytes):
    return json.loads(unprotect(raw).decode("utf-8"))


# --------------------------------------------------------------------------- file helpers
def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None


def read_json(path: Path, default=None):
    """Read JSON, tolerating a moment where Claude Code is mid-write."""
    for attempt in range(5):
        text = _read_text(path)
        if text is None:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            time.sleep(0.2)
    raise SwitchError(f"{path.name} could not be read (it may be mid-write). Try again in a moment.")


def _replace(tmp: Path, dest: Path):
    for attempt in range(30):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            time.sleep(0.2)
    tmp.unlink(missing_ok=True)
    raise SwitchError(f"{dest.name} is locked by another program. Close it and try again.")


def write_json(path: Path, obj, like_text=None):
    """Atomic write that keeps the file's existing style (indented or compact)."""
    indented = like_text is None or "\n" in like_text.strip()
    tmp = path.with_name(f"{path.name}.ca-{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    if indented:
        text = json.dumps(obj, indent=2, ensure_ascii=False)
    else:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, path)


def _write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    _replace(tmp, path)


# --------------------------------------------------------------------------- Claude Code's locks
# Claude Code guards token refresh and .claude.json writes with `proper-lockfile` directory locks.
# Holding the same locks while we swap means a swap can never land inside a token refresh.
# Protocol as documented and verified against the Claude Code bundle by claude-swap
# (github.com/realiti4/claude-swap, src/claude_swap/claude_locks.py); the `.oauth_refresh.lock`
# name was also confirmed inside the claude.exe installed on this machine.
CREDENTIALS_STALE_S = 60.0   # a credential lock younger than this belongs to a live holder
CONFIG_STALE_S = 10.0
TOUCH_EVERY_S = 3.0
LOCK_WAIT_S = 9.0
_lock_state = threading.local()


@contextmanager
def _dir_lock(lock_dir: Path, stale_after: float):
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            os.mkdir(lock_dir)
            break
        except FileExistsError:
            pass
        if time.monotonic() - start > LOCK_WAIT_S:
            raise SwitchError("Claude Code is refreshing its login right now. Try again in a few seconds.")
        try:
            held_since = os.stat(lock_dir).st_mtime
        except FileNotFoundError:
            continue
        if time.time() - held_since > stale_after:  # dead holder: remove and retake
            try:
                os.rmdir(lock_dir)
            except OSError:
                time.sleep(0.05)
            continue
        time.sleep(0.25 + random.random() * 0.25)

    stop = threading.Event()

    def keep_alive():
        while not stop.wait(TOUCH_EVERY_S):
            try:
                os.utime(lock_dir)
            except OSError:
                return

    toucher = threading.Thread(target=keep_alive, daemon=True)
    toucher.start()
    try:
        yield
    finally:
        stop.set()
        toucher.join(timeout=1.0)
        try:
            os.rmdir(lock_dir)
        except OSError:
            pass


@contextmanager
def claude_locks():
    """Hold Claude Code's credential-refresh locks, then its config lock (same order Claude Code uses)."""
    if getattr(_lock_state, "held", False):
        yield
        return
    with _dir_lock(CLAUDE_DIR / ".oauth_refresh.lock", CREDENTIALS_STALE_S), \
            _dir_lock(CLAUDE_DIR.parent / (CLAUDE_DIR.name + ".lock"), CREDENTIALS_STALE_S), \
            _dir_lock(CLAUDE_JSON.parent / (CLAUDE_JSON.name + ".lock"), CONFIG_STALE_S):
        _lock_state.held = True
        try:
            yield
        finally:
            _lock_state.held = False


# --------------------------------------------------------------------------- live login
def read_live():
    """The login Claude Code is using right now, or None if signed out."""
    creds = read_json(CREDS, {}) or {}
    cfg = read_json(CLAUDE_JSON, {}) or {}
    oauth, acct = creds.get("claudeAiOauth"), cfg.get("oauthAccount")
    if not oauth or not acct or not oauth.get("refreshToken"):
        return None
    return {"claudeAiOauth": oauth, "oauthAccount": acct}


def account_key(acct: dict) -> str:
    return f"{acct.get('accountUuid')}:{acct.get('organizationUuid')}"


def profile_id(acct: dict) -> str:
    return hashlib.sha1(account_key(acct).encode()).hexdigest()[:12]


def plan_label(acct: dict, oauth: dict) -> str:
    kind = (acct.get("organizationType") or "").replace("claude_", "")
    tier = acct.get("organizationRateLimitTier") or oauth.get("rateLimitTier") or ""
    m = re.search(r"(\d+)x", tier)
    if kind == "max":
        return f"Max {m.group(1)}x" if m else "Max"
    if kind in ("pro", "team", "enterprise"):
        return kind.title()
    return (oauth.get("subscriptionType") or kind or "Claude").title()


def is_team(acct: dict) -> bool:
    kind = acct.get("organizationType") or ""
    return "team" in kind or "enterprise" in kind


def describe(entry: dict) -> dict:
    acct, oauth = entry["oauthAccount"], entry["claudeAiOauth"]
    plan = plan_label(acct, oauth)
    org = acct.get("organizationName") or ""
    detail = f"{plan} · {org}" if is_team(acct) and org else plan
    return {"email": acct.get("emailAddress") or "unknown email", "plan": plan, "detail": detail,
            "team": is_team(acct)}


def refresh_expired(oauth: dict) -> bool:
    exp = oauth.get("refreshTokenExpiresAt")
    return bool(exp) and exp < time.time() * 1000


def refresh_days_left(oauth: dict):
    """Days until the stored sign-in dies, or None if Claude Code didn't record a date."""
    exp = oauth.get("refreshTokenExpiresAt")
    return (exp - time.time() * 1000) / 86_400_000 if exp else None


# --------------------------------------------------------------------------- saved profiles
def list_profiles():
    out = []
    PROFILES.mkdir(parents=True, exist_ok=True)
    for path in PROFILES.glob("*.dat"):
        try:
            data = _decode(path.read_bytes())
        except Exception:
            continue
        data["id"] = path.stem
        out.append(data)
    out.sort(key=lambda d: d.get("createdAt", ""))
    return out


def get_profile(pid: str):
    return next((p for p in list_profiles() if p["id"] == pid), None)


def find_profile(name_or_id: str):
    items = list_profiles()
    for p in items:
        if p["id"] == name_or_id or p["name"].lower() == name_or_id.lower():
            return p
    return None


def suggest_name(acct: dict, taken=None) -> str:
    taken = {t.lower() for t in (taken if taken is not None else (p["name"] for p in list_profiles()))}
    kind = acct.get("organizationType") or ""
    base = "Team" if "team" in kind else "Enterprise" if "enterprise" in kind else "Personal"
    name, i = base, 2
    while name.lower() in taken:
        name, i = f"{base} {i}", i + 1
    return name


def save_profile(live: dict, name=None) -> dict:
    """Create or update the saved copy of a login. Existing names are kept unless one is given."""
    pid = profile_id(live["oauthAccount"])
    old = get_profile(pid)
    now = datetime.now().isoformat()  # microseconds, so cards keep the order they were added in
    if old:
        entry = {k: v for k, v in old.items() if k != "id"}
        entry["name"] = name or old["name"]
    else:
        taken = {p["name"].lower() for p in list_profiles()}
        chosen = (name or "").strip() or suggest_name(live["oauthAccount"], taken)
        if chosen.lower() in taken:
            raise SwitchError(f'There is already an account named "{chosen}".')
        entry = {"name": chosen, "createdAt": now}
    entry["updatedAt"] = now
    entry["claudeAiOauth"] = copy.deepcopy(live["claudeAiOauth"])
    entry["oauthAccount"] = copy.deepcopy(live["oauthAccount"])
    _write_bytes(PROFILES / f"{pid}.dat", _encode(entry))
    entry["id"] = pid
    return entry


def sync_back(live: dict):
    """Refresh the saved copy with the live tokens (Claude Code rotates them while you work)."""
    old = get_profile(profile_id(live["oauthAccount"]))
    if not old:
        return None
    if old["claudeAiOauth"] == live["claudeAiOauth"] and old["oauthAccount"] == live["oauthAccount"]:
        return old
    return save_profile(live)


def rename_profile(pid: str, name: str):
    name = name.strip()
    if not name:
        raise SwitchError("The name can't be empty.")
    prof = get_profile(pid)
    if not prof:
        raise SwitchError("That account is no longer saved.")
    if any(p["name"].lower() == name.lower() and p["id"] != pid for p in list_profiles()):
        raise SwitchError(f'There is already an account named "{name}".')
    prof["name"] = name
    prof.pop("id")
    _write_bytes(PROFILES / f"{pid}.dat", _encode(prof))


def delete_profile(pid: str):
    (PROFILES / f"{pid}.dat").unlink(missing_ok=True)
    data = _read_usage_file()
    if pid in data:
        del data[pid]
        write_json(USAGE_FILE, data)


# --------------------------------------------------------------------------- backups
def backup_live(live):
    if not live:
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    path = BACKUPS / f"{datetime.now():%Y%m%d-%H%M%S-%f}.dat"
    _write_bytes(path, _encode(live))
    for old in sorted(BACKUPS.glob("*.dat"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return path


# --------------------------------------------------------------------------- applying a login
def apply_login(oauth: dict, acct: dict):
    """Write a login into Claude Code's files, under Claude Code's own locks. Rolls back if the second file fails."""
    with claude_locks():
        creds_text = _read_text(CREDS)
        creds = json.loads(creds_text) if creds_text else {}
        cfg_text = _read_text(CLAUDE_JSON)
        cfg = read_json(CLAUDE_JSON, {}) if cfg_text else {}
        if not isinstance(creds, dict) or not isinstance(cfg, dict):
            raise SwitchError("Claude Code's config files have an unexpected format. Nothing was changed.")
        old_creds = copy.deepcopy(creds)

        creds["claudeAiOauth"] = copy.deepcopy(oauth)
        write_json(CREDS, creds, creds_text)
        try:
            cfg["oauthAccount"] = copy.deepcopy(acct)
            cfg.pop("cachedExtraUsageDisabledReason", None)  # the only account-level cache that isn't keyed per account
            write_json(CLAUDE_JSON, cfg, cfg_text)
        except Exception:
            write_json(CREDS, old_creds, creds_text)
            raise


def switch_to(pid: str) -> dict:
    """Make a saved account the active one. Returns a result dict for the UI."""
    target = get_profile(pid)
    if not target:
        raise SwitchError("That account is no longer saved.")
    result = {"name": target["name"], "changed": True, "autosaved": None, "reverted": False}

    # Everything from reading the live login to writing the new one happens under Claude Code's locks,
    # so a token refresh can't slip in between "save the old tokens" and "write the new ones".
    with claude_locks():
        live = read_live()
        if live:
            if profile_id(live["oauthAccount"]) == pid:
                sync_back(live)
                result["changed"] = False
                return result
            if get_profile(profile_id(live["oauthAccount"])):
                sync_back(live)
            else:
                result["autosaved"] = save_profile(live)["name"]
            backup_live(live)

        if refresh_expired(target["claudeAiOauth"]):
            raise SwitchError(f'The saved sign-in for "{target["name"]}" has expired. '
                              f'Use "Add account" and sign in with it again.')

        # Journal: if this process dies between the two file writes, recover() finishes the job.
        _write_bytes(JOURNAL, json.dumps({"target": pid, "at": time.time()}).encode())
        try:
            apply_login(target["claudeAiOauth"], target["oauthAccount"])
        finally:
            # A clean failure has already rolled back, so nothing is left to finish. Only a process that
            # dies mid-switch never reaches this line, and that is exactly what the journal is for.
            JOURNAL.unlink(missing_ok=True)
    return result


def settle_check(pid: str) -> bool:
    """After a switch: make sure a running Claude Code didn't put the old account back.

    Returns True when the target account is the live one. Slow (about a second), so callers run it in
    the background; the switch itself is already done and in effect.
    """
    target = get_profile(pid)
    if not target:
        return False
    for attempt in range(2):
        time.sleep(1.2)
        now = read_live()
        if now and account_key(now["oauthAccount"]) == account_key(target["oauthAccount"]):
            return True
        apply_login(target["claudeAiOauth"], target["oauthAccount"])
    time.sleep(1.2)
    now = read_live()
    return bool(now and account_key(now["oauthAccount"]) == account_key(target["oauthAccount"]))


def recover():
    """Finish a switch that was interrupted (power loss, killed process). Safe to call at any time."""
    try:
        info = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if time.time() - info.get("at", 0) < 20:  # a switch is probably still running right now
        return None
    target = get_profile(info.get("target", ""))
    JOURNAL.unlink(missing_ok=True)
    if not target or refresh_expired(target["claudeAiOauth"]):
        return None
    apply_login(target["claudeAiOauth"], target["oauthAccount"])
    return target["name"]


def restore_live(live):
    """Put a previously read login back (used when a sign-in attempt is cancelled)."""
    if live:
        apply_login(live["claudeAiOauth"], live["oauthAccount"])


# --------------------------------------------------------------------------- usage (read-only)
# Same endpoint Claude Code and claude-swap use (GET /api/oauth/usage). Rules that keep it safe:
#  - the active account's numbers come from Claude Code's own cache in .claude.json (no network at all);
#  - another account is only queried while its saved access token is still valid. We never refresh a saved
#    account's token just to read usage, because that rotates its refresh token;
#  - otherwise the last known numbers are shown with an "as of" age;
#  - at most one request per account every few minutes, with backoff after an error or rate limit.
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
USAGE_FILE = STORE / "usage.json"   # percentages only, no tokens
USAGE_MIN_GAP_S = 180
USAGE_BACKOFF_S = 600
USAGE_LIVE_S = 15 * 60              # younger than this is "current"; older gets an "as of" note
CLAUDE_CACHE_MAX_AGE_S = 10 * 60


def _fetch_usage(access_token: str) -> dict:
    if os.environ.get("CA_NO_NETWORK"):
        raise OSError("network disabled")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {access_token}", "anthropic-beta": OAUTH_BETA, "User-Agent": "claude-accounts/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _iso_epoch(text):
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def parse_usage(data):
    """Reduce the usage response to what we show. None when it has no usable numbers."""
    out = {}
    for key, name in (("five_hour", "session"), ("seven_day", "week")):
        w = (data or {}).get(key)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            out[name] = {"pct": float(w["utilization"]), "resetsAt": _iso_epoch(w.get("resets_at"))}
    models = []
    for lim in (data or {}).get("limits") or []:
        if isinstance(lim, dict) and lim.get("kind") == "weekly_scoped" and isinstance(lim.get("percent"), (int, float)):
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            if model:
                models.append({"name": model, "pct": float(lim["percent"]), "resetsAt": _iso_epoch(lim.get("resets_at"))})
    if models:
        out["models"] = models
    return out if ("session" in out or "week" in out) else None


def _read_usage_file():
    try:
        data = read_json(USAGE_FILE, {})
    except SwitchError:
        return {}
    return data if isinstance(data, dict) else {}


def _store_usage(pid, entry):
    data = _read_usage_file()
    data[pid] = entry
    write_json(USAGE_FILE, data)


def _claude_cached_usage(live):
    """(parsed usage, fetched-at seconds) from Claude Code's own cache, if it belongs to the live account."""
    cfg = read_json(CLAUDE_JSON, {}) or {}
    cache = cfg.get("cachedUsageUtilization")
    if not isinstance(cache, dict) or cache.get("accountUuid") != live["oauthAccount"].get("accountUuid"):
        return None
    parsed = parse_usage(cache.get("utilization"))
    return (parsed, (cache.get("fetchedAtMs") or 0) / 1000) if parsed else None


def refresh_usage(force=False):
    """Update stored usage where it is safe. Returns {profile id: status} (statuses are for logs/tests)."""
    now = time.time()
    live = read_live()
    live_id = profile_id(live["oauthAccount"]) if live else None
    data = _read_usage_file()
    report = {}
    for p in list_profiles():
        pid = p["id"]
        entry = dict(data.get(pid) or {})
        if pid == live_id:
            cached = _claude_cached_usage(live)
            if cached and now - cached[1] < CLAUDE_CACHE_MAX_AGE_S:
                if cached[1] >= entry.get("fetchedAt", 0):
                    data[pid] = {**cached[0], "fetchedAt": cached[1], "source": "claude"}
                report[pid] = "claude-cache"
                continue
        oauth = live["claudeAiOauth"] if pid == live_id else p["claudeAiOauth"]
        if entry.get("retryAt", 0) > now:
            report[pid] = "backoff"
            continue
        if not force and now - entry.get("fetchedAt", 0) < USAGE_MIN_GAP_S:
            report[pid] = "recent"
            continue
        if not oauth.get("accessToken") or (oauth.get("expiresAt") or 0) < (now + 60) * 1000:
            report[pid] = "token-expired"  # not an error: we just don't touch the token
            continue
        try:
            parsed = parse_usage(_fetch_usage(oauth["accessToken"]))
            if not parsed:
                raise ValueError("no usage in response")
            data[pid] = {**parsed, "fetchedAt": now, "source": "api"}
            report[pid] = "fetched"
        except urllib.error.HTTPError as exc:
            wait = USAGE_BACKOFF_S
            try:
                wait = max(wait, float(exc.headers.get("Retry-After") or 0)) if exc.code == 429 else wait
            except (TypeError, ValueError, AttributeError):
                pass
            entry["retryAt"], entry["error"] = now + wait, f"http-{exc.code}"
            data[pid] = entry
            report[pid] = f"http-{exc.code}"
        except Exception as exc:  # offline, timeout, odd response: keep last known, try again later
            entry["retryAt"], entry["error"] = now + USAGE_BACKOFF_S, type(exc).__name__
            data[pid] = entry
            report[pid] = type(exc).__name__
    if data != _read_usage_file():
        write_json(USAGE_FILE, data)
    return report


def fmt_duration(sec):
    sec = max(0, int(sec))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    return f"{days}d {hours}h" if days else f"{hours}h {mins}m" if hours else f"{mins}m"


def fmt_ago(sec):
    if sec < 90:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)} min ago"
    return f"{int(sec // 3600)} h ago" if sec < 86400 else f"{int(sec // 86400)} d ago"


def fmt_clock(epoch):
    t = datetime.fromtimestamp(epoch)
    return t.strftime("%a ") + t.strftime("%I:%M %p").lstrip("0")


def _window_view(w, now):
    if not w:
        return None
    resets = w.get("resetsAt")
    over = bool(resets) and resets <= now  # that window has rolled over since we looked: the old % no longer applies
    return {"pct": None if over else int(round(w["pct"])), "resetsAt": resets, "over": over}


def usage_view(entry, now=None):
    """Display-ready usage for a UI. None when we know nothing about this account yet."""
    now = now or time.time()
    if not entry or not (entry.get("session") or entry.get("week")):
        return None
    session, week = _window_view(entry.get("session"), now), _window_view(entry.get("week"), now)
    age = max(0, now - entry.get("fetchedAt", now))
    parts, resets = [], []
    for label, w in (("Session", session), ("Week", week)):
        if not w:
            continue
        parts.append(f"{label} reset" if w["over"] else f"{label} {w['pct']}%")
        if not w["over"] and w["resetsAt"]:
            left = w["resetsAt"] - now
            resets.append(f"{label} resets " + (f"in {fmt_duration(left)}" if left < 86400 else fmt_clock(w["resetsAt"])))
    models = [{"name": m["name"], "pct": int(round(m["pct"]))} for m in entry.get("models") or [] if m.get("pct")]
    parts += [f"{m['name']} {m['pct']}%" for m in models]
    return {"session": session, "week": week, "models": models, "ageS": int(age), "stale": age > USAGE_LIVE_S,
            "text": " · ".join(parts), "resets": " · ".join(resets),
            "note": f"as of {fmt_ago(age)}" if age > USAGE_LIVE_S else ""}


# --------------------------------------------------------------------------- state for the UIs
def state() -> dict:
    """Everything a UI needs, with no secrets in it. Offline: usage is whatever was last stored."""
    live = read_live()
    if live:
        sync_back(live)  # keep the saved copy's tokens current while this account is active
    live_id = profile_id(live["oauthAccount"]) if live else None
    stored = _read_usage_file()
    if live:  # Claude Code keeps its own usage numbers for the active account; adopt them for free
        cached = _claude_cached_usage(live)
        if cached and cached[1] > (stored.get(live_id) or {}).get("fetchedAt", 0):
            stored[live_id] = {**cached[0], "fetchedAt": cached[1], "source": "claude"}
            _store_usage(live_id, stored[live_id])
    items, saved_ids = [], set()
    for p in list_profiles():
        saved_ids.add(p["id"])
        info = describe(p)
        items.append({"id": p["id"], "name": p["name"], "email": info["email"], "plan": info["plan"],
                      "detail": info["detail"], "team": info["team"], "active": p["id"] == live_id,
                      "expired": refresh_expired(p["claudeAiOauth"]),
                      "daysLeft": refresh_days_left(p["claudeAiOauth"]),
                      "usage": usage_view(stored.get(p["id"]))})
    unsaved = None
    if live and live_id not in saved_ids:
        info = describe(live)
        unsaved = {"email": info["email"], "plan": info["plan"], "detail": info["detail"], "team": info["team"]}
    return {"signedIn": bool(live), "active": live_id if live_id in saved_ids else None,
            "unsaved": unsaved, "profiles": items}


# --------------------------------------------------------------------------- add an account (browser sign-in)
def add_login(cancelled=lambda: False, timeout=600):
    """Run Claude Code's own sign-in, then save whatever account it ends up with.

    `cancelled` is polled; when it returns True the sign-in is aborted and the previous login restored.
    Returns {"status": "new"|"known"|"same"|"cancelled"|"failed"|"timeout", ...}. Never logs out first
    (a logout can revoke the saved login of the account being left).
    """
    before = read_live()
    if before:
        if get_profile(profile_id(before["oauthAccount"])):
            sync_back(before)
        else:
            save_profile(before)
        backup_live(before)
    proc, log = start_login()
    outcome, started = None, time.time()
    while outcome is None:
        if cancelled():
            outcome = "cancelled"
        elif proc.poll() is not None:
            outcome = "done"
        elif time.time() - started > timeout:
            outcome = "timeout"
        else:
            time.sleep(0.3)
    if proc.poll() is None:
        kill_tree(proc)
    try:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
    except OSError:
        tail = []
    log.unlink(missing_ok=True)

    after = read_live()
    unchanged = bool(before and after and account_key(before["oauthAccount"]) == account_key(after["oauthAccount"])
                     and before["claudeAiOauth"] == after["claudeAiOauth"])
    if outcome != "done" or proc.returncode != 0:
        if before and not unchanged:
            restore_live(before)  # a half-finished sign-in must never log you out of the previous account
        return {"status": "failed" if outcome == "done" else outcome, "detail": tail[0] if tail else ""}
    if not after:
        return {"status": "failed", "detail": "Sign-in finished but no login was found."}

    pid = profile_id(after["oauthAccount"])
    known = get_profile(pid)
    if known:
        save_profile(after)  # refreshed tokens
        same = bool(before and account_key(before["oauthAccount"]) == account_key(after["oauthAccount"]))
        return {"status": "same" if same else "known", "id": pid, "name": known["name"]}
    saved = save_profile(after)  # saved right away under a suggested name; the UI may offer a rename
    return {"status": "new", "id": pid, "name": saved["name"]}


# --------------------------------------------------------------------------- self-check
def doctor():
    """Check the assumptions this tool relies on, so a Claude Code update that breaks them is noticed."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    creds = read_json(CREDS, {}) or {}
    add("Login file found", CREDS.exists() and "claudeAiOauth" in creds, str(CREDS))
    cfg = read_json(CLAUDE_JSON, {}) or {}
    add("Account record found", "oauthAccount" in cfg, str(CLAUDE_JSON))
    try:
        add("Saved logins encrypted", _decode(_encode({"t": 1})) == {"t": 1}, "Windows DPAPI")
    except Exception as exc:
        add("Saved logins encrypted", False, str(exc))
    exe = claude_exe()
    add("claude command found", exe, exe or "not on PATH (sign-in from this tool needs it)")
    add("Saved accounts", True, f"{len(list_profiles())} saved")
    lock_ok, lock_where = _lock_name_present()
    add("Claude Code's lock names unchanged", lock_ok, lock_where)
    return checks


def _claude_binaries():
    ext = HOME / ".vscode" / "extensions"
    found = sorted(ext.glob("anthropic.claude-code-*/resources/native-binary/claude.exe"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:1] if ext.exists() else []
    return [str(p) for p in found]


def _lock_name_present():
    """Is Claude Code still using the lock name we rely on? Cached per binary because the scan is large."""
    cache_file = STORE / "doctor-cache.json"
    cache = read_json(cache_file, {}) or {}
    bins = _claude_binaries()
    if not bins:
        return True, "no VS Code binary found to inspect"
    path = bins[0]
    st = os.stat(path)
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    if cache.get("key") != key:
        needle, hit, tail = b".oauth_refresh.lock", False, b""
        with open(path, "rb") as fh:
            while not hit:
                chunk = fh.read(8 << 20)
                if not chunk:
                    break
                hit = needle in tail + chunk
                tail = chunk[-len(needle):]
        cache = {"key": key, "hit": hit}
        try:
            write_json(cache_file, cache)
        except Exception:
            pass
    return cache["hit"], os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))


# --------------------------------------------------------------------------- Claude CLI
def claude_exe():
    return shutil.which("claude")


def _cli_env():
    env = os.environ.copy()
    if _override:
        env["CLAUDE_CONFIG_DIR"] = str(CLAUDE_DIR)
    return env


def cli_status(timeout=30):
    """Ask the Claude CLI who it thinks is signed in. None if it can't be run."""
    exe = claude_exe()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=timeout,
                           env=_cli_env(), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return json.loads(r.stdout)
    except Exception:
        return None


def verify_active(acct: dict):
    """True/False if the CLI agrees with `acct`, None if it couldn't be checked."""
    status = cli_status()
    if not status or not status.get("loggedIn"):
        return None if status is None else False
    return status.get("orgId") == acct.get("organizationUuid") and status.get("email") == acct.get("emailAddress")


def start_login(email=None):
    """Start `claude auth login` (opens the browser). Returns (process, logfile path)."""
    exe = claude_exe()
    if not exe:
        raise SwitchError("The `claude` command was not found, so sign-in can't be started from here.")
    cmd = [exe, "auth", "login", "--claudeai"] + (["--email", email] if email else [])
    STORE.mkdir(parents=True, exist_ok=True)
    log = STORE / "login.log"
    handle = open(log, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                            env=_cli_env(), creationflags=NO_WINDOW)
    handle.close()
    return proc, log


def kill_tree(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
