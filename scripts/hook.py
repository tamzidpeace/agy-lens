#!/usr/bin/env python3
"""Lifecycle hook entrypoint for the context-guard Antigravity plugin.

Handles SessionStart, PreInvocation, PostInvocation, and Stop hook events.
Always fails open (exits 0 with valid JSON) and never blocks safe slash commands.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core as core
import lens_guard
import lens_quota

BYPASS_PREFIXES: tuple[str, ...] = ("/compact", "/btw", "/rewind", "/new", "/clear")
_SUPPORTED_EVENTS = frozenset({"SessionStart", "PreInvocation", "PostInvocation", "PostToolUse", "Stop"})


def is_bypass_command(last_user_input: str) -> bool:
    """Return True if the first whitespace-separated token is a safe slash command."""
    if not isinstance(last_user_input, str):
        return False
    stripped = last_user_input.strip().lower()
    if not stripped:
        return False
    first_token = stripped.split(None, 1)[0]
    return first_token in BYPASS_PREFIXES


def _utc_now_iso() -> str:
    """Return current UTC timestamp formatted as ISO-8601 with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _infer_conv_id(transcript_path: str | None) -> str | None:
    """Extract conversationId from .../<root>/brain/<convId>/.system_generated/logs/transcript.jsonl."""
    if not transcript_path:
        return None
    try:
        parts = Path(transcript_path).parts
    except (OSError, ValueError):
        return None
    if (
        len(parts) >= 5
        and parts[-1] == "transcript.jsonl"
        and parts[-2] == "logs"
        and parts[-3] == ".system_generated"
        and parts[-5] == "brain"
    ):
        return parts[-4] or None
    return None


def _system_message_step(single_line: str, markdown: str) -> dict[str, Any]:
    """Construct an Antigravity injectSteps systemMessage entry."""
    return {
        "systemMessage": {
            "systemMessage": single_line,
            "metadata": {"user_markdown": markdown},
        }
    }


def _error_message_step(user_message: str) -> dict[str, Any]:
    """Construct an Antigravity injectSteps errorMessage entry."""
    return {
        "errorMessage": {
            "userMessage": user_message,
        }
    }


def _build_warn_response(status: dict[str, Any]) -> dict[str, Any]:
    """Build concise warning payload when crossing warnThresholdTokens."""
    effective = status["effectiveTokens"]
    threshold = status["effectiveThresholdTokens"]
    pct = status["thresholdPercent"]
    single_line = (
        f"[Agy Lens] Context at {effective:,} / {threshold:,} tokens ({pct}%) "
        "— approaching threshold. Consider /compact or the Agy Lens side pane."
    )
    markdown = (
        f"**Agy Lens Warning:** Context usage reached **{effective:,} / {threshold:,} tokens** "
        f"({pct}%). Consider `/compact` or open the **Agy Lens** side pane for bloat inspection and handoff."
    )
    return {"injectSteps": [_system_message_step(single_line, markdown)]}


def _build_limit_response(status: dict[str, Any], *, terminate: bool) -> dict[str, Any]:
    """Build concise limit alert payload, optionally terminating the current turn."""
    effective = status["effectiveTokens"]
    threshold = status["effectiveThresholdTokens"]
    pct = status["thresholdPercent"]
    action_note = (
        "Pausing turn — run /compact, snooze, or use Smart Handoff in the Agy Lens side pane."
        if terminate
        else "Run /compact, snooze, or use Smart Handoff in the Agy Lens side pane."
    )
    single_line = (
        f"[Agy Lens] Context limit reached ({effective:,} / {threshold:,} tokens, {pct}%). "
        f"{action_note}"
    )
    markdown = (
        f"**Agy Lens Limit Reached:** Context usage is **{effective:,} / {threshold:,} tokens** "
        f"({pct}%). Run `/compact`, snooze alerts, or start a **Smart Handoff** from the **Agy Lens** side pane."
    )
    response: dict[str, Any] = {"injectSteps": [_system_message_step(single_line, markdown)]}
    if terminate:
        response["terminationBehavior"] = "terminate"
    return response


def _build_gate_block_response(status: dict[str, Any]) -> dict[str, Any]:
    """Build hard-block gate payload requiring /compact, bypass, or snooze."""
    effective = status["effectiveTokens"]
    threshold = status["effectiveThresholdTokens"]
    pct = status["thresholdPercent"]
    user_message = (
        f"Agy Lens (gate mode) paused this turn because context reached "
        f"{effective:,} / {threshold:,} tokens ({pct}%). "
        "Run /compact, /new, or open the Agy Lens side pane to snooze or start a Smart Handoff."
    )
    return {
        "injectSteps": [_error_message_step(user_message)],
        "terminationBehavior": "terminate",
    }


def _sync_conversation_state(
    config: dict[str, Any],
    *,
    conv_id: str | None,
    explicit_transcript: str | None,
    payload_model: str,
    status: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Update discovery metadata and latest metrics for the active conversation."""
    target_conv_id = (
        conv_id
        or (status.get("conversationId") if status is not None else None)
        or _infer_conv_id(explicit_transcript)
    )
    if not isinstance(target_conv_id, str) or not target_conv_id:
        return None

    config["lastActiveConversationId"] = target_conv_id
    conv_state = core.get_conversation_state(config, target_conv_id)

    if explicit_transcript:
        conv_state["transcriptPath"] = explicit_transcript
    elif status is not None and status.get("transcriptPath"):
        conv_state["transcriptPath"] = status["transcriptPath"]
    elif not conv_state.get("transcriptPath"):
        discovered = core.find_transcript_path(conv_id=target_conv_id, config=config)
        if discovered is not None:
            conv_state["transcriptPath"] = str(discovered)

    if status is not None and status.get("stepCount", 0) > 0:
        conv_state["lastTokens"] = status["effectiveTokens"]
        conv_state["lastCompactionIndex"] = status["activeEpochStep"]
        compaction_count = max(0, int(status.get("compactionCount", 0)))
        if conv_state.get("warnedAtEpoch", -1) > compaction_count:
            conv_state["warnedAtEpoch"] = -1
        if conv_state.get("alertedAtEpoch", -1) > compaction_count:
            conv_state["alertedAtEpoch"] = -1
        resolved_model = status.get("model") or payload_model
        if resolved_model:
            conv_state["lastModel"] = resolved_model
    elif payload_model:
        conv_state["lastModel"] = payload_model

    conv_state["updatedAt"] = _utc_now_iso()
    return conv_state


def _evaluate_invocation(
    event: str,
    status: dict[str, Any],
    conv_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Apply warn/stop/gate threshold rules with per-compaction-epoch deduplication.

    Returns ``(hook_response, crossed_limit_now)`` where ``crossed_limit_now``
    is True only on the first limit crossing for the active compaction epoch.
    """
    if status.get("status") in ("disabled", "no_data"):
        return ({}, False)

    effective_tokens = status["effectiveTokens"]
    effective_threshold = status["effectiveThresholdTokens"]
    warn_threshold = status["warnThresholdTokens"]
    compaction_count = status["compactionCount"]
    mode = status["mode"]

    warned_epoch = conv_state["warnedAtEpoch"] if conv_state is not None else -1
    alerted_epoch = conv_state["alertedAtEpoch"] if conv_state is not None else -1

    if effective_tokens >= effective_threshold:
        if event == "PreInvocation" and mode == "gate":
            first_cross = alerted_epoch < compaction_count
            if conv_state is not None:
                conv_state["warnedAtEpoch"] = max(warned_epoch, compaction_count)
                conv_state["alertedAtEpoch"] = max(alerted_epoch, compaction_count)
            return (_build_gate_block_response(status), first_cross)

        if alerted_epoch < compaction_count:
            if conv_state is not None:
                conv_state["warnedAtEpoch"] = max(warned_epoch, compaction_count)
                conv_state["alertedAtEpoch"] = compaction_count
            should_terminate = event == "PostInvocation" and mode in ("stop", "gate")
            return (_build_limit_response(status, terminate=should_terminate), True)
        return ({}, False)

    if effective_tokens >= warn_threshold and warned_epoch < compaction_count:
        if conv_state is not None:
            conv_state["warnedAtEpoch"] = compaction_count
        return (_build_warn_response(status), False)

    return ({}, False)


def _merge_hook_responses(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Merge context threshold and loop-guard hook responses cleanly."""
    if not primary:
        return dict(secondary) if secondary else {}
    if not secondary:
        return dict(primary)

    steps: list[Any] = []
    if isinstance(primary.get("injectSteps"), list):
        steps.extend(primary["injectSteps"])
    if isinstance(secondary.get("injectSteps"), list):
        steps.extend(secondary["injectSteps"])

    merged: dict[str, Any] = {}
    if steps:
        merged["injectSteps"] = steps

    term = primary.get("terminationBehavior") or secondary.get("terminationBehavior")
    if isinstance(term, str) and term:
        merged["terminationBehavior"] = term
    return merged


def handle_hook(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process an Antigravity lifecycle hook event and return hook response JSON."""
    if not isinstance(payload, dict):
        return {}

    raw_event = event_name or payload.get("hookEventName") or ""
    event = str(raw_event).strip()
    if event not in _SUPPORTED_EVENTS:
        return {}

    raw_conv_id = payload.get("conversationId")
    conv_id = raw_conv_id.strip() if isinstance(raw_conv_id, str) and raw_conv_id.strip() else None

    raw_transcript = payload.get("transcriptPath")
    explicit_transcript = (
        os.path.expanduser(raw_transcript.strip())
        if isinstance(raw_transcript, str) and raw_transcript.strip()
        else None
    )

    raw_last_input = payload.get("lastUserInput")
    last_user_input = raw_last_input if isinstance(raw_last_input, str) else ""

    raw_model = payload.get("modelName")
    payload_model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else ""

    config = core.load_config()

    if event == "SessionStart":
        conv_state = _sync_conversation_state(
            config,
            conv_id=conv_id,
            explicit_transcript=explicit_transcript,
            payload_model=payload_model,
            status=None,
        )
        if conv_state is not None:
            core.save_config(config)
        return {}

    if event == "PostToolUse":
        conv_state = _sync_conversation_state(
            config,
            conv_id=conv_id,
            explicit_transcript=explicit_transcript,
            payload_model=payload_model,
            status=None,
        )
        if conv_state is not None:
            lens_guard.record_tool_use(conv_state, payload, config)
            core.save_config(config)
        return {}

    status = core.build_status_payload(
        conv_id=conv_id,
        explicit_path=explicit_transcript,
        config=config,
    )
    conv_state = _sync_conversation_state(
        config,
        conv_id=conv_id,
        explicit_transcript=explicit_transcript,
        payload_model=payload_model,
        status=status,
    )

    response: dict[str, Any] = {}
    alerts_cfg = config.get("alerts") if isinstance(config.get("alerts"), dict) else {}

    if event in ("PreInvocation", "PostInvocation"):
        bypass = is_bypass_command(last_user_input)
        if bypass and conv_state is not None:
            conv_state["activeLoop"] = None
            conv_state["loopHistory"] = []

        ctx_resp: dict[str, Any] = {}
        crossed_limit = False
        if status.get("enabled") and not bypass:
            ctx_resp, crossed_limit = _evaluate_invocation(event, status, conv_state)

        loop_resp: dict[str, Any] = {}
        newly_loop_alert = False
        if conv_state is not None and not bypass:
            loop_resp, newly_loop_alert = lens_guard.evaluate_loop_hook(
                event, conv_state, payload, config
            )

        response = _merge_hook_responses(ctx_resp, loop_resp)

        if crossed_limit and alerts_cfg.get("notifyOnThreshold", True):
            lens_guard.maybe_send_alert(
                "threshold",
                "Agy Lens: Context Limit",
                f"Context reached {status['effectiveTokens']:,} / {status['effectiveThresholdTokens']:,} tokens ({status['thresholdPercent']}%).",
                config,
            )
        if newly_loop_alert and alerts_cfg.get("notifyOnLoop", True):
            active_loop = conv_state.get("activeLoop") if conv_state is not None else None
            tool_name = active_loop.get("toolName", "tool") if isinstance(active_loop, dict) else "tool"
            count = active_loop.get("count", 0) if isinstance(active_loop, dict) else 0
            lens_guard.maybe_send_alert(
                "loop",
                "Agy Lens: Loop Guard",
                f"Detected {count} consecutive repeats/failures on {tool_name}.",
                config,
            )

    elif event == "Stop":
        term_reason = str(payload.get("terminationReason") or "").strip().upper()
        fully_idle = payload.get("fullyIdle", True) is not False
        exec_num = core._safe_int(payload.get("executionNum"), 1)
        should_notify_stop = alerts_cfg.get("notifyOnStop", True) and (
            term_reason == "ERROR"
            or (fully_idle and exec_num >= 1 and term_reason != "USER_CANCELED")
        )
        if should_notify_stop:
            alert_msg = (
                core._sanitize_snippet(str(payload.get("error") or "Run stopped with error."), 140)
                if term_reason == "ERROR"
                else "Agent turn finished."
            )
            alert_kind = "error" if term_reason == "ERROR" else "stop"
            lens_guard.maybe_send_alert(alert_kind, "Agy Lens", alert_msg, config)

    if conv_state is not None:
        core.save_config(config)

    if event == "Stop":
        try:
            lens_quota.maybe_spawn_background_quota_refresh(min_interval_seconds=300)
        except Exception:
            pass

    return response


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: read JSON payload from stdin, emit JSON to stdout, always exit 0."""
    args = sys.argv[1:] if argv is None else argv
    event_name = args[0] if args else ""
    output = "{}"
    try:
        raw_stdin = sys.stdin.read()
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
        if isinstance(payload, dict):
            result = handle_hook(event_name, payload)
            if isinstance(result, dict):
                output = json.dumps(result)
    except Exception:
        output = "{}"
    try:
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
