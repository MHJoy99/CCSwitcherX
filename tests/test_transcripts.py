"""Tests for claude_accounts_transcripts: chats that a gateway model wrote must be usable on a Claude account again.
Everything runs in a throw-away folder; no real chat or login is touched."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="ca-trans-test-"))
os.environ["CA_CLAUDE_DIR"] = str(SANDBOX / "claude")
os.environ["CA_STORE"] = str(SANDBOX / "store")
os.environ["CA_NO_NETWORK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import claude_accounts_core as core  # noqa: E402
import claude_accounts_providers as prov  # noqa: E402
import claude_accounts_transcripts as tr  # noqa: E402


def line(uuid, parent, model, content, role="assistant"):
    msg = {"role": role, "content": content}
    if role == "assistant":
        msg["model"] = model
    return {"uuid": uuid, "parentUuid": parent, "type": role, "message": msg}


def write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def read(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.root = tr.PROJECTS
        self.chat = self.root / "proj" / "chat.jsonl"
        for base in (self.root, tr.BACKUP_DIR):
            if base.exists():
                for p in base.rglob("*"):
                    if p.is_file():
                        p.unlink()

    def test_tool_use_signature_removed_for_gateway_model_only(self):
        write(self.chat, [
            line("a", None, "gemini-3.8-flash-high", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}, "signature": "xyz"}]),
            line("b", "a", "claude-sonnet-5-5", [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {}, "signature": "keep"}]),
        ])
        self.assertEqual(tr.repair_file(self.chat), 1)
        out = read(self.chat)
        self.assertNotIn("signature", out[0]["message"]["content"][0])
        self.assertEqual(out[1]["message"]["content"][0]["signature"], "keep")   # a Claude model's block is never touched

    def test_foreign_thinking_blocks_removed_and_empty_line_relinked(self):
        write(self.chat, [
            line("a", None, "go-muse-spark-1.3-contributor", [{"type": "thinking", "thinking": "", "signature": ""}]),
            line("b", "a", "go-muse-spark-1.3-contributor", [{"type": "text", "text": "hi"}]),
            {"uuid": "c", "parentUuid": "b", "type": "user", "message": {"role": "user", "content": "next"}},
        ])
        self.assertEqual(tr.repair_file(self.chat), 1)
        out = read(self.chat)
        self.assertEqual([e["uuid"] for e in out], ["b", "c"])           # the thinking-only line is gone
        self.assertIsNone(out[0]["parentUuid"])                           # and its child now hangs off its parent

    def test_claude_chat_is_not_rewritten(self):
        write(self.chat, [line("a", None, "claude-opus-5-5", [{"type": "thinking", "thinking": "x", "signature": "real"}])])
        before = self.chat.read_bytes()
        self.assertEqual(tr.repair_file(self.chat), 0)
        self.assertEqual(self.chat.read_bytes(), before)

    def test_backup_keeps_the_first_original(self):
        write(self.chat, [line("a", None, "gemini-x", [{"type": "tool_use", "id": "t", "name": "B", "input": {}, "signature": "s"}])])
        original = self.chat.read_bytes()
        tr.repair_file(self.chat)
        backup = tr.BACKUP_DIR / "proj" / "chat.jsonl.orig"
        self.assertEqual(backup.read_bytes(), original)

    def test_second_run_changes_nothing(self):
        write(self.chat, [line("a", None, "gemini-x", [{"type": "tool_use", "id": "t", "name": "B", "input": {}, "signature": "s"}])])
        tr.repair_file(self.chat)
        done = self.chat.read_bytes()
        self.assertEqual(tr.repair_file(self.chat), 0)
        self.assertEqual(self.chat.read_bytes(), done)

    def test_any_unknown_key_on_gateway_blocks_is_removed(self):
        write(self.chat, [line("a", None, "gemini-x", [
            {"type": "text", "text": "hi", "thought_signature": "q"},
            {"type": "tool_use", "id": "t", "name": "B", "input": {}, "thoughtSignature": "q", "cache_control": {"type": "ephemeral"}}])])
        self.assertEqual(tr.repair_file(self.chat), 1)
        blocks = read(self.chat)[0]["message"]["content"]
        self.assertEqual(set(blocks[0]), {"type", "text"})
        self.assertEqual(set(blocks[1]), {"type", "id", "name", "input", "cache_control"})   # known keys stay

    def test_garbage_lines_survive(self):
        self.chat.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps(line("a", None, "gemini-x", [{"type": "tool_use", "id": "t", "name": "B", "input": {}, "signature": "s"}]))
        self.chat.write_text("not json\n" + good + "\n", encoding="utf-8")
        tr.repair_file(self.chat)
        self.assertTrue(self.chat.read_text(encoding="utf-8").startswith("not json\n"))

    def test_repair_all_reports_counts(self):
        write(self.chat, [line("a", None, "gemini-x", [{"type": "tool_use", "id": "t", "name": "B", "input": {}, "signature": "s"}])])
        write(self.root / "other" / "c2.jsonl", [line("a", None, "claude-opus-5-5", [{"type": "text", "text": "ok"}])])
        rep = tr.repair_all()
        self.assertEqual((rep["files"], rep["blocks"], rep["errors"]), (1, 1, []))


class LeavingAProviderRepairsChats(unittest.TestCase):
    def test_deactivate_repairs_even_when_no_provider_was_active(self):
        chat = tr.PROJECTS / "p" / "c.jsonl"
        write(chat, [line("a", None, "gemini-x", [{"type": "tool_use", "id": "t", "name": "B", "input": {}, "signature": "s"}])])
        prov.deactivate()
        self.assertNotIn("signature", read(chat)[0]["message"]["content"][0])

    def test_repair_problem_never_blocks_a_switch(self):
        original = tr.repair_all
        tr.repair_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            rep = prov.repair_chats()
        finally:
            tr.repair_all = original
        self.assertEqual(rep["files"], 0)
        self.assertTrue(rep["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
