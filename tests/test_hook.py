from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
HOOK_SCRIPT = SCRIPTS_DIR / "hook.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import context_core as core
import hook


def _write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(entry) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _planner_step(
    step_index: int,
    *,
    input_tokens: int,
    cache_read_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "gemini-4-hydrogen-high",
) -> dict[str, object]:
    return {
        "step_index": step_index,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": f"2026-11-25T10:00:{step_index:02d}Z",
        "model": model,
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
    }


def _env_for(tmp: str, cfg_path: Path | None = None, root: Path | None = None) -> dict[str, str]:
    return {
        "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path or (Path(tmp) / "config" / "context-guard.json")),
        "CONTEXT_GUARD_BRAIN_ROOTS": str(root or (Path(tmp) / "brain-root")),
    }


class TestHooks(unittest.TestCase):
    def test_session_start_registers_conversation_and_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-start" / ".system_generated" / "logs" / "transcript.jsonl"
            env = {**os.environ, **_env_for(tmp, cfg_path, root)}

            payload = {
                "conversationId": "conv-start",
                "transcriptPath": str(transcript),
                "modelName": "gemini-4-hydrogen-high",
                "hookEventName": "SessionStart",
            }
            proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT), "SessionStart"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {})

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                cfg = core.load_config()
            self.assertEqual(cfg["lastActiveConversationId"], "conv-start")
            self.assertEqual(
                cfg["conversations"]["conv-start"]["transcriptPath"],
                str(transcript),
            )

    def test_dedup_across_turns_and_reset_after_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-dedup" / ".system_generated" / "logs" / "transcript.jsonl"
            entries = [
                _planner_step(0, input_tokens=250_000),
            ]
            _write_jsonl(transcript, entries)

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                payload = {
                    "conversationId": "conv-dedup",
                    "transcriptPath": str(transcript),
                    "lastUserInput": "Check tests",
                }
                first = hook.handle_hook("PreInvocation", payload)
                self.assertIn("injectSteps", first)
                self.assertNotIn("terminationBehavior", first)
                sys_msg = first["injectSteps"][0]["systemMessage"]
                self.assertIsInstance(sys_msg["systemMessage"], str)
                self.assertNotIn("\n", sys_msg["systemMessage"])
                self.assertIn("user_markdown", sys_msg["metadata"])

                # Same epoch, higher tokens below limit -> deduplicated
                entries.append(_planner_step(1, input_tokens=260_000))
                _write_jsonl(transcript, entries)
                second = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(second, {})

                # Compaction occurs (drop 260k -> 40k): compactionCount == 1
                entries.append(_planner_step(2, input_tokens=40_000))
                _write_jsonl(transcript, entries)
                after_compact = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(after_compact, {})

                # Epoch 1 reaches warn threshold (250k / 300k) -> warns once again
                entries.append(_planner_step(3, input_tokens=250_000))
                _write_jsonl(transcript, entries)
                rearmed = hook.handle_hook("PreInvocation", payload)
                self.assertIn("injectSteps", rearmed)
                self.assertIn("systemMessage", rearmed["injectSteps"][0])

                # Still epoch 1 -> deduplicated again
                entries.append(_planner_step(4, input_tokens=265_000))
                _write_jsonl(transcript, entries)
                dedup_epoch1 = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(dedup_epoch1, {})

    def test_post_invocation_stop_mode_terminates_once_per_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-stop" / ".system_generated" / "logs" / "transcript.jsonl"
            entries = [_planner_step(0, input_tokens=310_000)]
            _write_jsonl(transcript, entries)

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                payload = {
                    "conversationId": "conv-stop",
                    "transcriptPath": str(transcript),
                    "lastUserInput": "Continue refactoring",
                }
                post_res = hook.handle_hook("PostInvocation", payload)
                self.assertEqual(post_res.get("terminationBehavior"), "terminate")
                self.assertIn("systemMessage", post_res["injectSteps"][0])

                # Next PreInvocation in stop mode must NOT trap the user
                pre_res = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(pre_res, {})

                # Subsequent PostInvocation in same epoch also does not repeat termination
                entries.append(_planner_step(1, input_tokens=315_000))
                _write_jsonl(transcript, entries)
                post_again = hook.handle_hook("PostInvocation", payload)
                self.assertEqual(post_again, {})

    def test_pre_invocation_gate_mode_blocks_until_snooze_or_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-gate" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(transcript, [_planner_step(0, input_tokens=310_000)])

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                cfg = core.load_config()
                core.update_settings(cfg, mode="gate")

                payload = {
                    "conversationId": "conv-gate",
                    "transcriptPath": str(transcript),
                    "lastUserInput": "Run deployment script",
                }
                blocked = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(blocked.get("terminationBehavior"), "terminate")
                err = blocked["injectSteps"][0]["errorMessage"]
                self.assertIn("/compact", err["userMessage"])
                self.assertIn("side pane", err["userMessage"].lower())

                # Remains blocked on subsequent turns while above threshold
                blocked_again = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(blocked_again.get("terminationBehavior"), "terminate")

                # Snoozing above current tokens unblocks cleanly without immediate re-warn
                core.update_settings(core.load_config(), conv_id="conv-gate", snooze_until_tokens=360_000)
                unblocked = hook.handle_hook("PreInvocation", payload)
                self.assertEqual(unblocked, {})

    def test_safe_bypass_commands_in_gate_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-bypass" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(transcript, [_planner_step(0, input_tokens=350_000)])

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                core.update_settings(core.load_config(), mode="gate")

                bypass_inputs = [
                    "/compact",
                    "/compact focus on auth",
                    " /BTW ",
                    "/rewind",
                    "/new",
                    "/clear",
                    "\t/Clear everything\n",
                ]
                for user_input in bypass_inputs:
                    for event in ("PreInvocation", "PostInvocation"):
                        res = hook.handle_hook(
                            event,
                            {
                                "conversationId": "conv-bypass",
                                "transcriptPath": str(transcript),
                                "lastUserInput": user_input,
                            },
                        )
                        self.assertEqual(res, {}, msg=f"Failed bypass for {user_input!r} on {event}")

                # Non-exact slash prefix like /compaction is NOT a bypass
                non_bypass = hook.handle_hook(
                    "PreInvocation",
                    {
                        "conversationId": "conv-bypass",
                        "transcriptPath": str(transcript),
                        "lastUserInput": "/compaction explain",
                    },
                )
                self.assertEqual(non_bypass.get("terminationBehavior"), "terminate")

    def test_stop_records_turn_stats_and_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-end" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                transcript,
                [
                    _planner_step(
                        0,
                        input_tokens=200_000,
                        cache_read_tokens=45_000,
                        output_tokens=5_000,
                        model="claude-3-7-sonnet",
                    )
                ],
            )

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                res = hook.handle_hook(
                    "Stop",
                    {
                        "conversationId": "conv-end",
                        "transcriptPath": str(transcript),
                        "modelName": "claude-3-7-sonnet",
                    },
                )
                self.assertEqual(res, {})

                cfg = core.load_config()
                self.assertEqual(cfg["lastActiveConversationId"], "conv-end")
                state = cfg["conversations"]["conv-end"]
                self.assertEqual(state["transcriptPath"], str(transcript))
                self.assertEqual(state["lastTokens"], 250_000)
                self.assertEqual(state["lastModel"], "claude-3-7-sonnet")
                self.assertTrue(state["updatedAt"])

    def test_fail_open_on_bad_stdin_and_corrupt_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "context-guard.json"
            cfg_path.write_text("{not-valid-json", encoding="utf-8")
            empty_root = Path(tmp) / "empty-root"
            env = {**os.environ, **_env_for(tmp, cfg_path, empty_root)}

            cases = ["{not-json", "", "[1, 2, 3]"]
            for bad_stdin in cases:
                proc = subprocess.run(
                    [sys.executable, str(HOOK_SCRIPT), "PreInvocation"],
                    input=bad_stdin,
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                self.assertEqual(json.loads(proc.stdout), {})

            # Even when config path parent is unwritable (points under a file), hook must fail open
            blocker_file = Path(tmp) / "not-a-dir"
            blocker_file.write_text("x", encoding="utf-8")
            unwritable_env = {
                **os.environ,
                "CONTEXT_GUARD_CONFIG_PATH": str(blocker_file / "context-guard.json"),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(empty_root),
            }
            proc2 = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT), "PreInvocation"],
                input=json.dumps({"conversationId": "conv-failopen"}),
                text=True,
                capture_output=True,
                env=unwritable_env,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, msg=proc2.stderr)
            self.assertEqual(json.loads(proc2.stdout), {})

    def test_warn_mode_and_disabled_never_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = root / "brain" / "conv-warn" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(transcript, [_planner_step(0, input_tokens=310_000)])

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                core.update_settings(core.load_config(), mode="warn")
                payload = {
                    "conversationId": "conv-warn",
                    "transcriptPath": str(transcript),
                    "lastUserInput": "Summarize module",
                }
                post_res = hook.handle_hook("PostInvocation", payload)
                self.assertIn("injectSteps", post_res)
                self.assertNotIn("terminationBehavior", post_res)

                # Subsequent call in same epoch is deduped
                self.assertEqual(hook.handle_hook("PreInvocation", payload), {})

                # Disabled mode emits nothing even in gate mode on fresh conversation
                core.update_settings(core.load_config(), enabled=False, mode="gate")
                _write_jsonl(
                    root / "brain" / "conv-disabled" / ".system_generated" / "logs" / "transcript.jsonl",
                    [_planner_step(0, input_tokens=400_000)],
                )
                disabled_res = hook.handle_hook(
                    "PreInvocation",
                    {
                        "conversationId": "conv-disabled",
                        "lastUserInput": "Do work",
                    },
                )
                self.assertEqual(disabled_res, {})


if __name__ == "__main__":
    unittest.main()
