from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_core as core


def _write_jsonl(path: Path, entries: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        entry if isinstance(entry, str) else json.dumps(entry)
        for entry in entries
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestTranscriptAnalysis(unittest.TestCase):
    def test_token_math_pending_delta_and_positional_bloat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:00Z",
                    "content": "u" * 400,  # 100 est tokens
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:05Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 20_000,
                    "cache_read_tokens": 80_000,
                    "output_tokens": 1_000,
                    "content": "Checking files now.",
                    "tool_calls": [
                        {"name": "read_file", "args": {"Path": "/repo/big.py"}},
                        {"name": "run_command", "args": {"CommandLine": "pytest"}},
                    ],
                },
                {
                    "step_index": 2,
                    "source": "SYSTEM",
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:06Z",
                    "content": "x" * 8_000,  # 2000 est tokens -> read_file
                },
                {
                    "step_index": 3,
                    "source": "SYSTEM",
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:07Z",
                    "content": "y" * 4_000,  # 1000 est tokens -> run_command
                },
            ]
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)

            self.assertEqual(result["model"], "gemini-4-hydrogen-high")
            self.assertEqual(result["stepCount"], 4)
            self.assertEqual(result["turnCount"], 1)
            self.assertEqual(result["inputTokens"], 20_000)
            self.assertEqual(result["cacheReadTokens"], 80_000)
            self.assertEqual(result["outputTokens"], 1_000)
            self.assertEqual(result["promptTokens"], 100_000)
            self.assertEqual(result["totalTokens"], 101_000)
            self.assertEqual(result["pendingDeltaTokens"], 3_000)
            self.assertEqual(result["effectiveTokens"], 104_000)
            self.assertEqual(result["cacheHitPercent"], 80.0)
            self.assertEqual(result["compactionCount"], 0)
            self.assertEqual(result["activeEpochStep"], 0)
            self.assertEqual(result["bloatByCategory"]["user"], 100)
            self.assertEqual(result["bloatByCategory"]["model"], 1_000)
            self.assertEqual(result["bloatByCategory"]["toolOutputs"], 3_000)
            self.assertEqual(result["topBloatSteps"][0]["stepIndex"], 2)
            self.assertEqual(result["topBloatSteps"][0]["tools"], ["read_file"])
            self.assertEqual(result["topBloatSteps"][0]["estimatedTokens"], 2_000)
            step3 = next(s for s in result["topBloatSteps"] if s["stepIndex"] == 3)
            self.assertEqual(step3["tools"], ["run_command"])
            self.assertEqual(step3["estimatedTokens"], 1_000)

    def test_compaction_detection_by_drop_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:00Z",
                    "content": "a" * 4_000,  # 1000 tokens BEFORE compaction
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:05Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 99_000,
                    "cache_read_tokens": 0,
                    "output_tokens": 1_000,  # prev_total = 100_000
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:01:00Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 25_000,  # < 65% of 100_000 -> compaction #1
                    "cache_read_tokens": 0,
                    "output_tokens": 500,
                },
                {
                    "step_index": 3,
                    "source": "SYSTEM",
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:01:05Z",
                    "content": "b" * 2_000,  # 500 tokens between compactions
                },
                {
                    "step_index": 4,
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:02:00Z",
                    "content": "<CONTEXT_SUMMARY>Compacted history</CONTEXT_SUMMARY>",  # compaction #2 at step 4
                },
                {
                    "step_index": 5,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:02:10Z",
                    "content": "c" * 800,  # 200 user tokens in active epoch
                },
                {
                    "step_index": 6,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:02:15Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 20_000,
                    "cache_read_tokens": 10_000,
                    "output_tokens": 400,
                    "content": "Continuing",
                    "tool_calls": [{"name": "read_file", "args": {"Path": "/a.py"}}],
                },
                {
                    "step_index": 7,
                    "source": "SYSTEM",
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:02:16Z",
                    "content": "d" * 1_600,  # 400 tool tokens in active epoch
                },
            ]
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)

            self.assertEqual(result["compactionCount"], 2)
            self.assertEqual(result["compactions"][0]["reason"], "drop")
            self.assertEqual(result["compactions"][0]["prevTotalTokens"], 100_000)
            self.assertEqual(result["compactions"][1]["reason"], "marker")
            self.assertEqual(result["activeEpochStep"], 4)
            # Only steps >= 4 counted in bloatByCategory and topBloatSteps
            self.assertEqual(result["bloatByCategory"]["user"], 200)
            self.assertEqual(result["bloatByCategory"]["toolOutputs"], 400)
            self.assertTrue(all(s["stepIndex"] >= 4 for s in result["topBloatSteps"]))

    def test_compaction_marker_followed_by_drop_counts_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:00Z",
                    "content": "z" * 4_000,  # 1000 tokens pre-compaction
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:05Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 119_000,
                    "cache_read_tokens": 0,
                    "output_tokens": 1_000,  # prev_total = 120_000
                },
                {
                    "step_index": 2,
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:01:00Z",
                    "content": "<CONTEXT_SUMMARY>" + ("s" * 383),  # 400 chars = 100 system tokens
                },
                {
                    "step_index": 3,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:01:05Z",
                    "content": "u" * 800,  # 200 user tokens
                },
                {
                    "step_index": 4,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:01:10Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 25_000,
                    "cache_read_tokens": 5_000,  # prompt_tok = 30_000 < 120_000 * 0.65
                    "output_tokens": 500,
                },
            ]
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)

            self.assertEqual(result["compactionCount"], 1)
            self.assertEqual(len(result["compactions"]), 1)
            self.assertEqual(result["compactions"][0]["reason"], "marker")
            self.assertEqual(result["compactions"][0]["newPromptTokens"], 30_000)
            self.assertEqual(result["activeEpochStep"], 2)
            self.assertEqual(result["bloatByCategory"]["system"], 100)
            self.assertEqual(result["bloatByCategory"]["user"], 200)
            self.assertEqual(result["bloatByCategory"]["model"], 500)

    def test_malformed_lines_and_zero_token_error_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries: list[object] = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:00Z",
                    "content": "hello",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:05Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 40_000,
                    "cache_read_tokens": 10_000,
                    "output_tokens": 800,
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "ERROR",
                    "created_at": "2026-11-25T10:00:10Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 0,
                    "cache_read_tokens": 0,
                    "output_tokens": 0,
                    "content": "",
                },
                {
                    "step_index": 3,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "ERROR",
                    "created_at": "2026-11-25T10:00:12Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 5_000,
                    "cache_read_tokens": 0,
                    "output_tokens": 50,
                    "tool_calls": [{"name": "read_file", "args": {"Path": "/ignored/error.py"}}],
                },
                {
                    "step_index": 4,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-11-25T10:00:14Z",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 0,
                    "cache_read_tokens": 0,
                    "output_tokens": 50,
                },
                '{"step_index": 5, "source": "SYSTEM", "type": "GENERIC", "content": "partial...',
            ]
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)

            self.assertEqual(result["compactionCount"], 0)
            self.assertEqual(result["promptTokens"], 50_000)
            self.assertEqual(result["outputTokens"], 800)
            self.assertEqual(result["totalTokens"], 50_800)
            self.assertEqual(result["turnCount"], 1)
            self.assertEqual(result["recentFiles"], [])

    def test_history_cap_and_avg_delta_estimation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries: list[object] = []
            for turn in range(65):
                entries.append(
                    {
                        "step_index": turn,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": f"2026-11-25T10:00:{turn:02d}Z",
                        "model": "gemini-4-hydrogen-high",
                        "input_tokens": 10_000 + turn * 2_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 0,
                    }
                )
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)

            self.assertEqual(len(result["history"]), 60)
            self.assertEqual(result["history"][0]["stepIndex"], 5)
            self.assertEqual(result["history"][-1]["stepIndex"], 64)
            self.assertEqual(result["avgDeltaPerTurn"], 2_000)


class TestConfigAndState(unittest.TestCase):
    def test_epoch_dedup_sentinel_and_snooze_rearm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            empty_root = Path(tmp) / "empty-root"
            env = {
                "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(empty_root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = core.load_config()
                self.assertEqual(cfg["thresholdTokens"], core.DEFAULT_THRESHOLD_TOKENS)
                self.assertEqual(cfg["warnPercent"], core.DEFAULT_WARN_PERCENT)
                self.assertEqual(cfg["mode"], core.DEFAULT_MODE)

                conv = core.get_conversation_state(cfg, "c1")
                self.assertEqual(conv["warnedAtEpoch"], -1)
                self.assertEqual(conv["alertedAtEpoch"], -1)
                self.assertEqual(conv["snoozedUntilTokens"], 0)
                self.assertEqual(conv["lastCompactionIndex"], 0)
                self.assertIsNone(conv["customThresholdTokens"])

                # Simulate prior alert in epoch 0 before snoozing
                conv["alertedAtEpoch"] = 0
                conv["warnedAtEpoch"] = 0
                conv["lastTokens"] = 290_000
                updated = core.update_settings(cfg, snooze_delta=50_000, conv_id="c1")
                self.assertGreaterEqual(updated["conversations"]["c1"]["snoozedUntilTokens"], 350_000)
                self.assertEqual(updated["conversations"]["c1"]["alertedAtEpoch"], -1)
                # new warn = 280k <= lastTokens (290k), so warnedAtEpoch stays 0
                self.assertEqual(updated["conversations"]["c1"]["warnedAtEpoch"], 0)
                self.assertEqual(core.resolve_effective_threshold(updated, "c1"), 350_000)

                # Raising customThresholdTokens above current warn resets both alertedAtEpoch & warnedAtEpoch
                conv["alertedAtEpoch"] = 0
                conv["warnedAtEpoch"] = 0
                core.update_settings(updated, custom_threshold_tokens=500_000, conv_id="c1")
                self.assertEqual(updated["conversations"]["c1"]["alertedAtEpoch"], -1)
                self.assertEqual(updated["conversations"]["c1"]["warnedAtEpoch"], -1)

                # Raising global threshold_tokens above current effective threshold also re-arms
                conv["alertedAtEpoch"] = 0
                conv["warnedAtEpoch"] = 0
                conv["customThresholdTokens"] = None
                conv["snoozedUntilTokens"] = 0
                core.update_settings(updated, threshold_tokens=600_000, conv_id="c1")
                self.assertEqual(updated["conversations"]["c1"]["alertedAtEpoch"], -1)
                self.assertEqual(updated["conversations"]["c1"]["warnedAtEpoch"], -1)

                # Persisted cleanly on disk
                reloaded = core.load_config()
                self.assertEqual(reloaded["thresholdTokens"], 600_000)

                # Invalid mode raises ValueError without corrupting config
                with self.assertRaises(ValueError):
                    core.update_settings(updated, mode="invalid")

                # Clamping check
                clamped = core.update_settings(updated, threshold_tokens=10, warn_percent=120)
                self.assertEqual(clamped["thresholdTokens"], 1_000)
                self.assertEqual(clamped["warnPercent"], 99)

                # Invalid paths in find_transcript_path fail closed (return None) without raising
                conv["transcriptPath"] = "/tmp/\x00invalid"
                self.assertIsNone(core.find_transcript_path("c1", explicit_path="/tmp/\x00bad", config=clamped))

    def test_discovery_status_and_overview_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "antigravity"
            cfg_path = Path(tmp) / "context-guard.json"
            transcript = root / "brain" / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:00Z",
                        "content": "Analyze code",
                    },
                    {
                        "step_index": 1,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:05Z",
                        "model": "gemini-4-hydrogen-high",
                        "input_tokens": 50_000,
                        "cache_read_tokens": 199_000,
                        "output_tokens": 1_000,
                    },
                ],
            )

            env = {
                "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = core.load_config()
                status = core.build_status_payload("conv-1", config=cfg)
                self.assertEqual(status["view"], "status")
                self.assertEqual(status["conversationId"], "conv-1")
                self.assertEqual(status["effectiveTokens"], 250_000)
                self.assertEqual(status["status"], "warn")
                self.assertEqual(status["modelWindowTokens"], 1_048_576)
                self.assertEqual(status["remainingToThreshold"], 50_000)

                overview = core.build_overview_payload(config=cfg)
                self.assertEqual(overview["view"], "overview")
                self.assertEqual(len(overview["conversations"]), 1)
                self.assertEqual(overview["conversations"][0]["conversationId"], "conv-1")
                self.assertEqual(overview["conversations"][0]["status"], "warn")

    def test_config_pruning_and_corrupt_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "context-guard.json"
            cfg_path.write_text("{corrupt json", encoding="utf-8")

            with mock.patch.dict(os.environ, {"CONTEXT_GUARD_CONFIG_PATH": str(cfg_path)}, clear=False):
                cfg = core.load_config()
                self.assertTrue(cfg["enabled"])
                self.assertEqual(cfg["thresholdTokens"], 300_000)

                cfg["lastActiveConversationId"] = "keep-active"
                for i in range(230):
                    cid = f"conv-{i:03d}"
                    st = core.get_conversation_state(cfg, cid)
                    st["updatedAt"] = f"2026-11-25T10:{i // 60:02d}:{i % 60:02d}Z"

                active = core.get_conversation_state(cfg, "keep-active")
                active["updatedAt"] = "2026-01-01T00:00:00Z"

                snoozed = core.get_conversation_state(cfg, "keep-snoozed")
                snoozed["snoozedUntilTokens"] = 400_000
                snoozed["updatedAt"] = "2026-01-01T00:00:00Z"

                custom = core.get_conversation_state(cfg, "keep-custom")
                custom["customThresholdTokens"] = 250_000
                custom["updatedAt"] = "2026-01-01T00:00:00Z"

                core.save_config(cfg)
                reloaded = core.load_config()

                self.assertLessEqual(len(reloaded["conversations"]), 200)
                self.assertIn("keep-active", reloaded["conversations"])
                self.assertIn("keep-snoozed", reloaded["conversations"])
                self.assertIn("keep-custom", reloaded["conversations"])

    def test_model_window_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "context-guard.json"
            with mock.patch.dict(os.environ, {"CONTEXT_GUARD_CONFIG_PATH": str(cfg_path)}, clear=False):
                cfg = core.load_config()
                self.assertEqual(core.resolve_model_window("gemini-4-hydrogen-high", cfg), 1_048_576)
                self.assertEqual(core.resolve_model_window("claude-sonnet-4", cfg), 200_000)
                self.assertEqual(core.resolve_model_window("gpt-4o-mini", cfg), 128_000)
                self.assertEqual(core.resolve_model_window("some-future-model", cfg), 1_048_576)

    def test_snooze_over_limit_reset_and_status_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "custom-app"
            cfg_path = Path(tmp) / "context-guard.json"
            transcript = root / "brain" / "conv-limit" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "step_index": 0,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:00Z",
                        "model": "claude-3-7-sonnet",
                        "input_tokens": 320_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 0,
                    }
                ],
            )
            env = {
                "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": "",
                "ANTIGRAVITY_APP_DATA_DIR": str(root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                roots = core.get_brain_roots()
                self.assertEqual(roots[0], root)

                cfg = core.load_config()
                no_data = core.build_status_payload("missing-conv", config=cfg)
                self.assertEqual(no_data["status"], "no_data")

                limit_status = core.build_status_payload("conv-limit", config=cfg)
                self.assertEqual(limit_status["status"], "limit")
                self.assertEqual(limit_status["modelWindowTokens"], 200_000)
                self.assertEqual(limit_status["remainingToThreshold"], 0)
                self.assertEqual(limit_status["estimatedTurnsRemaining"], 0)

                # Snooze +50k when effectiveTokens (320k) > threshold (300k) -> 370k
                core.update_settings(cfg, conv_id="conv-limit", snooze_delta=50_000)
                self.assertEqual(core.resolve_effective_threshold(cfg, "conv-limit"), 370_000)
                after_snooze = core.build_status_payload("conv-limit", config=cfg)
                self.assertEqual(after_snooze["status"], "warn")
                self.assertEqual(after_snooze["remainingToThreshold"], 50_000)

                # Reset snooze back to 0 -> threshold returns to 300k
                core.update_settings(cfg, conv_id="conv-limit", reset_snooze=True)
                self.assertEqual(core.resolve_effective_threshold(cfg, "conv-limit"), 300_000)

                # Disabled state
                core.update_settings(cfg, enabled=False)
                disabled = core.build_status_payload("conv-limit", config=cfg)
                self.assertEqual(disabled["status"], "disabled")


class TestHandoff(unittest.TestCase):
    def test_handoff_unwraps_quoted_tool_args_and_user_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "antigravity"
            cfg_path = Path(tmp) / "context-guard.json"
            transcript = root / "brain" / "conv-handoff" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:00Z",
                        "content": (
                            "<USER_REQUEST>\nFix bug in parser\n</USER_REQUEST>\n"
                            "<ADDITIONAL_METADATA>2026-11-25T10:00:00Z</ADDITIONAL_METADATA>"
                        ),
                    },
                    {
                        "step_index": 1,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:05Z",
                        "model": "gemini-4-hydrogen-high",
                        "input_tokens": 120_000,
                        "cache_read_tokens": 180_000,
                        "output_tokens": 2_000,
                        "tool_calls": [
                            {"name": "read_file", "args": {"Path": '"/repo/app.py"'}},
                            {"name": "run_command", "args": {"CommandLine": '"pytest -q"'}},
                        ],
                    },
                ],
            )

            env = {
                "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                handoff = core.build_handoff_brief(
                    "conv-handoff",
                    note="Focus on edge cases next.",
                )

            self.assertEqual(handoff["conversationId"], "conv-handoff")
            self.assertEqual(handoff["effectiveTokens"], 302_000)
            self.assertEqual(handoff["thresholdTokens"], 300_000)

            brief = handoff["brief"]
            self.assertIn("Fix bug in parser", brief)
            self.assertIn("/repo/app.py", brief)
            self.assertNotIn('"/repo/app.py"', brief)
            self.assertIn("pytest -q", brief)
            self.assertNotIn('"pytest -q"', brief)
            self.assertIn("Focus on edge cases next.", brief)
            self.assertNotIn("<ADDITIONAL_METADATA>", brief)
            self.assertNotIn("<USER_REQUEST>", brief)


if __name__ == "__main__":
    unittest.main()
