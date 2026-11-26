from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_core as core


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_payload(
    *,
    fetched_at: int,
    groups: list[dict] | None = None,
    available: bool = True,
    status: str = "ok",
) -> dict:
    return {
        "available": available,
        "status": status,
        "source": "agy-cli",
        "fetchedAt": fetched_at,
        "stale": False,
        "fetching": False,
        "ageSeconds": 0,
        "description": "Shared quota pools.",
        "groups": groups if groups is not None else [],
        "error": "",
    }


def _make_bucket(
    group_name: str,
    window: str,
    remaining_pct: float,
    reset_epoch: int | None,
) -> dict:
    return {
        "id": f"{group_name}:{window}",
        "window": window,
        "label": "5h limit" if window == "5h" else "Weekly limit",
        "remainingFraction": round(remaining_pct / 100.0, 4),
        "remainingPercent": float(remaining_pct),
        "resetTime": _iso(reset_epoch) if reset_epoch is not None else "",
    }


def _make_history_sample(
    captured_at: int,
    entries: list[tuple[str, str, float, int | None]],
) -> dict:
    by_group: dict[str, list[dict]] = {}
    for group_name, window, pct, reset_epoch in entries:
        by_group.setdefault(group_name, []).append(
            {
                "window": window,
                "remainingPercent": float(pct),
                "resetAtEpoch": reset_epoch,
            }
        )
    return {
        "capturedAtEpoch": captured_at,
        "capturedAt": _iso(captured_at),
        "groups": [
            {"name": gname, "windows": windows}
            for gname, windows in by_group.items()
        ],
    }


class TestLensQuotaPredictions(unittest.TestCase):
    def test_burn_rate_at_risk_tight_and_safe_predictions(self) -> None:
        import lens_quota

        now_ts = 1_700_003_600
        # 30 minutes (1800s = 0.5h) observation window
        t0 = now_ts - 1800

        history = [
            _make_history_sample(
                t0,
                [
                    ("Gemini", "5h", 90.0, now_ts + 9000),      # 2.5h to reset
                    ("Claude", "5h", 60.0, now_ts + 8280),      # 2.3h to reset
                    ("GPT", "5h", 65.0, now_ts + 14400),        # 4.0h to reset
                    ("NoReset", "5h", 80.0, None),
                ],
            )
        ]

        payload = _make_payload(
            fetched_at=now_ts,
            groups=[
                {
                    "name": "Gemini",
                    "modelsSummary": "Gemini 4",
                    # 90 -> 70 in 0.5h => burn 40%/h, exhaust 1.75h, reset 2.5h => buffer -0.75 (at_risk)
                    "buckets": [_make_bucket("Gemini", "5h", 70.0, now_ts + 9000)],
                },
                {
                    "name": "Claude",
                    "modelsSummary": "Claude 4",
                    # 60 -> 50 in 0.5h => burn 20%/h, exhaust 2.5h, reset 2.3h => buffer +0.2 (tight)
                    "buckets": [_make_bucket("Claude", "5h", 50.0, now_ts + 8280)],
                },
                {
                    "name": "GPT",
                    "modelsSummary": "GPT-5",
                    # 65 -> 60 in 0.5h => burn 10%/h, exhaust 6.0h, reset 4.0h => buffer +2.0 (safe)
                    "buckets": [_make_bucket("GPT", "5h", 60.0, now_ts + 14400)],
                },
                {
                    "name": "NoReset",
                    "modelsSummary": "Other",
                    # 80 -> 70 in 0.5h => burn 20%/h, exhaust 3.5h, no reset => risk unknown
                    "buckets": [_make_bucket("NoReset", "5h", 70.0, None)],
                },
            ],
        )
        snapshot_before = copy.deepcopy(payload)

        enriched = lens_quota.compute_quota_predictions(payload, history, now_ts=now_ts)
        # Must not mutate caller payload
        self.assertEqual(payload, snapshot_before)
        self.assertNotIn("prediction", payload["groups"][0]["buckets"][0])

        by_group = {g["name"]: g["buckets"][0]["prediction"] for g in enriched["groups"]}

        self.assertEqual(
            by_group["Gemini"],
            {
                "state": "active",
                "burnRatePerHour": 40.0,
                "hoursUntilExhausted": 1.75,
                "hoursUntilReset": 2.5,
                "bufferHours": -0.75,
                "riskLevel": "at_risk",
                "sampleCount": 2,
                "windowObservedSeconds": 1800,
                "usedDeltaPct": 20.0,
            },
        )
        self.assertEqual(by_group["Claude"]["riskLevel"], "tight")
        self.assertEqual(by_group["Claude"]["bufferHours"], 0.2)
        self.assertEqual(by_group["GPT"]["riskLevel"], "safe")
        self.assertEqual(by_group["GPT"]["bufferHours"], 2.0)
        self.assertEqual(by_group["NoReset"]["state"], "active")
        self.assertIsNone(by_group["NoReset"]["hoursUntilReset"])
        self.assertIsNone(by_group["NoReset"]["bufferHours"])
        self.assertEqual(by_group["NoReset"]["riskLevel"], "unknown")

    def test_reset_boundary_stops_before_reset_jump_and_reset_time_shift(self) -> None:
        import lens_quota

        now_ts = 1_700_010_000
        reset_cur = now_ts + 10_800  # 3.0h

        # Group A: reset jump (40% -> 100% -> 90% -> current 80%)
        # Group B: resetTime shift > 300s (older sample has previous cycle reset)
        history = [
            _make_history_sample(
                now_ts - 5400,
                [
                    ("JumpGroup", "5h", 40.0, reset_cur),
                    ("ShiftGroup", "5h", 25.0, reset_cur - 18_000),
                ],
            ),
            _make_history_sample(
                now_ts - 3600,
                [
                    ("JumpGroup", "5h", 100.0, reset_cur),
                    ("ShiftGroup", "5h", 95.0, reset_cur + 120),  # within 300s jitter
                ],
            ),
            _make_history_sample(
                now_ts - 1800,
                [
                    ("JumpGroup", "5h", 90.0, reset_cur),
                    ("ShiftGroup", "5h", 85.0, reset_cur),
                ],
            ),
        ]

        payload = _make_payload(
            fetched_at=now_ts,
            groups=[
                {
                    "name": "JumpGroup",
                    "buckets": [_make_bucket("JumpGroup", "5h", 80.0, reset_cur)],
                },
                {
                    "name": "ShiftGroup",
                    "buckets": [_make_bucket("ShiftGroup", "5h", 75.0, reset_cur)],
                },
            ],
        )

        enriched = lens_quota.compute_quota_predictions(payload, history, now_ts=now_ts)
        jump_pred = enriched["groups"][0]["buckets"][0]["prediction"]
        shift_pred = enriched["groups"][1]["buckets"][0]["prediction"]

        # Must stop before t=-5400 (40%) and use [t=-3600 (100%), t=-1800 (90%), t=0 (80%)]
        self.assertEqual(jump_pred["sampleCount"], 3)
        self.assertEqual(jump_pred["windowObservedSeconds"], 3600)
        self.assertEqual(jump_pred["usedDeltaPct"], 20.0)
        self.assertEqual(jump_pred["burnRatePerHour"], 20.0)

        # Must stop before t=-5400 (shifted reset) and use [t=-3600 (95%), t=-1800 (85%), t=0 (75%)]
        self.assertEqual(shift_pred["sampleCount"], 3)
        self.assertEqual(shift_pred["windowObservedSeconds"], 3600)
        self.assertEqual(shift_pred["usedDeltaPct"], 20.0)
        self.assertEqual(shift_pred["burnRatePerHour"], 20.0)

    def test_warming_and_idle_states(self) -> None:
        import lens_quota

        now_ts = 1_700_020_000
        reset_cur = now_ts + 7200

        history = [
            _make_history_sample(
                now_ts - 3600,
                [
                    ("IdleGroup", "5h", 80.3, reset_cur),
                ],
            ),
            _make_history_sample(
                now_ts - 120,  # < 180s
                [
                    ("WarmGroup", "5h", 95.0, reset_cur),
                ],
            ),
        ]

        payload = _make_payload(
            fetched_at=now_ts,
            groups=[
                {
                    "name": "WarmGroup",
                    "buckets": [_make_bucket("WarmGroup", "5h", 88.0, reset_cur)],
                },
                {
                    "name": "IdleGroup",
                    "buckets": [_make_bucket("IdleGroup", "5h", 80.0, reset_cur)],
                },
            ],
        )

        enriched = lens_quota.compute_quota_predictions(payload, history, now_ts=now_ts)
        warm_pred = enriched["groups"][0]["buckets"][0]["prediction"]
        idle_pred = enriched["groups"][1]["buckets"][0]["prediction"]

        self.assertEqual(warm_pred["state"], "warming")
        self.assertIsNone(warm_pred["burnRatePerHour"])
        self.assertIsNone(warm_pred["hoursUntilExhausted"])
        self.assertIsNone(warm_pred["bufferHours"])
        self.assertEqual(warm_pred["riskLevel"], "unknown")
        self.assertEqual(warm_pred["windowObservedSeconds"], 120)

        self.assertEqual(idle_pred["state"], "idle")
        self.assertEqual(idle_pred["burnRatePerHour"], 0.0)
        self.assertIsNone(idle_pred["hoursUntilExhausted"])
        self.assertIsNone(idle_pred["bufferHours"])
        self.assertEqual(idle_pred["riskLevel"], "safe")
        self.assertEqual(idle_pred["usedDeltaPct"], 0.3)


class TestLensQuotaHistoryAndWorker(unittest.TestCase):
    def test_record_quota_sample_dedup_pruning_and_cap(self) -> None:
        import lens_quota

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            hist_path = Path(tmp) / "agy-lens-quota-history.json"
            env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "AGY_LENS_QUOTA_HISTORY_PATH": str(hist_path),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(lens_quota.get_quota_history_path(), hist_path)

                # Corrupt file recovers cleanly
                hist_path.write_text("{bad json", encoding="utf-8")
                self.assertEqual(lens_quota.load_quota_history(), [])

                base_ts = 1_700_000_000
                reset_ts = base_ts + 18_000

                # Ineligible payload is ignored
                bad_payload = _make_payload(
                    fetched_at=base_ts,
                    available=False,
                    status="error",
                    groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", 80.0, reset_ts)]}],
                )
                self.assertEqual(lens_quota.record_quota_sample(bad_payload, now_ts=base_ts), [])
                self.assertEqual(hist_path.read_text(encoding="utf-8"), "{bad json")

                # Seed 245 samples spanning > 7 days to test pruning + 240 cap
                old_ts = base_ts - (7 * 86400) - 600
                p0 = _make_payload(
                    fetched_at=old_ts,
                    groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", 99.0, reset_ts)]}],
                )
                lens_quota.record_quota_sample(p0, now_ts=old_ts)

                # Add 242 samples spaced 200s apart ending at base_ts
                start_recent = base_ts - (241 * 200)
                for idx in range(242):
                    ts = start_recent + idx * 200
                    pct = round(90.0 - (idx * 0.1), 1)
                    p = _make_payload(
                        fetched_at=ts,
                        groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", pct, reset_ts)]}],
                    )
                    samples = lens_quota.record_quota_sample(p, now_ts=ts)

                # Old (> 7d) sample must be pruned and list capped at 240
                self.assertEqual(len(samples), 240)
                self.assertGreaterEqual(samples[0]["capturedAtEpoch"], base_ts - 7 * 86400)
                self.assertEqual(samples[-1]["capturedAtEpoch"], base_ts)

                # Dedup: <180s later with <0.2% change and <=120s reset shift -> skipped
                last_pct = samples[-1]["groups"][0]["windows"][0]["remainingPercent"]
                dup_p = _make_payload(
                    fetched_at=base_ts + 60,
                    groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", last_pct, reset_ts + 60)]}],
                )
                after_dup = lens_quota.record_quota_sample(dup_p, now_ts=base_ts + 60)
                self.assertEqual(len(after_dup), 240)
                self.assertEqual(after_dup[-1]["capturedAtEpoch"], base_ts)

                # <180s later WITH >=0.2% change -> appended (still capped at 240)
                changed_p = _make_payload(
                    fetched_at=base_ts + 90,
                    groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", round(last_pct - 0.5, 1), reset_ts)]}],
                )
                after_change = lens_quota.record_quota_sample(changed_p, now_ts=base_ts + 90)
                self.assertEqual(len(after_change), 240)
                self.assertEqual(after_change[-1]["capturedAtEpoch"], base_ts + 90)

    def test_maybe_spawn_background_quota_refresh_respects_throttle_and_disable_env(self) -> None:
        import lens_quota

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            quota_path = Path(tmp) / "agy-lens-quota.json"
            now_ts = 1_700_050_000

            base_env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "AGY_LENS_QUOTA_PATH": str(quota_path),
                "CONTEXT_GUARD_DISABLE_AUTO_QUOTA": "",
            }

            with mock.patch.dict(os.environ, base_env, clear=False):
                with mock.patch.object(lens_quota.subprocess, "Popen") as popen_mock:
                    # 1. Disabled via env
                    with mock.patch.dict(os.environ, {"CONTEXT_GUARD_DISABLE_AUTO_QUOTA": "1"}, clear=False):
                        self.assertFalse(lens_quota.maybe_spawn_background_quota_refresh(now_ts=now_ts))
                        popen_mock.assert_not_called()

                    # 2. Fresh cache -> skip
                    core.save_json_locked(
                        quota_path,
                        _make_payload(
                            fetched_at=now_ts - 60,
                            groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", 75.0, now_ts + 3600)]}],
                        ),
                    )
                    self.assertFalse(
                        lens_quota.maybe_spawn_background_quota_refresh(min_interval_seconds=300, now_ts=now_ts)
                    )
                    popen_mock.assert_not_called()

                    # 3. Stale cache -> spawns detached worker and writes lock timestamp
                    core.save_json_locked(
                        quota_path,
                        _make_payload(
                            fetched_at=now_ts - 600,
                            groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", 75.0, now_ts + 3600)]}],
                        ),
                    )
                    spawned = lens_quota.maybe_spawn_background_quota_refresh(min_interval_seconds=300, now_ts=now_ts)
                    self.assertTrue(spawned)
                    self.assertEqual(popen_mock.call_count, 1)
                    args, kwargs = popen_mock.call_args
                    self.assertIn("--refresh-background", args[0])
                    self.assertTrue(kwargs.get("start_new_session"))
                    self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
                    self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)
                    self.assertEqual(kwargs.get("stderr"), subprocess.DEVNULL)

                    # 4. Second call within min_interval_seconds -> throttled
                    spawned_again = lens_quota.maybe_spawn_background_quota_refresh(
                        min_interval_seconds=300,
                        now_ts=now_ts + 120,
                    )
                    self.assertFalse(spawned_again)
                    self.assertEqual(popen_mock.call_count, 1)

                    # 5. Call after min_interval_seconds -> spawns again
                    spawned_later = lens_quota.maybe_spawn_background_quota_refresh(
                        min_interval_seconds=300,
                        now_ts=now_ts + 305,
                    )
                    self.assertTrue(spawned_later)
                    self.assertEqual(popen_mock.call_count, 2)

    def test_load_quota_cache_and_fetch_model_quota_attach_predictions_and_history(self) -> None:
        import lens_quota

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            quota_path = Path(tmp) / "agy-lens-quota.json"
            hist_path = Path(tmp) / "agy-lens-quota-history.json"
            fake_cli = Path(tmp) / "agy"
            fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_cli.chmod(0o755)

            t0 = 1_700_000_000
            t1 = t0 + 1800
            reset_iso = _iso(t1 + 7200)

            env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "AGY_LENS_QUOTA_PATH": str(quota_path),
                "AGY_LENS_QUOTA_HISTORY_PATH": str(hist_path),
                "ANTIGRAVITY_CLI_PATH": str(fake_cli),
            }
            sample_raw = json.dumps(
                {
                    "status": "ok",
                    "command": {
                        "data": {
                            "description": "Shared quota pools.",
                            "groups": [
                                {
                                    "name": "Gemini",
                                    "description": "Models within this group: Gemini 4",
                                    "buckets": [
                                        {
                                            "id": "gem-5h",
                                            "window": "5h",
                                            "name": "5h remaining",
                                            "remaining_fraction": 0.70,
                                            "reset_time": reset_iso,
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                }
            )

            with mock.patch.dict(os.environ, env, clear=False):
                # Seed an earlier history sample at t0 with 90%
                seed_payload = _make_payload(
                    fetched_at=t0,
                    groups=[{"name": "Gemini", "buckets": [_make_bucket("Gemini", "5h", 90.0, t1 + 7200)]}],
                )
                lens_quota.record_quota_sample(seed_payload, now_ts=t0)

                completed = mock.Mock(returncode=0, stdout=sample_raw, stderr="")
                with mock.patch.object(core.time, "time", return_value=t1):
                    with mock.patch.object(core.subprocess, "run", return_value=completed):
                        fetched = core.fetch_model_quota()

                pred = fetched["groups"][0]["buckets"][0]["prediction"]
                self.assertEqual(pred["state"], "active")
                self.assertEqual(pred["burnRatePerHour"], 40.0)
                self.assertEqual(pred["sampleCount"], 2)

                # History should now have 2 samples (t0 and t1)
                self.assertEqual(len(lens_quota.load_quota_history()), 2)

                # load_quota_cache() must also attach prediction cleanly
                cached = core.load_quota_cache(ttl_seconds=300, now_ts=t1 + 60)
                self.assertIn("prediction", cached["groups"][0]["buckets"][0])
                self.assertEqual(cached["groups"][0]["buckets"][0]["prediction"]["burnRatePerHour"], 40.0)

    def test_run_background_refresh_low_quota_alerts_per_group_and_reset_cycle(self) -> None:
        import lens_quota

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            quota_path = Path(tmp) / "agy-lens-quota.json"
            hist_path = Path(tmp) / "agy-lens-quota-history.json"
            env = {
                "AGY_LENS_CONFIG_PATH": str(cfg_path),
                "AGY_LENS_QUOTA_PATH": str(quota_path),
                "AGY_LENS_QUOTA_HISTORY_PATH": str(hist_path),
            }

            now_ts = 1_700_050_000
            reset_gem = now_ts + 3600
            reset_claude = now_ts + 7200

            core.save_json_locked(
                cfg_path,
                {
                    "alerts": {
                        "notifyOnLowQuotaPct": 15,
                    }
                },
            )

            fake_payload = _make_payload(
                fetched_at=now_ts,
                groups=[
                    {
                        "name": "Gemini",
                        "buckets": [
                            _make_bucket("Gemini", "5h", 12.0, reset_gem),
                            _make_bucket("Gemini", "weekly", 80.0, reset_gem + 86400),
                        ],
                    },
                    {
                        "name": "Claude",
                        "buckets": [
                            _make_bucket("Claude", "5h", 40.0, reset_claude),
                        ],
                    },
                ],
            )

            fake_guard = types.ModuleType("lens_guard")
            alert_calls: list[dict] = []

            def _fake_maybe_send_alert(*args, **kwargs):
                alert_calls.append({"args": args, "kwargs": kwargs})
                return True

            fake_guard.maybe_send_alert = _fake_maybe_send_alert  # type: ignore[attr-defined]

            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(core, "fetch_model_quota", return_value=fake_payload):
                    with mock.patch.dict(sys.modules, {"lens_guard": fake_guard}):
                        lens_quota.run_background_refresh()
                        self.assertEqual(len(alert_calls), 1)

                        # Same reset cycle -> must not alert again
                        lens_quota.run_background_refresh()
                        self.assertEqual(len(alert_calls), 1)

                    cfg_after = core.load_config()
                    self.assertEqual(
                        cfg_after["alerts"]["lastLowQuotaAlertByGroup"].get("Gemini"),
                        _iso(reset_gem),
                    )
                    self.assertNotIn("Claude", cfg_after["alerts"]["lastLowQuotaAlertByGroup"])

                    # New reset cycle for Gemini + Claude drops below threshold -> both alert
                    new_payload = _make_payload(
                        fetched_at=now_ts + 4000,
                        groups=[
                            {
                                "name": "Gemini",
                                "buckets": [_make_bucket("Gemini", "5h", 10.0, reset_gem + 18000)],
                            },
                            {
                                "name": "Claude",
                                "buckets": [_make_bucket("Claude", "5h", 14.0, reset_claude)],
                            },
                        ],
                    )
                    with mock.patch.object(core, "fetch_model_quota", return_value=new_payload):
                        with mock.patch.dict(sys.modules, {"lens_guard": fake_guard}):
                            lens_quota.run_background_refresh()
                            self.assertEqual(len(alert_calls), 3)

    def test_parse_iso_epoch_and_cli_refresh_without_lens_guard(self) -> None:
        import lens_quota

        self.assertEqual(lens_quota.parse_iso_epoch("2023-11-14T22:13:20Z"), 1_700_000_000)
        self.assertEqual(lens_quota.parse_iso_epoch("2023-11-14T22:13:20+00:00"), 1_700_000_000)
        self.assertIsNone(lens_quota.parse_iso_epoch(""))
        self.assertIsNone(lens_quota.parse_iso_epoch("not-a-timestamp"))

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "agy-lens.json"
            core.save_json_locked(cfg_path, {"alerts": {"notifyOnLowQuotaPct": 20}})
            now_ts = 1_700_060_000
            low_payload = _make_payload(
                fetched_at=now_ts,
                groups=[
                    {
                        "name": "Gemini",
                        "buckets": [_make_bucket("Gemini", "5h", 10.0, now_ts + 3600)],
                    }
                ],
            )
            with mock.patch.dict(os.environ, {"AGY_LENS_CONFIG_PATH": str(cfg_path)}, clear=False):
                with mock.patch.object(core, "fetch_model_quota", return_value=low_payload):
                    with mock.patch.dict(sys.modules, {"lens_guard": None}):
                        rc = lens_quota.main(["--refresh-background"])
                        self.assertEqual(rc, 0)
                cfg_after = core.load_config()
                self.assertEqual(
                    cfg_after["alerts"]["lastLowQuotaAlertByGroup"].get("Gemini"),
                    _iso(now_ts + 3600),
                )


if __name__ == "__main__":
    unittest.main()
