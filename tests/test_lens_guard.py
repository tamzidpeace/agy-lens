from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import context_core as core
import lens_guard as guard
import lens_quota


class TestLensGuard(unittest.TestCase):
    def test_extract_signature_handles_dict_and_json_string_args(self) -> None:
        sig_cmd, name_cmd, _, failed_cmd, err_cmd = guard.extract_tool_signature(
            {
                "toolCall": {
                    "name": "CORTEX_STEP_TYPE_RUN_COMMAND",
                    "args": json.dumps({"CommandLine": "  pytest   tests/test_app.py  "}),
                },
                "error": "exit status 1",
            }
        )
        self.assertEqual(name_cmd, "run_command")
        self.assertEqual(sig_cmd, "cmd:pytest tests/test_app.py")
        self.assertTrue(failed_cmd)
        self.assertIn("exit status 1", err_cmd)

        sig_file, name_file, _, failed_file, _ = guard.extract_tool_signature(
            {
                "toolCall": {
                    "name": "edit_file",
                    "args": {"TargetFile": "/repo/src/main.py"},
                },
                "exitCode": 0,
            }
        )
        self.assertEqual(name_file, "edit_file")
        self.assertEqual(sig_file, "file:/repo/src/main.py")
        self.assertFalse(failed_file)

    def test_failure_streak_triggers_and_resets_on_other_progress(self) -> None:
        cfg = core._default_config()
        state = core._default_conversation_state()
        fail_payload = {
            "toolCall": {"name": "run_command", "args": {"CommandLine": "npm test"}},
            "error": "exit status 1",
            "stepIdx": 1,
        }

        self.assertIsNone(guard.record_tool_use(state, fail_payload, cfg, now_ts=100))
        self.assertIsNone(guard.record_tool_use(state, fail_payload, cfg, now_ts=101))
        active = guard.record_tool_use(state, fail_payload, cfg, now_ts=102)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["count"], 3)
        self.assertEqual(active["reason"], "consecutive_failures")
        self.assertEqual(active["toolName"], "run_command")

        # Progress on a different file resets activeLoop cleanly
        ok_other = {
            "toolCall": {"name": "edit_file", "args": {"Path": "/repo/app.py"}},
            "stepIdx": 4,
        }
        res_after = guard.record_tool_use(state, ok_other, cfg, now_ts=105)
        self.assertIsNone(res_after)
        self.assertIsNone(state["activeLoop"])

    def test_identical_repeat_streak_triggers_at_threshold_plus_one(self) -> None:
        cfg = core._default_config()
        state = core._default_conversation_state()
        read_payload = {
            "toolCall": {"name": "read_file", "args": {"Path": "/repo/a.py", "StartLine": 1}},
            "stepIdx": 1,
        }
        for i in range(3):
            self.assertIsNone(guard.record_tool_use(state, read_payload, cfg, now_ts=200 + i))

        active = guard.record_tool_use(state, read_payload, cfg, now_ts=204)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["reason"], "identical_repeats")
        self.assertEqual(active["count"], 4)

    def test_pre_invocation_nudge_once_then_auto_pause_and_cli_recovery(self) -> None:
        cfg = core._default_config()
        state = core._default_conversation_state()
        fail_payload = {
            "toolCall": {"name": "run_command", "args": {"CommandLine": "cargo test"}},
            "error": "exit status 101",
            "stepIdx": 1,
        }
        for i in range(3):
            guard.record_tool_use(state, fail_payload, cfg, now_ts=300 + i)

        # Count == 3: nudge once on PreInvocation, do not terminate yet
        res1, alert1 = guard.evaluate_loop_hook(
            "PreInvocation", state, {"lastUserInput": "Fix tests"}, cfg
        )
        self.assertTrue(alert1)
        self.assertIn("injectSteps", res1)
        self.assertIn("systemMessage", res1["injectSteps"][0])
        self.assertNotIn("terminationBehavior", res1)

        # Same count == 3 again: nudgedAtCount prevents repeat spam
        res_repeat, alert_repeat = guard.evaluate_loop_hook(
            "PreInvocation", state, {"lastUserInput": "Fix tests"}, cfg
        )
        self.assertEqual(res_repeat, {})
        self.assertFalse(alert_repeat)

        # Escalate to 5 failures (>= autoPauseThreshold=5): PreInvocation terminates once
        guard.record_tool_use(state, fail_payload, cfg, now_ts=304)
        guard.record_tool_use(state, fail_payload, cfg, now_ts=305)
        res_pause, _ = guard.evaluate_loop_hook(
            "PreInvocation", state, {"lastUserInput": "Fix tests"}, cfg
        )
        self.assertEqual(res_pause.get("terminationBehavior"), "terminate")
        self.assertIn("errorMessage", res_pause["injectSteps"][0])

        # CLI recovery: a NEW lastUserInput automatically clears paused activeLoop
        res_recover, alert_recover = guard.evaluate_loop_hook(
            "PreInvocation", state, {"lastUserInput": "Try another file instead"}, cfg
        )
        self.assertEqual(res_recover, {})
        self.assertFalse(alert_recover)
        self.assertIsNone(state["activeLoop"])
        self.assertEqual(state["loopHistory"], [])

    def test_clear_active_loop_persists_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            with mock.patch.dict(os.environ, {"AGY_LENS_CONFIG_PATH": str(cfg_path)}, clear=False):
                cfg = core.load_config()
                cfg["lastActiveConversationId"] = "conv-clear"
                st = core.get_conversation_state(cfg, "conv-clear")
                guard.record_tool_use(
                    st,
                    {"toolCall": {"name": "run_command", "args": {"CommandLine": "fail"}}, "error": "1"},
                    cfg,
                    now_ts=10,
                )
                core.save_config(cfg)

                cleared = guard.clear_active_loop()
                self.assertTrue(cleared["ok"])
                reloaded = core.load_config()
                self.assertIsNone(reloaded["conversations"]["conv-clear"]["activeLoop"])
                self.assertEqual(reloaded["conversations"]["conv-clear"]["loopHistory"], [])

    def test_maybe_send_alert_respects_env_disable_cooldown_and_popen(self) -> None:
        cfg = core._default_config()
        with mock.patch.dict(os.environ, {"AGY_LENS_DISABLE_SYSTEM_ALERTS": "1"}, clear=False):
            with mock.patch("lens_guard.subprocess.Popen") as popen_mock:
                res = guard.maybe_send_alert("stop", "Done", "Finished", cfg, now_ts=1000)
                self.assertFalse(res["sent"])
                popen_mock.assert_not_called()

        clean_env = dict(os.environ)
        clean_env.pop("AGY_LENS_DISABLE_SYSTEM_ALERTS", None)
        with mock.patch.dict(os.environ, clean_env, clear=True):
            with mock.patch("lens_guard.sys.platform", "darwin"):
                with mock.patch("lens_guard.subprocess.Popen") as popen_mock:
                    res1 = guard.maybe_send_alert(
                        "stop", 'Agy "Lens"', "Task complete!", cfg, now_ts=1000
                    )
                    self.assertTrue(res1["sent"])
                    self.assertTrue(res1["soundTriggered"])
                    self.assertTrue(res1["notificationTriggered"])
                    self.assertEqual(popen_mock.call_count, 2)

                    # Within cooldownSeconds=15: skipped unless force=True
                    res2 = guard.maybe_send_alert("stop", "Agy Lens", "Again", cfg, now_ts=1005)
                    self.assertFalse(res2["sent"])
                    self.assertEqual(popen_mock.call_count, 2)

                    # force=True bypasses cooldown
                    res3 = guard.maybe_send_alert(
                        "test", "Test", "Sound check", cfg, force=True, now_ts=1006
                    )
                    self.assertTrue(res3["sent"])
                    self.assertEqual(popen_mock.call_count, 4)

    def test_background_quota_refresh_triggers_low_quota_alert_via_lens_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            quota_path = Path(tmp) / "agy-lens-quota.json"
            hist_path = Path(tmp) / "agy-lens-quota-history.json"
            env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "AGY_LENS_QUOTA_PATH": str(quota_path),
                "AGY_LENS_QUOTA_HISTORY_PATH": str(hist_path),
            }
            fake_quota = {
                "available": True,
                "status": "ok",
                "source": "agy-cli",
                "fetchedAt": 5000,
                "stale": False,
                "fetching": False,
                "ageSeconds": 0,
                "description": "",
                "groups": [
                    {
                        "name": "Gemini Models",
                        "modelsSummary": "Gemini 4 Hydrogen",
                        "buckets": [
                            {
                                "id": "gemini:5h",
                                "window": "5h",
                                "label": "5h limit",
                                "remainingFraction": 0.1,
                                "remainingPercent": 10.0,
                                "resetTime": "2026-11-26T10:00:00Z",
                            }
                        ],
                    }
                ],
                "error": "",
            }
            clean_env = {k: v for k, v in os.environ.items() if k != "AGY_LENS_DISABLE_SYSTEM_ALERTS"}
            clean_env.update(env)
            with mock.patch.dict(os.environ, clean_env, clear=True):
                with mock.patch("context_core.fetch_model_quota", return_value=fake_quota):
                    with mock.patch("lens_guard.sys.platform", "darwin"):
                        with mock.patch("lens_guard.subprocess.Popen") as popen_mock:
                            lens_quota.run_background_refresh()
                            self.assertGreaterEqual(popen_mock.call_count, 1)
                            reloaded = core.load_config()
                            self.assertEqual(
                                reloaded["alerts"]["lastLowQuotaAlertByGroup"].get("Gemini Models"),
                                "2026-11-26T10:00:00Z",
                            )
                            # Second call for same reset cycle should be deduped (no extra Popen)
                            count_before = popen_mock.call_count
                            lens_quota.run_background_refresh()
                            self.assertEqual(popen_mock.call_count, count_before)


if __name__ == "__main__":
    unittest.main()
