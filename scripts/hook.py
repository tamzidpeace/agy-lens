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

BYPASS_PREFIXES: tuple[str, ...] = ("/compact", "/btw", "/rewind", "/new", "/clear")
_SUPPORTED_EVENTS = frozenset({"SessionStart", "PreInvocation", "PostInvocation", "Stop"})


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
        f"[Context Guard] Context at {effective:,} / {threshold:,} tokens ({pct}%) "
        "— approaching threshold. Consider /compact or the Context Guard side pane."
    )
    markdown = (
        f"**Context Guard Warning:** Context usage reached **{effective:,} / {threshold:,} tokens** "
        f"({pct}%). Consider `/compact` or open the **Context Guard** side pane for bloat inspection and handoff."
    )
    return {"injectSteps": [_system_message_step(single_line, markdown)]}


def _build_limit_response(status: dict[str, Any], *, terminate: bool) -> dict[str, Any]:
    """Build concise limit alert payload, optionally terminating the current turn."""
    effective = status["effectiveTokens"]
    threshold = status["effectiveThresholdTokens"]
    pct = status["thresholdPercent"]
    action_note = (
        "Pausing turn — run /compact, snooze, or use Smart Handoff in the Context Guard side pane."
        if terminate
        else "Run /compact, snooze, or use Smart Handoff in the Context Guard side pane."
    )
    single_line = (
        f"[Context Guard] Context limit reached ({effective:,} / {threshold:,} tokens, {pct}%). "
        f"{action_note}"
    )
    markdown = (
        f"**Context Guard Limit Reached:** Context usage is **{effective:,} / {threshold:,} tokens** "
        f"({pct}%). Run `/compact`, snooze alerts, or start a **Smart Handoff** from the **Context Guard** side pane."
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
        f"Context Guard (gate mode) paused this turn because context reached "
        f"{effective:,} / {threshold:,} tokens ({pct}%). "
        "Run /compact, /new, or open the Context Guard side pane to snooze or start a Smart Handoff."
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
) -> dict[str, Any]:
    """Apply warn/stop/gate threshold rules with per-compaction-epoch deduplication."""
    if status.get("status") in ("disabled", "no_data"):
        return {}

    effective_tokens = status["effectiveTokens"]
    effective_threshold = status["effectiveThresholdTokens"]
    warn_threshold = status["warnThresholdTokens"]
    compaction_count = status["compactionCount"]
    mode = status["mode"]

    warned_epoch = conv_state["warnedAtEpoch"] if conv_state is not None else -1
    alerted_epoch = conv_state["alertedAtEpoch"] if conv_state is not None else -1

    if effective_tokens >= effective_threshold:
        if event == "PreInvocation" and mode == "gate":
            if conv_state is not None:
                conv_state["warnedAtEpoch"] = max(warned_epoch, compaction_count)
                conv_state["alertedAtEpoch"] = max(alerted_epoch, compaction_count)
            return _build_gate_block_response(status)

        if alerted_epoch < compaction_count:
            if conv_state is not None:
                conv_state["warnedAtEpoch"] = max(warned_epoch, compaction_count)
                conv_state["alertedAtEpoch"] = compaction_count
            should_terminate = event == "PostInvocation" and mode in ("stop", "gate")
            return _build_limit_response(status, terminate=should_terminate)
        return {}

    if effective_tokens >= warn_threshold and warned_epoch < compaction_count:
        if conv_state is not None:
            conv_state["warnedAtEpoch"] = compaction_count
        return _build_warn_response(status)

    return {}


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
    if event in ("PreInvocation", "PostInvocation"):
        if status.get("enabled") and not is_bypass_command(last_user_input):
            response = _evaluate_invocation(event, status, conv_state)

    if conv_state is not None:
        core.save_config(config)
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
