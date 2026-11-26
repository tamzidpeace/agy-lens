#!/usr/bin/env python3
"""Quota burn-rate predictor, ring-buffer history, and background refresh worker.

Tracks compact historical quota snapshots in `agy-lens-quota-history.json`,
computes per-bucket burn rate, exhaustion ETA, and reset risk predictions, and
manages throttled detached background quota refreshes with per-group 5h low-quota
alert deduplication.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core as core

HISTORY_RETENTION_SECONDS = 7 * 86_400
HISTORY_MAX_SAMPLES = 240
DEDUP_MIN_INTERVAL_SECONDS = 180
DEDUP_MIN_PCT_DELTA = 0.2
DEDUP_MAX_RESET_SHIFT_SECONDS = 120

WARMING_MIN_SECONDS = 180
IDLE_MAX_DELTA_PCT = 0.5
RESET_TIME_SHIFT_TOLERANCE_SECONDS = 300
RESET_JUMP_TOLERANCE_PCT = 1.5
DEFAULT_SPAWN_MIN_INTERVAL_SECONDS = 300


def _get_quota_state_dir() -> Path:
    """Resolve directory for quota history and spawn lock files."""
    if core._has_quota_path_override() and not core._has_config_path_override():
        return core.get_quota_cache_path().parent
    return core.get_config_path().parent


def get_quota_history_path() -> Path:
    """Return quota history path, honoring AGY_LENS_QUOTA_HISTORY_PATH override."""
    override = os.environ.get("AGY_LENS_QUOTA_HISTORY_PATH", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return _get_quota_state_dir() / "agy-lens-quota-history.json"


def get_quota_spawn_lock_path() -> Path:
    """Return path to the background quota spawn throttle lock file."""
    return _get_quota_state_dir() / ".agy-lens-quota-spawn.json"


def parse_iso_epoch(raw_iso: str) -> int | None:
    """Parse an RFC3339 / ISO-8601 timestamp string into a UTC epoch integer."""
    if not isinstance(raw_iso, str):
        return None
    text = raw_iso.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _epoch_to_iso(epoch: int) -> str:
    """Format an integer UTC epoch timestamp as an ISO-8601 Z string."""
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_float(value: Any) -> float | None:
    """Parse a finite non-boolean numeric value as float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _normalize_history_samples(raw_samples: Any) -> list[dict[str, Any]]:
    """Sanitize and chronologically sort quota history sample entries."""
    if not isinstance(raw_samples, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in raw_samples:
        if not isinstance(entry, dict):
            continue
        epoch = core._parse_int(entry.get("capturedAtEpoch"))
        if epoch is None or epoch <= 0:
            continue
        raw_groups = entry.get("groups")
        if not isinstance(raw_groups, list):
            continue

        groups: list[dict[str, Any]] = []
        for grp in raw_groups:
            if not isinstance(grp, dict):
                continue
            grp_name = str(grp.get("name") or "").strip()
            raw_windows = grp.get("windows")
            if not grp_name or not isinstance(raw_windows, list):
                continue
            windows: list[dict[str, Any]] = []
            for win in raw_windows:
                if not isinstance(win, dict):
                    continue
                win_key = str(win.get("window") or "").strip()
                pct = _parse_float(win.get("remainingPercent"))
                if not win_key or pct is None:
                    continue
                reset_epoch = core._parse_int(win.get("resetAtEpoch"))
                windows.append(
                    {
                        "window": win_key,
                        "remainingPercent": round(max(0.0, min(100.0, pct)), 1),
                        "resetAtEpoch": reset_epoch,
                    }
                )
            if windows:
                groups.append({"name": grp_name, "windows": windows})
        if groups:
            captured_at = str(entry.get("capturedAt") or "").strip() or _epoch_to_iso(epoch)
            cleaned.append(
                {
                    "capturedAtEpoch": epoch,
                    "capturedAt": captured_at,
                    "groups": groups,
                }
            )
    cleaned.sort(key=lambda item: item["capturedAtEpoch"])
    return cleaned


def load_quota_history() -> list[dict[str, Any]]:
    """Load and normalize quota history samples safely; return [] if missing/corrupt."""
    path = get_quota_history_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return _normalize_history_samples(raw.get("samples"))


def _extract_sample_from_payload(
    payload: dict[str, Any],
    *,
    now_ts: int | None = None,
) -> dict[str, Any] | None:
    """Build a compact history sample from an eligible live quota payload."""
    if not isinstance(payload, dict):
        return None
    if not payload.get("available") or payload.get("status") != "ok":
        return None
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return None

    if now_ts is not None:
        epoch = int(now_ts)
    else:
        fetched_at = core._parse_int(payload.get("fetchedAt"))
        epoch = fetched_at if fetched_at and fetched_at > 0 else int(time.time())

    groups: list[dict[str, Any]] = []
    for grp in raw_groups:
        if not isinstance(grp, dict):
            continue
        grp_name = str(grp.get("name") or "").strip()
        raw_buckets = grp.get("buckets")
        if not grp_name or not isinstance(raw_buckets, list):
            continue
        windows: list[dict[str, Any]] = []
        for bucket in raw_buckets:
            if not isinstance(bucket, dict):
                continue
            win_key = str(bucket.get("window") or bucket.get("label") or "").strip()
            pct = _parse_float(bucket.get("remainingPercent"))
            if not win_key or pct is None:
                continue
            reset_epoch = parse_iso_epoch(str(bucket.get("resetTime") or ""))
            if reset_epoch is None:
                reset_epoch = core._parse_int(bucket.get("resetAtEpoch"))
            windows.append(
                {
                    "window": win_key,
                    "remainingPercent": round(max(0.0, min(100.0, pct)), 1),
                    "resetAtEpoch": reset_epoch,
                }
            )
        if windows:
            groups.append({"name": grp_name, "windows": windows})

    if not groups:
        return None
    return {
        "capturedAtEpoch": epoch,
        "capturedAt": _epoch_to_iso(epoch),
        "groups": groups,
    }


def _build_sample_lookup(
    sample: dict[str, Any],
) -> dict[tuple[str, str], tuple[float, int | None]]:
    """Index a history sample's buckets by (group_name, window_key)."""
    out: dict[tuple[str, str], tuple[float, int | None]] = {}
    for grp in sample.get("groups", []):
        grp_name = str(grp.get("name") or "").strip()
        for win in grp.get("windows", []):
            win_key = str(win.get("window") or "").strip()
            pct = _parse_float(win.get("remainingPercent"))
            if grp_name and win_key and pct is not None:
                out[(grp_name, win_key)] = (pct, core._parse_int(win.get("resetAtEpoch")))
    return out


def _is_duplicate_sample(latest: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Return True if candidate is too close in time and unchanged from latest."""
    age = candidate["capturedAtEpoch"] - latest["capturedAtEpoch"]
    if age < 0 or age >= DEDUP_MIN_INTERVAL_SECONDS:
        return False

    latest_map = _build_sample_lookup(latest)
    cand_map = _build_sample_lookup(candidate)
    if set(latest_map.keys()) != set(cand_map.keys()):
        return False

    for key, (cand_pct, cand_reset) in cand_map.items():
        old_pct, old_reset = latest_map[key]
        if abs(cand_pct - old_pct) >= DEDUP_MIN_PCT_DELTA:
            return False
        if cand_reset != old_reset:
            if cand_reset is None or old_reset is None:
                return False
            if abs(cand_reset - old_reset) > DEDUP_MAX_RESET_SHIFT_SECONDS:
                return False
    return True


def record_quota_sample(
    payload: dict[str, Any],
    *,
    now_ts: int | None = None,
) -> list[dict[str, Any]]:
    """Record a deduplicated, bounded quota history sample under file lock."""
    candidate = _extract_sample_from_payload(payload, now_ts=now_ts)
    if candidate is None:
        return load_quota_history()

    result_samples: list[dict[str, Any]] = []

    def _updater(disk_raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal result_samples
        samples = _normalize_history_samples(disk_raw.get("samples"))
        if not (samples and _is_duplicate_sample(samples[-1], candidate)):
            samples.append(candidate)
            samples.sort(key=lambda item: item["capturedAtEpoch"])

        newest_epoch = samples[-1]["capturedAtEpoch"]
        cutoff = newest_epoch - HISTORY_RETENTION_SECONDS
        samples = [s for s in samples if s["capturedAtEpoch"] >= cutoff]
        if len(samples) > HISTORY_MAX_SAMPLES:
            samples = samples[-HISTORY_MAX_SAMPLES:]

        result_samples = samples
        return {"version": 1, "samples": samples}

    core.update_json_locked(
        get_quota_history_path(),
        _updater,
        default={"version": 1, "samples": []},
    )
    return result_samples


def _predict_bucket(
    group_name: str,
    bucket: dict[str, Any],
    indexed_history: list[tuple[int, dict[tuple[str, str], tuple[float, int | None]]]],
    *,
    observation_ts: int,
    current_ts: int,
) -> dict[str, Any]:
    """Compute burn-rate and reset exhaustion prediction for a single bucket."""
    window_key = str(bucket.get("window") or bucket.get("label") or "").strip()
    current_pct = _parse_float(bucket.get("remainingPercent"))
    if current_pct is None:
        current_pct = 0.0
    current_reset = parse_iso_epoch(str(bucket.get("resetTime") or ""))

    matched_historical: list[dict[str, Any]] = []
    newer_remaining = current_pct

    for sample_ts, lookup in reversed(indexed_history):
        if sample_ts >= observation_ts:
            continue
        point = lookup.get((group_name, window_key))
        if point is None:
            continue
        sample_pct, sample_reset = point
        if (
            current_reset is not None
            and sample_reset is not None
            and abs(sample_reset - current_reset) > RESET_TIME_SHIFT_TOLERANCE_SECONDS
        ):
            break
        if sample_pct < newer_remaining - RESET_JUMP_TOLERANCE_PCT:
            break
        matched_historical.append(
            {
                "capturedAtEpoch": sample_ts,
                "remainingPercent": sample_pct,
            }
        )
        newer_remaining = sample_pct

    matched_historical.reverse()
    series = matched_historical + [
        {
            "capturedAtEpoch": observation_ts,
            "remainingPercent": current_pct,
        }
    ]

    window_observed = max(0, series[-1]["capturedAtEpoch"] - series[0]["capturedAtEpoch"])
    used_delta_pct = max(
        0.0,
        round(series[0]["remainingPercent"] - series[-1]["remainingPercent"], 2),
    )
    hours_until_reset = (
        round(max(0.0, (current_reset - current_ts) / 3600.0), 2)
        if current_reset is not None
        else None
    )

    if window_observed < WARMING_MIN_SECONDS:
        state = "warming"
        burn_rate_per_hour: float | None = None
        hours_until_exhausted: float | None = None
        buffer_hours: float | None = None
        risk_level = "unknown"
    elif used_delta_pct < IDLE_MAX_DELTA_PCT:
        state = "idle"
        burn_rate_per_hour = 0.0
        hours_until_exhausted = None
        buffer_hours = None
        risk_level = "safe"
    else:
        state = "active"
        burn_rate_per_hour = round(used_delta_pct / (window_observed / 3600.0), 1)
        hours_until_exhausted = (
            round(series[-1]["remainingPercent"] / burn_rate_per_hour, 2)
            if burn_rate_per_hour > 0
            else None
        )
        if hours_until_reset is not None and hours_until_exhausted is not None:
            buffer_hours = round(hours_until_exhausted - hours_until_reset, 2)
            if buffer_hours < -0.1:
                risk_level = "at_risk"
            elif buffer_hours <= 0.5:
                risk_level = "tight"
            else:
                risk_level = "safe"
        else:
            buffer_hours = None
            risk_level = "unknown"

    return {
        "state": state,
        "burnRatePerHour": burn_rate_per_hour,
        "hoursUntilExhausted": hours_until_exhausted,
        "hoursUntilReset": hours_until_reset,
        "bufferHours": buffer_hours,
        "riskLevel": risk_level,
        "sampleCount": len(series),
        "windowObservedSeconds": window_observed,
        "usedDeltaPct": used_delta_pct,
    }


def compute_quota_predictions(
    payload: dict[str, Any],
    history_samples: list[dict[str, Any]],
    *,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Attach burn-rate predictions to a deep copy of payload without mutating input."""
    result = copy.deepcopy(payload)
    if not isinstance(result, dict):
        return result
    groups = result.get("groups")
    if not isinstance(groups, list) or not groups:
        return result

    current_ts = int(time.time()) if now_ts is None else int(now_ts)
    fetched_at = core._parse_int(result.get("fetchedAt"))
    observation_ts = (
        fetched_at
        if fetched_at is not None and 0 < fetched_at <= current_ts
        else current_ts
    )

    normalized_history = _normalize_history_samples(history_samples)
    indexed_history = [
        (sample["capturedAtEpoch"], _build_sample_lookup(sample))
        for sample in normalized_history
        if sample["capturedAtEpoch"] <= observation_ts
    ]

    for grp in groups:
        if not isinstance(grp, dict):
            continue
        grp_name = str(grp.get("name") or "").strip()
        buckets = grp.get("buckets")
        if not grp_name or not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket["prediction"] = _predict_bucket(
                grp_name,
                bucket,
                indexed_history,
                observation_ts=observation_ts,
                current_ts=current_ts,
            )

    return result


def attach_quota_predictions(
    payload: dict[str, Any],
    *,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Load persisted quota history and attach predictions to payload."""
    if not isinstance(payload, dict) or not payload.get("groups"):
        return payload
    history = load_quota_history()
    return compute_quota_predictions(payload, history, now_ts=now_ts)


def maybe_spawn_background_quota_refresh(
    *,
    min_interval_seconds: int = DEFAULT_SPAWN_MIN_INTERVAL_SECONDS,
    now_ts: int | None = None,
) -> bool:
    """Spawn a detached background quota refresh worker if stale and not throttled."""
    if os.environ.get("CONTEXT_GUARD_DISABLE_AUTO_QUOTA", "").strip() == "1":
        return False

    current_ts = int(time.time()) if now_ts is None else int(now_ts)
    interval = max(1, int(min_interval_seconds))

    cached = core.load_quota_cache(ttl_seconds=interval, now_ts=current_ts)
    if not cached.get("stale", True):
        return False

    should_spawn = False

    def _updater(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal should_spawn
        last_spawn = core._parse_int(state.get("lastSpawnAt")) or 0
        if last_spawn > 0 and 0 <= (current_ts - last_spawn) < interval:
            should_spawn = False
            return state
        should_spawn = True
        return {"lastSpawnAt": current_ts}

    core.update_json_locked(
        get_quota_spawn_lock_path(),
        _updater,
        default={"lastSpawnAt": 0},
    )
    if not should_spawn:
        return False

    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--refresh-background"],
            cwd=str(Path(__file__).resolve().parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return False
    return True


def _find_5h_bucket(group: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the 5h rolling quota bucket within a normalized quota group."""
    for bucket in group.get("buckets", []):
        if not isinstance(bucket, dict):
            continue
        win = str(bucket.get("window") or "").strip().lower()
        label = str(bucket.get("label") or "").strip().lower()
        if win == "5h" or label == "5h limit":
            return bucket
    return None


def _dispatch_low_quota_alerts(
    alerts_to_send: list[tuple[str, float, str]],
    config: dict[str, Any],
) -> None:
    """Send low-quota alerts via lens_guard when available (no-op if not installed)."""
    try:
        import lens_guard  # type: ignore[import-not-found]
    except ImportError:
        return
    notify_fn = getattr(lens_guard, "maybe_send_alert", None)
    if not callable(notify_fn):
        return

    for group_name, remaining_pct, reset_time in alerts_to_send:
        reset_hint = f" (resets {reset_time})" if reset_time else ""
        notify_fn(
            kind="low_quota",
            title=f"AGY Lens: Low 5h Quota ({group_name})",
            message=f"{group_name} 5h quota is down to {remaining_pct:.0f}%{reset_hint}.",
            config=config,
        )


def run_background_refresh() -> dict[str, Any]:
    """Fetch live model quota and trigger per-group low-quota alerts once per reset cycle."""
    payload = core.fetch_model_quota()
    if not payload.get("available") or payload.get("status") != "ok":
        return payload

    cfg = core.load_config()
    alerts_cfg = cfg.get("alerts", {})
    threshold_pct = core._parse_int(alerts_cfg.get("notifyOnLowQuotaPct"))
    if threshold_pct is None or threshold_pct <= 0:
        return payload

    candidates: list[tuple[str, float, str, str]] = []
    for grp in payload.get("groups", []):
        if not isinstance(grp, dict):
            continue
        group_name = str(grp.get("name") or "").strip()
        if not group_name:
            continue
        bucket = _find_5h_bucket(grp)
        if bucket is None:
            continue
        remaining_pct = _parse_float(bucket.get("remainingPercent"))
        if remaining_pct is None or remaining_pct > threshold_pct:
            continue
        reset_time = str(bucket.get("resetTime") or "").strip()
        reset_key = reset_time or f"window:{int(payload.get('fetchedAt') or 0) // 18_000}"
        candidates.append((group_name, remaining_pct, reset_time, reset_key))

    if not candidates:
        return payload

    triggered: list[tuple[str, float, str]] = []
    updated_cfg: dict[str, Any] = cfg

    def _update_config(disk_raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal triggered, updated_cfg
        triggered = []
        base = core._normalize_config(disk_raw)
        alerts = base.get("alerts", {})
        live_thresh = core._parse_int(alerts.get("notifyOnLowQuotaPct"))
        if live_thresh is None or live_thresh <= 0:
            updated_cfg = base
            return base

        last_by_group = dict(alerts.get("lastLowQuotaAlertByGroup") or {})
        changed = False
        for group_name, remaining_pct, reset_time, reset_key in candidates:
            if remaining_pct <= live_thresh and last_by_group.get(group_name) != reset_key:
                last_by_group[group_name] = reset_key
                triggered.append((group_name, remaining_pct, reset_time))
                changed = True

        if changed:
            alerts["lastLowQuotaAlertByGroup"] = last_by_group
            base["alerts"] = core._normalize_alerts(alerts)
        updated_cfg = base
        return base

    core.update_json_locked(
        core.get_config_path(),
        _update_config,
        default=core._default_config(),
    )
    if triggered:
        _dispatch_low_quota_alerts(triggered, updated_cfg)

    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for detached background quota refresh."""
    parser = argparse.ArgumentParser(description="AGY Lens quota worker")
    parser.add_argument(
        "--refresh-background",
        action="store_true",
        help="Fetch live quota, update history, and evaluate low-quota alerts.",
    )
    args = parser.parse_args(argv)
    if args.refresh_background:
        run_background_refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
