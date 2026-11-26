from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
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

    def test_checkpoint_resets_stale_tokens_before_next_planner_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "content": "Initial task",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 2_900,
                    "cache_read_tokens": 363_500,
                    "output_tokens": 1_089,
                },
                {
                    "step_index": 2,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "content": "/compact",
                },
                {
                    "step_index": 3,
                    "source": "SYSTEM",
                    "type": "CHECKPOINT",
                    "status": "DONE",
                    "input_tokens": 2_944,
                    "cache_read_tokens": 366_213,
                    "output_tokens": 3_367,
                    "content": "# Resuming from a compaction\n\n" + ("x" * 3_968),  # ~1000 est tokens
                },
            ]
            _write_jsonl(transcript, entries)

            immediately_after = core.analyze_transcript(transcript)
            self.assertEqual(immediately_after["compactionCount"], 1)
            self.assertEqual(immediately_after["compactions"][0]["reason"], "checkpoint")
            self.assertEqual(immediately_after["compactions"][0]["prevTotalTokens"], 367_489)
            self.assertEqual(immediately_after["compactions"][0]["newPromptTokens"], 0)
            self.assertEqual(immediately_after["totalTokens"], 0)
            self.assertEqual(immediately_after["activeEpochStep"], 3)
            self.assertEqual(immediately_after["firstTurnTokens"], 367_489)
            self.assertEqual(immediately_after["history"], [])
            self.assertLess(immediately_after["effectiveTokens"], 2_000)
            self.assertEqual(immediately_after["effectiveTokens"], immediately_after["pendingDeltaTokens"])

            # Subsequent post-compaction PLANNER_RESPONSE updates tokens without double-counting.
            entries.append(
                {
                    "step_index": 4,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 30_622,
                    "cache_read_tokens": 0,
                    "output_tokens": 812,
                }
            )
            _write_jsonl(transcript, entries)
            after_turn = core.analyze_transcript(transcript)
            self.assertEqual(after_turn["compactionCount"], 1)
            self.assertEqual(after_turn["compactions"][0]["newPromptTokens"], 30_622)
            self.assertEqual(after_turn["effectiveTokens"], 31_434)
            self.assertEqual(after_turn["firstTurnTokens"], 367_489)
            self.assertEqual([h["stepIndex"] for h in after_turn["history"]], [4])

    def test_prose_context_summary_mentions_not_counted_as_compactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.jsonl"
            entries = [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "content": "Why did <CONTEXT_SUMMARY> trigger here?",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "model": "gemini-4-hydrogen-high",
                    "input_tokens": 50_000,
                    "cache_read_tokens": 0,
                    "output_tokens": 500,
                    "content": "We check `<CONTEXT_SUMMARY>` in parser.",
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "GENERIC",
                    "status": "DONE",
                    "content": "grep hit: marker_detected = '<CONTEXT_SUMMARY>' in line",
                },
                {
                    "step_index": 3,
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "status": "DONE",
                    "content": (
                        "The following is a <SYSTEM_MESSAGE> not actually sent by the user.\n\n"
                        "<SYSTEM_MESSAGE>\n"
                        "[Message] sender=subagent content=Review mentioned `<CONTEXT_SUMMARY>` handling"
                    ),
                },
            ]
            _write_jsonl(transcript, entries)

            result = core.analyze_transcript(transcript)
            self.assertEqual(result["compactionCount"], 0)
            self.assertEqual(result["compactions"], [])

            # Real wrapped <CONTEXT_SUMMARY> SYSTEM_MESSAGE still counts once.
            entries.append(
                {
                    "step_index": 4,
                    "source": "SYSTEM",
                    "type": "SYSTEM_MESSAGE",
                    "status": "DONE",
                    "content": (
                        "The following is a <SYSTEM_MESSAGE> not actually sent by the user.\n\n"
                        "<SYSTEM_MESSAGE>\n"
                        "<CONTEXT_SUMMARY>Summary body</CONTEXT_SUMMARY>"
                    ),
                }
            )
            _write_jsonl(transcript, entries)
            with_real_marker = core.analyze_transcript(transcript)
            self.assertEqual(with_real_marker["compactionCount"], 1)
            self.assertEqual(with_real_marker["compactions"][0]["reason"], "marker")


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
                self.assertEqual(cfg["autoRefreshSeconds"], core.DEFAULT_AUTO_REFRESH_SECONDS)

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
                clamped = core.update_settings(
                    updated,
                    threshold_tokens=10,
                    warn_percent=120,
                    auto_refresh_seconds=999,
                )
                self.assertEqual(clamped["thresholdTokens"], 1_000)
                self.assertEqual(clamped["warnPercent"], 99)
                self.assertEqual(clamped["autoRefreshSeconds"], core.MAX_AUTO_REFRESH_SECONDS)

                custom_refresh = core.update_settings(updated, auto_refresh_seconds=2)
                self.assertEqual(custom_refresh["autoRefreshSeconds"], 2)
                self.assertEqual(core.load_config()["autoRefreshSeconds"], 2)
                min_refresh = core.update_settings(updated, auto_refresh_seconds=0)
                self.assertEqual(min_refresh["autoRefreshSeconds"], core.MIN_AUTO_REFRESH_SECONDS)

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
                self.assertEqual(overview["conversations"][0]["title"], "Analyze code")

    def test_title_resolution_pbtxt_sqlite_subagent_and_prompt_fallback(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "antigravity"
            cfg_path = Path(tmp) / "context-guard.json"
            for cid, prompt in (
                ("c-pbtxt", "First prompt ignored because pbtxt wins"),
                ("c-sqlite", "Prompt ignored because sqlite wins"),
                ("c-sub", "Review Task 1 files"),
                ("c-prompt", "<USER_REQUEST>\nBuild shiny dashboard\n</USER_REQUEST>"),
            ):
                _write_jsonl(
                    root / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl",
                    [
                        {
                            "step_index": 0,
                            "source": "USER_EXPLICIT",
                            "type": "USER_INPUT",
                            "status": "DONE",
                            "created_at": "2026-11-25T10:00:00Z",
                            "content": prompt,
                        },
                        {
                            "step_index": 1,
                            "source": "MODEL",
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "created_at": "2026-11-25T10:00:05Z",
                            "model": "gemini-4-hydrogen-high",
                            "input_tokens": 5_000,
                            "cache_read_tokens": 10_000,
                            "output_tokens": 500,
                        },
                    ],
                )

            ann_dir = root / "annotations"
            ann_dir.mkdir(parents=True, exist_ok=True)
            (ann_dir / "c-pbtxt.pbtxt").write_text(
                'title:"বাংলা টাইটেল \\"Demo\\""  last_user_view_time:{seconds:1}',
                encoding="utf-8",
            )

            db_path = root / "conversation_summaries.db"
            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id TEXT PRIMARY KEY, title TEXT, preview TEXT, "
                "parent_conversation_id TEXT, agent_name TEXT)"
            )
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                ("c-pbtxt", "Stale SQLite Title", "", "", ""),
            )
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                ("c-sqlite", "SQLite Session Title", "", "", ""),
            )
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                ("c-sub", "", "", "c-pbtxt", "context-guard-reviewer"),
            )
            con.commit()
            con.close()

            env = {
                "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = core.load_config()
                pb_status = core.build_status_payload("c-pbtxt", config=cfg)
                self.assertEqual(pb_status["title"], 'বাংলা টাইটেল "Demo"')
                self.assertFalse(pb_status["isSubagent"])

                overview = core.build_overview_payload(config=cfg, limit=10)
                by_id = {c["conversationId"]: c for c in overview["conversations"]}
                self.assertEqual(by_id["c-pbtxt"]["title"], 'বাংলা টাইটেল "Demo"')
                self.assertEqual(by_id["c-sqlite"]["title"], "SQLite Session Title")
                self.assertEqual(by_id["c-sub"]["title"], "Review Task 1 files")
                self.assertTrue(by_id["c-sub"]["isSubagent"])
                self.assertEqual(by_id["c-sub"]["agentName"], "context-guard-reviewer")
                self.assertEqual(by_id["c-sub"]["parentConversationId"], "c-pbtxt")
                self.assertEqual(by_id["c-prompt"]["title"], "Build shiny dashboard")

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


class TestModelQuota(unittest.TestCase):
    def test_parse_agy_usage_json_and_cache(self) -> None:
        sample_raw = json.dumps(
            {
                "conversation_id": "",
                "status": "SUCCESS",
                "command": {
                    "name": "usage",
                    "data": {
                        "description": "Shared quota pools.",
                        "groups": [
                            {
                                "name": "Gemini Models",
                                "description": "Models within this group: Gemini 4 Hydrogen, Gemini 3.8 Flash",
                                "buckets": [
                                    {
                                        "id": "gemini-weekly",
                                        "name": "Weekly Limit Remaining",
                                        "window": "weekly",
                                        "remaining_fraction": 0.85,
                                        "reset_time": "2026-12-02T22:36:29Z",
                                    },
                                    {
                                        "id": "gemini-5h",
                                        "name": "Five Hour Limit Remaining",
                                        "window": "5h",
                                        "remaining_fraction": 0.51,
                                        "reset_time": "2026-11-26T03:36:29Z",
                                    },
                                ],
                            },
                            {
                                "name": "Claude and GPT models",
                                "description": "Models within this group: Claude Sonnet 4.6, Claude Opus 4.6",
                                "buckets": [
                                    {
                                        "id": "3p-weekly",
                                        "name": "Weekly Limit Remaining",
                                        "window": "weekly",
                                        "remaining_fraction": 1.0,
                                        "reset_time": "2026-12-03T00:00:00Z",
                                    }
                                ],
                            },
                        ],
                    },
                },
            }
        )
        parsed = core.parse_agy_usage_json(sample_raw, fetched_at=1_700_000_000)
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(len(parsed["groups"]), 2)
        gemini = parsed["groups"][0]
        self.assertEqual(gemini["modelsSummary"], "Gemini 4 Hydrogen, Gemini 3.8 Flash")
        # 5h bucket is sorted before weekly
        self.assertEqual(gemini["buckets"][0]["label"], "5h limit")
        self.assertEqual(gemini["buckets"][0]["remainingPercent"], 51.0)
        self.assertEqual(gemini["buckets"][1]["label"], "Weekly limit")
        self.assertEqual(gemini["buckets"][1]["remainingPercent"], 85.0)

        with tempfile.TemporaryDirectory() as tmp:
            quota_path = Path(tmp) / "quota.json"
            fake_cli = Path(tmp) / "agy"
            fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_cli.chmod(0o755)
            env = {
                "CONTEXT_GUARD_QUOTA_PATH": str(quota_path),
                "ANTIGRAVITY_CLI_PATH": str(fake_cli),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                completed = mock.Mock(returncode=0, stdout=sample_raw, stderr="")
                with mock.patch.object(core.subprocess, "run", return_value=completed):
                    fetched = core.fetch_model_quota()
                self.assertEqual(fetched["status"], "ok")
                self.assertTrue(quota_path.is_file())
                cached = core.load_quota_cache(ttl_seconds=300, now_ts=fetched["fetchedAt"] + 10)
                self.assertFalse(cached["stale"])
                self.assertEqual(cached["groups"][0]["buckets"][0]["remainingPercent"], 51.0)


class TestAgyLensMigrationAndLocking(unittest.TestCase):
    def test_migrates_legacy_context_guard_config_to_agy_lens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cfg_dir = fake_home / ".gemini" / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            legacy_cfg_path = cfg_dir / "context-guard.json"
            legacy_cfg_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "thresholdTokens": 250_000,
                        "warnPercent": 75,
                        "mode": "gate",
                        "autoRefreshSeconds": 10,
                        "lastActiveConversationId": "legacy-conv",
                        "conversations": {
                            "legacy-conv": {
                                "lastTokens": 120_000,
                                "customThresholdTokens": 200_000,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            legacy_quota_path = cfg_dir / "context-guard-quota.json"
            legacy_quota_path.write_text(
                json.dumps(
                    {
                        "available": True,
                        "status": "ok",
                        "source": "agy-cli",
                        "fetchedAt": 1_700_000_000,
                        "groups": [
                            {
                                "name": "Gemini Models",
                                "buckets": [
                                    {
                                        "id": "gemini-5h",
                                        "label": "5h limit",
                                        "remainingPercent": 72.0,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            clean_env = {
                k: v
                for k, v in os.environ.items()
                if k
                not in {
                    "AGY_LENS_CONFIG_PATH",
                    "CONTEXT_GUARD_CONFIG_PATH",
                    "AGY_LENS_QUOTA_PATH",
                    "CONTEXT_GUARD_QUOTA_PATH",
                }
            }
            clean_env["HOME"] = str(fake_home)

            with mock.patch.dict(os.environ, clean_env, clear=True):
                expected_cfg_path = cfg_dir / "agy-lens.json"
                expected_quota_path = cfg_dir / "agy-lens-quota.json"
                self.assertEqual(core.get_config_path(), expected_cfg_path)
                self.assertEqual(core.get_quota_cache_path(), expected_quota_path)

                cfg = core.load_config()
                self.assertEqual(cfg["thresholdTokens"], 250_000)
                self.assertEqual(cfg["warnPercent"], 75)
                self.assertEqual(cfg["mode"], "gate")
                self.assertEqual(cfg["autoRefreshSeconds"], 10)
                self.assertEqual(cfg["lastActiveConversationId"], "legacy-conv")
                self.assertEqual(cfg["conversations"]["legacy-conv"]["lastTokens"], 120_000)
                self.assertEqual(cfg["conversations"]["legacy-conv"]["customThresholdTokens"], 200_000)
                self.assertEqual(cfg["loopGuard"], core.DEFAULT_LOOP_GUARD)
                self.assertEqual(cfg["alerts"], core.DEFAULT_ALERTS)

                quota = core.load_quota_cache(ttl_seconds=300, now_ts=1_700_000_050)
                self.assertTrue(quota["available"])
                self.assertFalse(quota["stale"])
                self.assertEqual(quota["groups"][0]["buckets"][0]["remainingPercent"], 72.0)

                saved_path = core.save_config(cfg)
                self.assertEqual(saved_path, expected_cfg_path)
                self.assertTrue(expected_cfg_path.is_file())
                reloaded = json.loads(expected_cfg_path.read_text(encoding="utf-8"))
                self.assertEqual(reloaded["thresholdTokens"], 250_000)
                self.assertEqual(reloaded["loopGuard"], core.DEFAULT_LOOP_GUARD)
                self.assertEqual(reloaded["alerts"], core.DEFAULT_ALERTS)

    def test_update_json_locked_serializes_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared-state.json"
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    for _ in range(10):
                        def _inc(cur: dict[str, object]) -> dict[str, object]:
                            count = int(cur.get("count", 0))  # type: ignore[arg-type]
                            time.sleep(0.002)
                            return {"count": count + 1}

                        core.update_json_locked(target, _inc, default={"count": 0})
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            self.assertEqual(errors, [])
            final_data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(final_data["count"], 20)

    def test_update_settings_clamps_loop_guard_and_alerts_and_preserves_loop_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            empty_root = Path(tmp) / "empty-root"
            env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "CONTEXT_GUARD_BRAIN_ROOTS": str(empty_root),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = core.load_config()
                conv = core.get_conversation_state(cfg, "conv-loop")
                conv["activeLoop"] = {
                    "signature": "read_file:/tmp/a.py",
                    "toolName": "read_file",
                    "streak": 3,
                    "nudged": True,
                }
                conv["loopHistory"] = [
                    {"signature": f"cmd:{i}", "streak": 3 + (i % 2)}
                    for i in range(12)
                ]
                core.save_config(cfg)

                updated = core.update_settings(
                    cfg,
                    loop_guard_patch={
                        "enabled": False,
                        "consecutiveThreshold": 1,
                        "nudgeOncePerStreak": False,
                        "autoPauseThreshold": 2,
                    },
                    alerts_patch={
                        "soundEnabled": False,
                        "desktopNotificationEnabled": True,
                        "notifyOnStop": False,
                        "notifyOnThreshold": False,
                        "notifyOnLoop": True,
                        "notifyOnLowQuotaPct": -5,
                        "cooldownSeconds": -10,
                    },
                )
                self.assertEqual(updated["loopGuard"]["enabled"], False)
                self.assertEqual(updated["loopGuard"]["consecutiveThreshold"], 2)
                self.assertEqual(updated["loopGuard"]["nudgeOncePerStreak"], False)
                self.assertEqual(updated["loopGuard"]["autoPauseThreshold"], 4)
                self.assertEqual(updated["alerts"]["soundEnabled"], False)
                self.assertEqual(updated["alerts"]["notifyOnStop"], False)
                self.assertEqual(updated["alerts"]["notifyOnLowQuotaPct"], 0)
                self.assertEqual(updated["alerts"]["cooldownSeconds"], 0)

                updated2 = core.update_settings(
                    updated,
                    loop_guard_patch={
                        "consecutiveThreshold": 99,
                        "autoPauseThreshold": 99,
                    },
                    alerts_patch={
                        "notifyOnLowQuotaPct": 99,
                        "cooldownSeconds": 999,
                    },
                )
                self.assertEqual(updated2["loopGuard"]["consecutiveThreshold"], 6)
                self.assertEqual(updated2["loopGuard"]["autoPauseThreshold"], 10)
                self.assertEqual(updated2["alerts"]["notifyOnLowQuotaPct"], 50)
                self.assertEqual(updated2["alerts"]["cooldownSeconds"], 300)

                # 0 disables auto-pause
                updated3 = core.update_settings(
                    updated2,
                    loop_guard_patch={"autoPauseThreshold": 0},
                )
                self.assertEqual(updated3["loopGuard"]["autoPauseThreshold"], 0)

                reloaded = core.load_config()
                self.assertEqual(reloaded["loopGuard"]["autoPauseThreshold"], 0)
                self.assertEqual(reloaded["loopGuard"]["consecutiveThreshold"], 6)
                self.assertEqual(reloaded["alerts"]["notifyOnLowQuotaPct"], 50)
                self.assertEqual(reloaded["alerts"]["cooldownSeconds"], 300)
                reloaded_conv = reloaded["conversations"]["conv-loop"]
                self.assertEqual(
                    reloaded_conv["activeLoop"],
                    {
                        "signature": "read_file:/tmp/a.py",
                        "toolName": "read_file",
                        "streak": 3,
                        "nudged": True,
                    },
                )
                self.assertEqual(len(reloaded_conv["loopHistory"]), 10)
                self.assertEqual(reloaded_conv["loopHistory"][0]["signature"], "cmd:2")
                self.assertEqual(reloaded_conv["loopHistory"][-1]["signature"], "cmd:11")


if __name__ == "__main__":
    unittest.main()
