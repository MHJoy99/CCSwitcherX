"""Repair old Claude Code chats so they keep working on a real Claude account after a provider was used.

A gateway that serves another model (Gemini, Muse...) through Claude Code writes blocks into the chat history that
Anthropic's API refuses when the same chat is continued on a Claude account:

  - tool_use blocks with an extra "signature" field   -> 400 "tool_use.signature: Extra inputs are not permitted"
  - thinking blocks whose signature is empty or was not made by Anthropic -> 400 "Invalid signature in thinking block"

Claude Code resends the whole history with every message, so one such block breaks the chat for good. This module
rewrites the chat files (<claude dir>/projects/**/*.jsonl):

  - any key Anthropic does not know (the "signature" above, or anything else a gateway adds) is removed from the
    tool_use and text blocks written by a non-Claude model;
  - thinking / redacted_thinking blocks written by a non-Claude model are removed (Claude Code stores one block per
    line, so a line left empty is dropped and its children are re-linked to its parent).

Nothing written by a Claude model is changed. Every file is backed up before its first rewrite, and a file that grows
while we work (Claude Code appending) is re-read instead of overwritten.
"""
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import claude_accounts_core as core

PROJECTS = core.CLAUDE_DIR / "projects"
BACKUP_DIR = core.STORE / "transcript-backups"
# a model name that is not Claude's own: quick byte test so clean files are never parsed
FOREIGN_MODEL_RE = re.compile(rb'"model"\s*:\s*"(?!claude-|<synthetic>)[^"]+"')
PARENT_KEYS = ("parentUuid", "logicalParentUuid")
# the only keys Anthropic's Messages API accepts on these assistant blocks when they are sent back
ALLOWED_KEYS = {
    "tool_use": {"type", "id", "name", "input", "cache_control", "caller"},
    "text": {"type", "text", "cache_control", "citations"},
}


def _is_foreign(model):
    model = str(model or "")
    return bool(model) and not (model.startswith("claude-") or model == "<synthetic>")


def _clean_entry(obj):
    """Returns (entry or None to drop, changed). Only assistant messages from a non-Claude model are touched."""
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant" or not _is_foreign(msg.get("model")):
        return obj, False
    content = msg.get("content")
    if not isinstance(content, list):
        return obj, False
    out, changed = [], False
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        kind = block.get("type")
        if kind in ("thinking", "redacted_thinking"):
            changed = True
            continue
        allowed = ALLOWED_KEYS.get(kind)
        if allowed and any(k not in allowed for k in block):
            # a gateway may add fields of its own ("signature", ...); Anthropic rejects any key it does not know
            block = {k: v for k, v in block.items() if k in allowed}
            changed = True
        out.append(block)
    if not changed:
        return obj, False
    if not out:
        return None, True
    msg["content"] = out
    return obj, True


def clean_text(text):
    """Clean one .jsonl file's text. Returns (new text, blocks fixed). Unparseable lines are kept as they are."""
    lines = text.split("\n")
    parsed, dropped, fixed = [], {}, 0
    for line in lines:
        if not line.strip():
            parsed.append((line, None))
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            parsed.append((line, None))
            continue
        if not isinstance(obj, dict):
            parsed.append((line, None))
            continue
        new, changed = _clean_entry(obj)
        if not changed:
            parsed.append((line, None))
            continue
        fixed += 1
        if new is None:
            if obj.get("uuid"):
                dropped[obj["uuid"]] = obj.get("parentUuid")
            continue
        parsed.append((None, new))
    if not fixed:
        return text, 0

    def resolve(uuid):
        seen = set()
        while uuid in dropped and uuid not in seen:
            seen.add(uuid)
            uuid = dropped[uuid]
        return uuid

    out = []
    for line, obj in parsed:
        if obj is None and dropped and line.strip():
            try:
                cand = json.loads(line)
            except ValueError:
                cand = None
            if isinstance(cand, dict) and any(cand.get(k) in dropped for k in PARENT_KEYS):
                obj = cand
        if obj is not None:
            for key in PARENT_KEYS:
                if obj.get(key) in dropped:
                    obj[key] = resolve(obj[key])
            out.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        else:
            out.append(line)
    return "\n".join(out), fixed


def _backup(path):
    rel = path.relative_to(PROJECTS)
    dest = BACKUP_DIR / rel.parent / (rel.name + ".orig")
    if not dest.exists():   # keep the first, untouched original
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def repair_file(path):
    """Fix one chat file in place. Returns the number of blocks fixed (0 if it was already clean)."""
    for _ in range(5):
        before = path.stat()
        raw = path.read_bytes()
        if not FOREIGN_MODEL_RE.search(raw):
            return 0
        new, fixed = clean_text(raw.decode("utf-8", "replace"))
        if not fixed:
            return 0
        now = path.stat()
        if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            time.sleep(0.3)   # Claude Code is appending right now: read it again
            continue
        _backup(path)
        tmp = path.with_name(path.name + ".ca-tmp")
        tmp.write_bytes(new.encode("utf-8"))
        latest = path.stat()
        if (latest.st_size, latest.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            tmp.unlink(missing_ok=True)
            time.sleep(0.3)
            continue
        os.replace(tmp, path)
        return fixed
    return 0


def repair_all(root=None):
    """Fix every chat under the projects folder. Returns {"files": n, "blocks": n, "errors": [..]}."""
    root = Path(root) if root else PROJECTS
    report = {"files": 0, "blocks": 0, "errors": []}
    if not root.exists():
        return report
    for path in root.rglob("*.jsonl"):
        try:
            fixed = repair_file(path)
        except OSError as exc:
            report["errors"].append(f"{path.name}: {exc}")
            continue
        if fixed:
            report["files"] += 1
            report["blocks"] += fixed
    if report["files"]:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        with open(BACKUP_DIR / "repair.log", "a", encoding="utf-8") as log:
            log.write(f'{datetime.now().isoformat()} fixed {report["blocks"]} blocks in {report["files"]} chats\n')
    return report
