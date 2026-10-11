"""Providers for Claude Accounts: point Claude Code at another model gateway and switch back.

A provider is a base URL, an API key and a model map (which model answers for Opus, Sonnet, Haiku and
subagents). Using one means writing a few lines into Claude Code's own settings file, <claude dir>/settings.json:

  env.ANTHROPIC_BASE_URL                      where requests go
  env.ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL, env.CLAUDE_CODE_SUBAGENT_MODEL   what each alias means
  apiKeyHelper                                a command Claude Code runs to get the key

The key itself is never written to settings.json: it stays encrypted (Windows DPAPI) in the store, and the helper
command (`claude_accounts.py key <id>`) hands it to Claude Code at run time. Claude Code reloads settings.json while
it runs, so a switch reaches the next message (about a second and a half later) with no restart.

Rules that keep it safe:
  - only the keys listed above are ever touched, everything else in settings.json stays exactly as it was;
  - the previous value of each of those keys is remembered and put back when you switch away;
  - the base URL and the credential helper are written in one atomic replace, so a base URL can never be live
    without a credential (Claude Code would otherwise send your claude.ai login to that URL);
  - a backup of settings.json (encrypted) is taken before every change and the result is verified, with rollback;
  - a settings.json that already holds ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY is refused, because that credential
    would win over the helper.
"""
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import claude_accounts_core as core
from claude_accounts_core import SwitchError

PROVIDERS = core.STORE / "providers"
ROUTE_FILE = core.STORE / "route.json"            # what we changed in settings.json (no secrets)
SETTINGS = core.CLAUDE_DIR / "settings.json"
SETTINGS_BACKUPS = core.STORE / "settings-backups"
ROUTE_LOCK = core.STORE / ".route.lock"
KEEP_BACKUPS = 10
SETTLE_SECONDS = 2.0   # Claude Code notices a settings.json edit after roughly 1.5 s (measured 2026-10-12)

MODEL_ENV = {"opus": "ANTHROPIC_DEFAULT_OPUS_MODEL", "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
             "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL", "subagent": "CLAUDE_CODE_SUBAGENT_MODEL"}
MANAGED_ENV = ("ANTHROPIC_BASE_URL",) + tuple(MODEL_ENV.values())
FOREIGN_CREDENTIALS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
HELPER_RE = re.compile(r'claude_accounts\.py"?\s+key\s+([0-9a-f]{12})\s*$')
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}$")
# Gateways behind Cloudflare often refuse the default Python user agent (error 1010); Claude Code's own is accepted.
TEST_UA = "claude-cli/2.1.296 (external, cli)"


# --------------------------------------------------------------------------- saved providers
def _normalize_url(url):
    text = (url or "").strip().rstrip("/")
    if text.lower().endswith("/v1"):   # Claude Code adds /v1/messages itself
        text = text[:-3].rstrip("/")
    m = re.match(r"^(https?)://([A-Za-z0-9.-]+)(:\d+)?(/[^\s?#@]*)?$", text)
    if not m:
        raise SwitchError("The address must look like https://gateway.example.com (no spaces, user name or query).")
    if m.group(1) == "http" and m.group(2).lower() not in ("localhost", "127.0.0.1"):
        raise SwitchError("Only https:// addresses are allowed (plain http:// only for localhost).")
    return text


def _clean_models(models):
    out = {}
    for tier in ("opus", "sonnet", "haiku", "subagent"):
        value = ((models or {}).get(tier) or "").strip()
        if tier == "subagent" and not value:
            value = ((models or {}).get("haiku") or "").strip()   # subagents default to the small model
        if not value:
            raise SwitchError(f"Pick a model for {tier.title()}.")
        if not MODEL_RE.match(value):
            raise SwitchError(f'"{value}" is not a valid model name.')
        out[tier] = value
    return out


def _clean_name(name, exclude_id=None):
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise SwitchError("Give the provider a name of 1 to 40 characters.")
    if any(p["name"].lower() == name.lower() and p["id"] != exclude_id for p in list_providers()):
        raise SwitchError(f'There is already a provider named "{name}".')
    return name


def list_providers():
    out = []
    PROVIDERS.mkdir(parents=True, exist_ok=True)
    for path in PROVIDERS.glob("*.dat"):
        try:
            data = core._decode(path.read_bytes())
        except Exception:
            continue
        data["id"] = path.stem
        out.append(data)
    out.sort(key=lambda d: d.get("createdAt", ""))
    return out


def get_provider(pid):
    return next((p for p in list_providers() if p["id"] == pid), None)


def find_provider(name_or_id):
    return next((p for p in list_providers()
                 if p["id"] == name_or_id or p["name"].lower() == (name_or_id or "").lower()), None)


def public(p):
    """A provider without its key, safe to hand to a UI."""
    return {"id": p["id"], "name": p["name"], "baseUrl": p["baseUrl"], "host": re.sub(r"^https?://", "", p["baseUrl"]),
            "models": dict(p["models"]), "keyOk": bool(p.get("apiKey")), "createdAt": p.get("createdAt")}


def _save(p):
    entry = {k: v for k, v in p.items() if k != "id"}
    core._write_bytes(PROVIDERS / f'{p["id"]}.dat', core._encode(entry))


def add_provider(name, base_url, api_key, models):
    key = (api_key or "").strip()
    if not key:
        raise SwitchError("Enter the API key for this provider.")
    now = datetime.now().isoformat()
    p = {"id": uuid.uuid4().hex[:12], "name": _clean_name(name), "baseUrl": _normalize_url(base_url), "apiKey": key,
         "models": _clean_models(models), "createdAt": now, "updatedAt": now}
    _save(p)
    return public(p)


def update_provider(pid, name=None, base_url=None, api_key=None, models=None):
    """Change a provider. A blank api_key keeps the saved one. If it is the provider in use, the change is applied now."""
    p = get_provider(pid)
    if not p:
        raise SwitchError("That provider is no longer saved.")
    if name is not None:
        p["name"] = _clean_name(name, exclude_id=pid)
    if base_url is not None:
        p["baseUrl"] = _normalize_url(base_url)
    if api_key is not None and api_key.strip():
        p["apiKey"] = api_key.strip()
    if models is not None:
        p["models"] = _clean_models(models)
    p["updatedAt"] = datetime.now().isoformat()
    _save(p)
    if current_route().get("id") == pid:
        activate(pid)
    return public(p)


def delete_provider(pid):
    if current_route().get("id") == pid:
        deactivate()
    (PROVIDERS / f"{pid}.dat").unlink(missing_ok=True)


def key_for(pid):
    """The API key, for the helper command only."""
    p = get_provider(pid)
    if not p or not p.get("apiKey"):
        raise SwitchError("That provider has no saved key.")
    return p["apiKey"]


# --------------------------------------------------------------------------- settings.json
def _read_settings():
    """(parsed settings, raw text). A missing or empty file is {}. Anything unreadable stops us before we write."""
    text = core._read_text(SETTINGS)
    if text is None or not text.strip():
        return {}, text
    for attempt in range(3):   # Claude Code may be mid-write
        try:
            obj = json.loads(text)
            break
        except ValueError:
            time.sleep(0.2)
            text = core._read_text(SETTINGS) or ""
    else:
        raise SwitchError("Claude Code's settings.json is not valid JSON, so nothing was changed. Fix or delete it first.")
    if not isinstance(obj, dict):
        raise SwitchError("Claude Code's settings.json has an unexpected format. Nothing was changed.")
    if "env" in obj and not isinstance(obj["env"], dict):
        raise SwitchError('The "env" entry in settings.json is not an object. Nothing was changed.')
    return obj, text


def _backup_settings(text):
    if text is None:
        return
    SETTINGS_BACKUPS.mkdir(parents=True, exist_ok=True)
    core._write_bytes(SETTINGS_BACKUPS / f"{datetime.now():%Y%m%d-%H%M%S-%f}.dat", core._encode({"text": text}))
    for old in sorted(SETTINGS_BACKUPS.glob("*.dat"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


def _write_settings(obj, old_text):
    """Write settings.json atomically, read it back, and restore the previous text if it does not check out."""
    core.write_json(SETTINGS, obj, old_text)
    try:
        back, _ = _read_settings()
        if back != obj:
            raise SwitchError("settings.json did not contain what was written.")
    except SwitchError:
        if old_text is None:
            SETTINGS.unlink(missing_ok=True)
        else:
            core._write_bytes(SETTINGS, old_text.encode("utf-8"))
        raise SwitchError("Claude Code's settings could not be updated safely. The previous settings were restored.")


def _read_route():
    try:
        data = json.loads(ROUTE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def helper_command(pid):
    """The command Claude Code runs to get the key. python.exe (not pythonw) because it must print to stdout."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        exe = exe.parent / "python.exe"
    return f'"{exe}" -I "{core.APP_DIR / "claude_accounts.py"}" key {pid}'


def current_route(obj=None):
    """What Claude Code will use right now, judged from settings.json alone: account, provider, or someone else's URL."""
    if obj is None:
        obj, _ = _read_settings()
    env = obj.get("env") if isinstance(obj.get("env"), dict) else {}
    helper = obj.get("apiKeyHelper") if isinstance(obj.get("apiKeyHelper"), str) else ""
    base = env.get("ANTHROPIC_BASE_URL")
    m = HELPER_RE.search(helper.strip())
    if m:
        p = get_provider(m.group(1))
        return {"kind": "provider", "id": m.group(1), "name": p["name"] if p else None, "baseUrl": base,
                "missing": p is None}
    if base:
        return {"kind": "custom", "baseUrl": base}
    return {"kind": "account"}


def activate(pid):
    """Make Claude Code use this provider. Returns {"name", "settleSeconds"}; the next message picks it up."""
    p = get_provider(pid)
    if not p:
        raise SwitchError("That provider is no longer saved.")
    if not p.get("apiKey"):
        raise SwitchError(f'"{p["name"]}" has no API key saved.')
    with core._dir_lock(ROUTE_LOCK, 30.0):
        obj, text = _read_settings()
        env = obj.get("env") or {}
        for name in FOREIGN_CREDENTIALS:
            if env.get(name):
                raise SwitchError(f"settings.json already sets {name}; it would override this provider's key. "
                                  f"Remove that line first. Nothing was changed.")
        route = _read_route()
        if route.get("provider") and current_route(obj).get("kind") == "provider":
            saved = route["saved"]            # already ours: keep the original values, not our own
        else:
            saved = {"envExisted": "env" in obj, "env": {k: env.get(k) for k in MANAGED_ENV},
                     "apiKeyHelper": obj.get("apiKeyHelper")}
        written = {"ANTHROPIC_BASE_URL": p["baseUrl"], **{MODEL_ENV[t]: p["models"][t] for t in MODEL_ENV}}
        helper = helper_command(pid)

        new = copy.deepcopy(obj)
        new.setdefault("env", {}).update(written)
        new["apiKeyHelper"] = helper
        _backup_settings(text)
        _write_settings(new, text)   # base URL and key helper land in the same atomic replace
        core.write_json(ROUTE_FILE, {"provider": pid, "at": datetime.now().isoformat(), "saved": saved,
                                     "written": {"env": written, "apiKeyHelper": helper}})
    return {"name": p["name"], "settleSeconds": SETTLE_SECONDS}


def repair_chats():
    """Make old chats that a provider touched usable on a Claude account again. Never raises."""
    try:
        import claude_accounts_transcripts as transcripts
        return transcripts.repair_all()
    except Exception as exc:   # a repair problem must never block a switch
        return {"files": 0, "blocks": 0, "errors": [str(exc)]}


def deactivate():
    """Go back to the Claude account route. Returns the name of the provider that was in use, or None.
    Old chats are repaired too, so the ones that ran on the provider keep working on the account."""
    left = _deactivate_settings()
    repair_chats()
    return left


def _deactivate_settings():
    with core._dir_lock(ROUTE_LOCK, 30.0):
        obj, text = _read_settings()
        route = current_route(obj)
        if route["kind"] != "provider":
            ROUTE_FILE.unlink(missing_ok=True)
            return None
        info = _read_route()
        saved = info.get("saved") or {"envExisted": True, "env": {}, "apiKeyHelper": None}
        written = info.get("written") or {"env": {}, "apiKeyHelper": None}
        new = copy.deepcopy(obj)
        env = new.get("env") if isinstance(new.get("env"), dict) else {}
        for key in MANAGED_ENV:
            # put back what was there before; leave a value alone if somebody changed it since we wrote it
            if key in written["env"] and env.get(key) != written["env"][key]:
                continue
            if saved["env"].get(key) is None:
                env.pop(key, None)
            else:
                env[key] = saved["env"][key]
        if saved.get("apiKeyHelper") is None:
            new.pop("apiKeyHelper", None)
        else:
            new["apiKeyHelper"] = saved["apiKeyHelper"]
        if env:
            new["env"] = env
        elif saved.get("envExisted"):
            new["env"] = {}
        else:
            new.pop("env", None)
        _backup_settings(text)
        _write_settings(new, text)
        ROUTE_FILE.unlink(missing_ok=True)
        return route.get("name") or "provider"


def use_account(pid):
    """Switch Claude Code to a saved account, leaving any provider. Result as core.switch_to plus leftProvider."""
    res = core.switch_to(pid)
    left = deactivate()
    res["leftProvider"] = left
    res["changed"] = bool(res["changed"] or left)
    return res


# --------------------------------------------------------------------------- testing a provider
def _http(url, key, body, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": key, "Authorization": "Bearer " + key, "anthropic-version": "2023-06-01",
        "content-type": "application/json", "user-agent": TEST_UA})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except Exception as exc:   # offline, DNS, timeout, TLS
        return 0, f"{type(exc).__name__}: {exc}"[:160], time.time() - started
    try:
        return status, json.loads(raw), time.time() - started
    except ValueError:
        return status, raw[:160].decode("utf-8", "replace"), time.time() - started


def _short(body):
    if isinstance(body, dict):
        err = body.get("error")
        text = err.get("message") if isinstance(err, dict) else (err or json.dumps(body))
    else:
        text = body
    return re.sub(r"\s+", " ", str(text))[:140]


_WEATHER = [{"name": "get_weather", "description": "Get the weather for a city",
             "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
_ASK = {"role": "user", "content": "What's the weather in Paris? Use the tool."}


def check_model(base_url, key, model, deep=True):
    """One model, the way Claude Code uses it. deep=True also continues after a tool call, which is exactly where a
    gateway that mishandles the model's thinking blocks fails (that is how the Muse Spark bug showed up)."""
    url = base_url + "/v1/messages"
    if not deep:
        status, body, secs = _http(url, key, {"model": model, "max_tokens": 600,
                                              "messages": [{"role": "user", "content": "Reply with the word PONG."}]})
        ok = status == 200 and isinstance(body, dict) and bool(body.get("content"))
        return {"model": model, "ok": ok, "seconds": round(secs, 1), "detail": "answers" if ok else f"HTTP {status}: {_short(body)}"}
    status, body, s1 = _http(url, key, {"model": model, "max_tokens": 2000, "tools": _WEATHER, "messages": [_ASK]})
    if status != 200 or not isinstance(body, dict):
        return {"model": model, "ok": False, "seconds": round(s1, 1), "detail": f"HTTP {status}: {_short(body)}"}
    call = next((b for b in body.get("content", []) if b.get("type") == "tool_use"), None)
    if not call:
        return {"model": model, "ok": False, "seconds": round(s1, 1),
                "detail": "answered but did not use the tool (Claude Code needs tool use)"}
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": "18C and sunny"}]}
    status, body2, s2 = _http(url, key, {"model": model, "max_tokens": 2000, "tools": _WEATHER,
                                         "messages": [_ASK, {"role": "assistant", "content": body["content"]}, result]})
    total = round(s1 + s2, 1)
    if status != 200 or not isinstance(body2, dict):
        return {"model": model, "ok": False, "seconds": total, "detail": f"fails after a tool call: HTTP {status}: {_short(body2)}"}
    return {"model": model, "ok": True, "seconds": total, "detail": "tool use and follow-up step work"}


def test_provider(pid_or_provider, deep=True):
    """Check every distinct model of a provider. Never raises for a failing model; returns one row each."""
    p = pid_or_provider if isinstance(pid_or_provider, dict) else get_provider(pid_or_provider)
    if not p:
        raise SwitchError("That provider is no longer saved.")
    seen, rows = set(), []
    for tier in ("opus", "sonnet", "haiku", "subagent"):
        model = p["models"].get(tier)
        if model and model not in seen:
            seen.add(model)
            row = check_model(p["baseUrl"], p["apiKey"], model, deep)
            row["tiers"] = [t for t in ("opus", "sonnet", "haiku", "subagent") if p["models"].get(t) == model]
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- state and self-check
def full_state():
    """core.state() plus providers and the route. Each account also gets inUse (live login AND no provider active)."""
    st = core.state()
    route = current_route()
    st["route"] = route
    st["providers"] = [{**public(p), "active": route["kind"] == "provider" and route.get("id") == p["id"]}
                       for p in list_providers()]
    for item in st["profiles"]:
        item["inUse"] = bool(item["active"] and route["kind"] != "provider")
    return st


def doctor_checks():
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        obj, _ = _read_settings()
        add("Claude Code settings readable", True, str(SETTINGS))
    except SwitchError as exc:
        add("Claude Code settings readable", False, str(exc))
        return checks
    route = current_route(obj)
    env = obj.get("env") or {}
    add("No other credential in settings.json", not any(env.get(k) for k in FOREIGN_CREDENTIALS) or route["kind"] != "provider",
        "ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY would override a provider's key")
    leaked = [k for k in FOREIGN_CREDENTIALS + ("ANTHROPIC_BASE_URL",) if os.environ.get(k)]
    add("No provider settings in the Windows environment", not leaked, ", ".join(leaked) or "clean")
    if route["kind"] == "provider":
        add("Provider still saved", not route.get("missing"), route.get("name") or "the saved provider is gone; switch back")
        helper = obj.get("apiKeyHelper", "")
        m = re.search(r'"([^"]*claude_accounts\.py)"', helper)
        add("Key helper script exists", bool(m) and Path(m.group(1)).exists(), m.group(1) if m else helper)
    elif route["kind"] == "custom":
        add("Custom base URL is managed by this app", False, f'{route["baseUrl"]} was set outside this app')
    return checks
