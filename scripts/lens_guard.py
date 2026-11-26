#!/usr/bin/env python3
"""Stuck-loop detector and cross-platform smart alerts for Agy Lens.

Tracks per-conversation tool failure/repeat streaks from PostToolUse hooks,
injects loop-breaking nudges or recoverable auto-pause steps on PreInvocation /
PostInvocation, and triggers non-blocking sound/desktop notifications.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core as core

MAX_LOOP_HISTORY = 10
_FILE_TOOL_NAMES = frozenset(
    {
        "edit_file",
        "write_file",
        "read_file",
        "view_file",
        "replace_file_content",
        "multi_replace_file_content",
        "view_file_outline",
        "view_code_item",
        "create_file",
        "patch_file",
    }
)
_FILE_PATH_KEYS = (
    "TargetFile",
    "target_file",
    "targetFile",
    "Path",
    "path",
    "AbsolutePath",
    "absolute_path",
    "FilePath",
    "file_path",
    "File",
    "file",
)
_CMD_KEYS = ("CommandLine", "command_line", "commandLine", "command", "Command", "cmd")


def _parse_args_dict(raw_args: Any) -> dict[str, Any]:
    """Decode toolCall.args defensively whether provided as dict or JSON string."""
    unwrapped = core._unwrap_json_value(raw_args)
    if isinstance(unwrapped, dict):
        return unwrapped
    return {}


def _normalize_tool_name(raw_name: Any) -> str:
    """Normalize Antigravity tool step names into concise lowercase names."""
    name = str(raw_name or "").strip()
    if name.upper().startswith("CORTEX_STEP_TYPE_"):
        name = name[len("CORTEX_STEP_TYPE_") :]
    return name.lower() or "unknown_tool"


def _detect_failure(payload: dict[str, Any]) -> tuple[bool, str]:
    """Inspect hook payload for explicit error string or non-zero exit code."""
    raw_error = payload.get("error")
    err_text = str(raw_error).strip() if raw_error is not None else ""
    if err_text:
        return (True, core._sanitize_snippet(err_text, 120))

    for key in ("exitCode", "exit_code"):
        val = payload.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val != 0:
            return (True, f"exit status {val}")
    return (False, "")


def extract_tool_signature(payload: dict[str, Any]) -> tuple[str, str, str, bool, str]:
    """Extract normalized signature, tool name, args digest, failure flag, and error snippet."""
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        tool_call = {}

    tool_name = _normalize_tool_name(tool_call.get("name"))
    args = _parse_args_dict(tool_call.get("args"))

    sig = ""
    if tool_name == "run_command":
        cmd_text = ""
        for k in _CMD_KEYS:
            if args.get(k):
                cmd_text = str(args[k])
                break
        collapsed = core._WHITESPACE_RE.sub(" ", cmd_text).strip()[:100]
        sig = f"cmd:{collapsed}" if collapsed else "cmd:<empty>"
    elif tool_name in _FILE_TOOL_NAMES:
        target = ""
        for k in _FILE_PATH_KEYS:
            if args.get(k):
                target = str(args[k]).strip()
                break
        sig = f"file:{target}" if target else f"file:<{tool_name}>"
    else:
        top_keys = ",".join(sorted(str(k) for k in args.keys())[:2])
        sig = f"tool:{tool_name}:{top_keys}" if top_keys else f"tool:{tool_name}"

    try:
        canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=True, default=str)
    except TypeError:
        canonical_args = str(args)
    args_digest = hashlib.sha1(canonical_args.encode("utf-8")).hexdigest()[:12]

    failed, error_snippet = _detect_failure(payload)
    return (sig, tool_name, args_digest, failed, error_snippet)


def record_tool_use(
    conv_state: dict[str, Any],
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    now_ts: int | None = None,
) -> dict[str, Any] | None:
    """Append tool call to loopHistory and return updated activeLoop if a streak triggers."""
    now = int(time.time()) if now_ts is None else int(now_ts)
    sig, tool_name, args_digest, failed, error_snippet = extract_tool_signature(payload)

    history = conv_state.get("loopHistory")
    if not isinstance(history, list):
        history = []
        conv_state["loopHistory"] = history

    history.append(
        {
            "sig": sig,
            "name": tool_name,
            "argsDigest": args_digest,
            "failed": failed,
            "err": error_snippet,
            "stepIdx": core._safe_int(payload.get("stepIdx"), 0),
            "ts": now,
        }
    )
    if len(history) > MAX_LOOP_HISTORY:
        del history[: len(history) - MAX_LOOP_HISTORY]

    raw_lg = config.get("loopGuard")
    lg = raw_lg if isinstance(raw_lg, dict) else core.DEFAULT_LOOP_GUARD
    if not bool(lg.get("enabled", True)):
        conv_state["activeLoop"] = None
        return None

    existing_active = conv_state.get("activeLoop")
    if not failed and isinstance(existing_active, dict) and existing_active.get("signature") != sig:
        conv_state["activeLoop"] = None
        return None

    fail_streak = 0
    for item in reversed(history):
        if not isinstance(item, dict) or not item.get("failed") or item.get("sig") != sig:
            break
        fail_streak += 1

    repeat_streak = 0
    for item in reversed(history):
        if (
            not isinstance(item, dict)
            or item.get("sig") != sig
            or item.get("argsDigest") != args_digest
        ):
            break
        repeat_streak += 1

    threshold = max(
        core.MIN_LOOP_CONSECUTIVE_THRESHOLD,
        core._safe_int(lg.get("consecutiveThreshold"), 3),
    )

    reason = ""
    count = 0
    if fail_streak >= threshold:
        reason = "consecutive_failures"
        count = fail_streak
    elif repeat_streak >= threshold + 1:
        reason = "identical_repeats"
        count = repeat_streak

    if not reason:
        return None

    prior = existing_active if isinstance(existing_active, dict) and existing_active.get("signature") == sig else {}
    active = {
        "signature": sig,
        "toolName": tool_name,
        "count": count,
        "reason": reason,
        "lastErrorSnippet": error_snippet or str(prior.get("lastErrorSnippet") or ""),
        "detectedAt": core._safe_int(prior.get("detectedAt"), 0) or now,
        "nudgedAtCount": core._safe_int(prior.get("nudgedAtCount"), 0),
        "pausedAtCount": core._safe_int(prior.get("pausedAtCount"), 0),
        "alertedAtCount": core._safe_int(prior.get("alertedAtCount"), 0),
        "pausedOnInput": str(prior.get("pausedOnInput") or ""),
    }
    conv_state["activeLoop"] = active
    return active


def clear_active_loop(conv_id: str | None = None) -> dict[str, Any]:
    """Clear activeLoop and loopHistory for the target (or active) conversation safely."""
    path = core.get_config_path()
    result_id: str = ""

    def _clear(disk_raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal result_id
        cfg = core._normalize_config(disk_raw) if disk_raw else core.load_config()
        target = (
            conv_id.strip()
            if isinstance(conv_id, str) and conv_id.strip()
            else str(cfg.get("lastActiveConversationId") or "").strip()
        )
        result_id = target
        if target:
            convs = cfg.get("conversations")
            if isinstance(convs, dict) and target in convs and isinstance(convs[target], dict):
                convs[target]["activeLoop"] = None
                convs[target]["loopHistory"] = []
        return cfg

    core.update_json_locked(path, _clear, default=None)
    return {"ok": True, "convId": result_id}


def evaluate_loop_hook(
    event: str,
    conv_state: dict[str, Any],
    payload: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Evaluate PreInvocation/PostInvocation for an active loop streak.

    Returns ``(hook_response, newly_alertable)``. Automatically unlocks any
    prior auto-pause when the user sends a new/different ``lastUserInput``.
    """
    active = conv_state.get("activeLoop")
    if not isinstance(active, dict):
        return ({}, False)

    raw_lg = config.get("loopGuard")
    lg = raw_lg if isinstance(raw_lg, dict) else core.DEFAULT_LOOP_GUARD
    if not bool(lg.get("enabled", True)):
        return ({}, False)

    last_user_input = str(payload.get("lastUserInput") or "")
    paused_at = core._safe_int(active.get("pausedAtCount"), 0)
    if paused_at > 0 and last_user_input != str(active.get("pausedOnInput") or ""):
        conv_state["activeLoop"] = None
        conv_state["loopHistory"] = []
        return ({}, False)

    count = max(1, core._safe_int(active.get("count"), 1))
    tool_name = str(active.get("toolName") or "tool")
    sig = str(active.get("signature") or tool_name)
    reason = str(active.get("reason") or "consecutive_failures")
    reason_label = "consecutive failures" if reason == "consecutive_failures" else "identical repeats"
    err_hint = str(active.get("lastErrorSnippet") or "").strip()

    alerted_at = core._safe_int(active.get("alertedAtCount"), 0)
    newly_alertable = count > alerted_at

    auto_pause = core._safe_int(lg.get("autoPauseThreshold"), 5)
    if event == "PreInvocation" and auto_pause > 0 and count >= auto_pause and paused_at < count:
        active["pausedAtCount"] = count
        active["pausedOnInput"] = last_user_input
        active["alertedAtCount"] = max(alerted_at, count)
        detail = f" ({err_hint})" if err_hint else ""
        msg = (
            f"Agy Lens Loop Guard paused this turn after {count} {reason_label} on "
            f"{tool_name} [{sig}]{detail}. Send a new instruction, run /compact or /new, "
            "or click Dismiss in the Agy Lens side pane."
        )
        return (
            {
                "injectSteps": [{"errorMessage": {"userMessage": msg}}],
                "terminationBehavior": "terminate",
            },
            newly_alertable,
        )

    nudge_once = bool(lg.get("nudgeOncePerStreak", True))
    nudged_at = core._safe_int(active.get("nudgedAtCount"), 0)
    if count > nudged_at or not nudge_once:
        active["nudgedAtCount"] = count
        active["alertedAtCount"] = max(alerted_at, count)
        detail = f" — last error: {err_hint}" if err_hint else ""
        single_line = (
            f"[Agy Lens Loop Guard] Detected {count} {reason_label} on {tool_name} ({sig}){detail}. "
            "Step back, inspect the root cause, and try a different approach."
        )
        markdown = (
            f"**Agy Lens Loop Guard:** Detected **{count} {reason_label}** on `{tool_name}` "
            f"(`{sig}`){detail}. Pausing repetition — reconsider root cause before retrying."
        )
        return (
            {
                "injectSteps": [
                    {
                        "systemMessage": {
                            "systemMessage": single_line,
                            "metadata": {"user_markdown": markdown},
                        }
                    }
                ]
            },
            newly_alertable,
        )

    return ({}, False)


def _sanitize_osascript_text(text: str) -> str:
    """Escape backslashes/quotes and strip control chars for safe AppleScript string literals."""
    cleaned = "".join(ch if ch >= " " else " " for ch in str(text))
    cleaned = core._WHITESPACE_RE.sub(" ", cleaned).strip()[:200]
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')


def _spawn_detached(argv: list[str]) -> bool:
    """Launch a fire-and-forget OS helper without blocking hooks or raising."""
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def maybe_send_alert(
    kind: str,
    title: str,
    message: str,
    config: dict[str, Any],
    *,
    force: bool = False,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Emit non-blocking desktop sound/notification honoring cooldown and test-disable env."""
    if os.environ.get("AGY_LENS_DISABLE_SYSTEM_ALERTS", "").strip() == "1":
        return {"sent": False, "soundTriggered": False, "notificationTriggered": False, "kind": kind, "reason": "disabled_by_env"}

    now = int(time.time()) if now_ts is None else int(now_ts)
    alerts = config.get("alerts")
    if not isinstance(alerts, dict):
        alerts = core._normalize_alerts(None)
        config["alerts"] = alerts

    last_by_kind = alerts.setdefault("lastAlertAtByKind", {})
    if not isinstance(last_by_kind, dict):
        last_by_kind = {}
        alerts["lastAlertAtByKind"] = last_by_kind

    cooldown = max(0, core._safe_int(alerts.get("cooldownSeconds"), 15))
    prev_ts = core._safe_int(last_by_kind.get(kind), 0)
    if not force and cooldown > 0 and prev_ts > 0 and (now - prev_ts) < cooldown:
        return {"sent": False, "soundTriggered": False, "notificationTriggered": False, "kind": kind, "reason": "cooldown"}

    sound_enabled = force or bool(alerts.get("soundEnabled", True))
    notif_enabled = force or bool(alerts.get("desktopNotificationEnabled", True))
    if not sound_enabled and not notif_enabled:
        return {"sent": False, "soundTriggered": False, "notificationTriggered": False, "kind": kind, "reason": "disabled"}

    sound_triggered = False
    notif_triggered = False

    if sys.platform == "darwin":
        if sound_enabled:
            preferred = (
                "/System/Library/Sounds/Basso.aiff"
                if kind in ("loop", "threshold", "quota", "error")
                else "/System/Library/Sounds/Glass.aiff"
            )
            sound_file = preferred if Path(preferred).is_file() else "/System/Library/Sounds/Glass.aiff"
            sound_triggered = _spawn_detached(["afplay", sound_file])
        if notif_enabled:
            safe_title = _sanitize_osascript_text(title)
            safe_msg = _sanitize_osascript_text(message)
            script = f'display notification "{safe_msg}" with title "{safe_title}"'
            notif_triggered = _spawn_detached(["osascript", "-e", script])
    else:
        if notif_enabled:
            notify_bin = shutil.which("notify-send")
            if notify_bin:
                notif_triggered = _spawn_detached([notify_bin, title[:120], message[:240]])

    sent = sound_triggered or notif_triggered
    if sent or not force:
        last_by_kind[kind] = now
    return {
        "sent": sent,
        "soundTriggered": sound_triggered,
        "notificationTriggered": notif_triggered,
        "kind": kind,
    }
