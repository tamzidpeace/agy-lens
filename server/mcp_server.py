#!/usr/bin/env python3
"""Zero-dependency Python stdio MCP server and MCP App host for context-guard.

Implements newline-delimited JSON-RPC 2.0 over stdin/stdout (Python 3.9+ stdlib
only), exposes Context Guard tools and the single-file dashboard HTML resource,
and resolves target conversations across tool arguments, request _meta,
environment variables, config state, and recent transcripts.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, TextIO

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core
import lens_footprint
import lens_guard
import lens_search

APP_URI = "ui://context-guard/dashboard.html"
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO: dict[str, str] = {
    "name": "agy-lens-monitor",
    "version": "2.0.0-beta.1",
}
DEFAULT_SNOOZE_DELTA_TOKENS = 50_000
DEFAULT_OVERVIEW_LIMIT = 20
MAX_OVERVIEW_LIMIT = 100
DEFAULT_APP_HTML_PATH = Path(__file__).resolve().parent.parent / "ui" / "app.html"

_FALLBACK_HTML = (
    "<!doctype html>"
    "<html lang=\"en\">"
    "<head><meta charset=\"utf-8\"><title>Agy Lens</title></head>"
    "<body>"
    "<h1>Agy Lens Dashboard</h1>"
    "<p>Dashboard UI bundle (ui/app.html) has not been built yet.</p>"
    "</body>"
    "</html>"
)

_META_RESOURCE_UI: dict[str, Any] = {"ui": {"prefersBorder": False}}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "context_status",
        "description": (
            "Inspect live context window token usage, threshold progress, turn history, "
            "and bloat attribution for a conversation, and open the Context Guard side pane."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {
                    "type": "string",
                    "description": "Optional conversation ID; defaults to the active or most recent conversation.",
                },
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "resourceUri": APP_URI,
                "visibility": ["model", "app"],
            },
            "com.google.antigravity/ui": {
                "entrypoints": [{"type": "aux_pane", "title": "Agy Lens"}],
            },
        },
    },
    {
        "name": "context_overview",
        "description": (
            "List recent conversations across Antigravity App, CLI, and IDE with live "
            "context token usage, threshold percentage, and guard status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_OVERVIEW_LIMIT,
                    "description": "Maximum number of recent conversations to return (default 20).",
                },
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "resourceUri": APP_URI,
                "visibility": ["model", "app"],
            },
            "com.google.antigravity/ui": {
                "entrypoints": [{"type": "global", "title": "Agy Lens"}],
            },
        },
    },
    {
        "name": "context_configure",
        "description": (
            "Update global Context Guard settings (thresholdTokens, warnPercent, mode, enabled) "
            "or per-conversation custom threshold and snooze state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thresholdTokens": {
                    "type": "integer",
                    "minimum": context_core.MIN_THRESHOLD_TOKENS,
                    "description": "Global context alert threshold in tokens.",
                },
                "warnPercent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 99,
                    "description": "Percentage of effective threshold that triggers early warnings.",
                },
                "mode": {
                    "type": "string",
                    "enum": list(context_core.VALID_MODES),
                    "description": "Enforcement mode when reaching threshold ('warn', 'stop', or 'gate').",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable or disable Context Guard lifecycle hooks.",
                },
                "autoRefreshSeconds": {
                    "type": "integer",
                    "minimum": context_core.MIN_AUTO_REFRESH_SECONDS,
                    "maximum": context_core.MAX_AUTO_REFRESH_SECONDS,
                    "description": "Dashboard auto-refresh polling interval in seconds (default 5, range 1-300).",
                },
                "conversationId": {
                    "type": "string",
                    "description": "Target conversation ID for per-conversation threshold or snooze changes.",
                },
                "customThresholdTokens": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Per-conversation threshold override in tokens (0 clears override).",
                },
                "snoozeTokens": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Snooze alerts for this many additional tokens above current usage/threshold.",
                },
                "resetSnooze": {
                    "type": "boolean",
                    "description": "Clear any active snooze on the target conversation.",
                },
                "loopGuard": {
                    "type": "object",
                    "description": "Stuck-loop detector settings patch (enabled, consecutiveThreshold, nudgeOncePerStreak, autoPauseThreshold).",
                },
                "alerts": {
                    "type": "object",
                    "description": "Smart sound & desktop notification settings patch.",
                },
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["model", "app"],
            },
        },
    },
    {
        "name": "context_handoff",
        "description": (
            "Generate a structured continuation brief summarizing the original goal, recent "
            "requests, files touched, commands run, and context stats for a fresh conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {
                    "type": "string",
                    "description": "Target conversation ID; defaults to the active or most recent conversation.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional extra instructions or context to include in the handoff brief.",
                },
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["model", "app"],
            },
        },
    },
    {
        "name": "lens_search",
        "description": (
            "Full-text search across all past Antigravity conversation transcripts, "
            "artifacts, and edited file paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms or quoted phrases (empty lists recent chats)."},
                "scope": {"type": "string", "enum": ["all", "messages", "artifacts", "files"]},
                "includeSubagents": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "resourceUri": APP_URI,
                "visibility": ["model", "app"],
            },
        },
    },
    {
        "name": "lens_footprint",
        "description": (
            "Inspect startup context token footprint across global and workspace rules, "
            "plugins, skills, MCP servers, and hooks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspacePath": {"type": "string", "description": "Optional workspace root directory to inspect."},
                "conversationId": {"type": "string", "description": "Optional conversation ID to compare observed startup tokens."},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "resourceUri": APP_URI,
                "visibility": ["model", "app"],
            },
        },
    },
    {
        "name": "app_save_settings",
        "description": "Persist Agy Lens settings from the MCP App UI and return refreshed status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thresholdTokens": {"type": "integer", "minimum": context_core.MIN_THRESHOLD_TOKENS},
                "warnPercent": {"type": "integer", "minimum": 1, "maximum": 99},
                "mode": {"type": "string", "enum": list(context_core.VALID_MODES)},
                "enabled": {"type": "boolean"},
                "autoRefreshSeconds": {
                    "type": "integer",
                    "minimum": context_core.MIN_AUTO_REFRESH_SECONDS,
                    "maximum": context_core.MAX_AUTO_REFRESH_SECONDS,
                },
                "conversationId": {"type": "string"},
                "customThresholdTokens": {"type": "integer", "minimum": 0},
                "resetSnooze": {"type": "boolean"},
                "loopGuard": {"type": "object"},
                "alerts": {"type": "object"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_snooze",
        "description": "Snooze or reset Context Guard threshold alerts for a conversation from the UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {"type": "string"},
                "deltaTokens": {"type": "integer", "minimum": 0},
                "snoozeUntilTokens": {"type": "integer", "minimum": 0},
                "resetSnooze": {"type": "boolean"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_refresh",
        "description": "Refresh live status or overview payload for the MCP App UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": ["status", "overview"]},
                "conversationId": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_OVERVIEW_LIMIT},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_build_handoff",
        "description": "Build a Smart Handoff continuation brief for the MCP App UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_quota",
        "description": "Return cached Antigravity model quota limits and optionally trigger a background refresh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_search",
        "description": "Cross-chat full-text search for the MCP App UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string", "enum": ["all", "messages", "artifacts", "files"]},
                "includeSubagents": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_footprint",
        "description": "Inspect startup customizations token footprint for the MCP App UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspacePath": {"type": "string"},
                "conversationId": {"type": "string"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_clear_loop",
        "description": "Dismiss active Loop Guard alert and reset loop streak for a conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversationId": {"type": "string"},
            },
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
    {
        "name": "app_test_alert",
        "description": "Trigger a one-shot test sound and desktop notification from the UI Settings drawer.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "_meta": {
            "ui": {
                "visibility": ["app"],
            },
        },
    },
]


def _non_empty_str(value: Any) -> str | None:
    """Return stripped string if value is a non-empty string, else None."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def resolve_conversation_id(
    arguments: dict[str, Any],
    request_meta: dict[str, Any],
    config: dict[str, Any],
) -> str | None:
    """Resolve target conversation ID across args, request _meta, env, config, and transcripts."""
    args = arguments if isinstance(arguments, dict) else {}
    meta = request_meta if isinstance(request_meta, dict) else {}
    cfg = config if isinstance(config, dict) else {}

    candidates = (
        args.get("conversationId"),
        meta.get("antigravity.google/conversation_id"),
        meta.get("com.google.antigravity/conversation_id"),
        os.environ.get("ANTIGRAVITY_CONVERSATION_ID"),
        cfg.get("lastActiveConversationId"),
    )
    for candidate in candidates:
        resolved = _non_empty_str(candidate)
        if resolved is not None:
            return resolved

    recent = context_core.list_recent_transcripts(1)
    if recent:
        return _non_empty_str(recent[0].get("conversationId"))
    return None


def load_app_html() -> str:
    """Load dashboard HTML from CONTEXT_GUARD_APP_HTML, ui/app.html, or a clean fallback."""
    override = _non_empty_str(os.environ.get("CONTEXT_GUARD_APP_HTML"))
    if override is not None:
        override_path = Path(os.path.expanduser(override))
        try:
            if override_path.is_file():
                return override_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pass

    try:
        if DEFAULT_APP_HTML_PATH.is_file():
            return DEFAULT_APP_HTML_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass

    return _FALLBACK_HTML


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    """Extract an optional string argument, raising ValueError on non-string values."""
    if key not in arguments or arguments[key] is None:
        return None
    val = arguments[key]
    if not isinstance(val, str):
        raise ValueError(f"'{key}' must be a string")
    return val


def _optional_int(
    arguments: dict[str, Any],
    key: str,
    *,
    min_value: int = 0,
    max_value: int | None = None,
) -> int | None:
    """Extract an optional integer argument (excluding bools) within bounds."""
    if key not in arguments or arguments[key] is None:
        return None
    val = arguments[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"'{key}' must be an integer")
    if val < min_value:
        raise ValueError(f"'{key}' must be >= {min_value}")
    if max_value is not None and val > max_value:
        raise ValueError(f"'{key}' must be <= {max_value}")
    return val


def _optional_bool(arguments: dict[str, Any], key: str) -> bool | None:
    """Extract an optional boolean argument, raising ValueError on non-bool values."""
    if key not in arguments or arguments[key] is None:
        return None
    val = arguments[key]
    if not isinstance(val, bool):
        raise ValueError(f"'{key}' must be a boolean")
    return val


def _optional_dict(arguments: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Extract an optional object argument, raising ValueError on non-dict values."""
    if key not in arguments or arguments[key] is None:
        return None
    val = arguments[key]
    if not isinstance(val, dict):
        raise ValueError(f"'{key}' must be an object")
    return val


def _attach_lens_state(
    payload: dict[str, Any],
    config: dict[str, Any],
    conv_id: str | None,
) -> dict[str, Any]:
    """Attach activeLoop, loopGuard, and public alerts settings to a status payload."""
    target_id = conv_id or payload.get("conversationId")
    convs = config.get("conversations")
    conv_state = (
        convs.get(target_id)
        if isinstance(target_id, str) and isinstance(convs, dict)
        else None
    )
    active_loop = conv_state.get("activeLoop") if isinstance(conv_state, dict) else None
    payload["activeLoop"] = copy.deepcopy(active_loop) if isinstance(active_loop, dict) else None

    lg = config.get("loopGuard")
    payload["loopGuard"] = copy.deepcopy(lg) if isinstance(lg, dict) else dict(context_core.DEFAULT_LOOP_GUARD)

    raw_alerts = config.get("alerts")
    alerts_src = raw_alerts if isinstance(raw_alerts, dict) else context_core.DEFAULT_ALERTS
    payload["alerts"] = {
        "soundEnabled": bool(alerts_src.get("soundEnabled", True)),
        "desktopNotificationEnabled": bool(alerts_src.get("desktopNotificationEnabled", True)),
        "notifyOnStop": bool(alerts_src.get("notifyOnStop", True)),
        "notifyOnThreshold": bool(alerts_src.get("notifyOnThreshold", True)),
        "notifyOnLoop": bool(alerts_src.get("notifyOnLoop", True)),
        "notifyOnLowQuotaPct": context_core._safe_int(alerts_src.get("notifyOnLowQuotaPct"), 15),
        "cooldownSeconds": context_core._safe_int(alerts_src.get("cooldownSeconds"), 15),
    }
    return payload


def _tool_result(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    """Build standard MCP tool response with concise text and structuredContent."""
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
    }


def _tool_error(message: str) -> dict[str, Any]:
    """Build MCP tool error payload with isError=True."""
    return {
        "isError": True,
        "content": [{"type": "text", "text": message}],
    }


def _format_status_summary(status: dict[str, Any]) -> str:
    """Format concise human-readable summary line for a single-conversation status payload."""
    if status.get("status") == "no_data":
        return (
            "Context Guard: No transcript data found yet "
            f"(threshold {status['effectiveThresholdTokens']:,} tokens, mode={status['mode']})."
        )
    conv_label = status.get("conversationId") or "active"
    return (
        f"Context Guard ({conv_label}): "
        f"{status['effectiveTokens']:,} / {status['effectiveThresholdTokens']:,} tokens "
        f"({status['thresholdPercent']}%, status={status['status']}, "
        f"mode={status['mode']}, compactions={status['compactionCount']})."
    )


def _format_overview_summary(overview: dict[str, Any]) -> str:
    """Format concise summary line for a multi-conversation overview payload."""
    convs = overview.get("conversations", [])
    if not convs:
        return (
            "Context Guard Overview: No recent conversations found "
            f"(threshold {overview['thresholdTokens']:,} tokens, mode={overview['mode']})."
        )
    active = overview.get("active") or convs[0]
    return (
        f"Context Guard Overview: {len(convs)} recent conversation(s); "
        f"active={active['conversationId']} at {active['effectiveTokens']:,} / "
        f"{active['thresholdTokens']:,} tokens ({active['thresholdPercent']}%, {active['status']})."
    )


_QUOTA_LOCK = threading.Lock()
_QUOTA_FETCHING = False
_QUOTA_LAST_ATTEMPT_TS = -1e9
_QUOTA_AUTO_COOLDOWN_SECONDS = 30.0


def _run_quota_fetch_worker() -> None:
    """Background worker that refreshes Antigravity model quota without blocking RPC."""
    global _QUOTA_FETCHING
    try:
        context_core.fetch_model_quota()
    except Exception:
        pass
    finally:
        with _QUOTA_LOCK:
            _QUOTA_FETCHING = False


def get_quota_snapshot(*, trigger_if_stale: bool = False, force: bool = False) -> dict[str, Any]:
    """Return cached model quota immediately and optionally spawn one non-blocking refresh."""
    global _QUOTA_FETCHING, _QUOTA_LAST_ATTEMPT_TS
    cache = context_core.load_quota_cache()
    disable_auto = os.environ.get("CONTEXT_GUARD_DISABLE_AUTO_QUOTA") == "1"
    now_mono = time.monotonic()
    should_spawn = False

    with _QUOTA_LOCK:
        if not _QUOTA_FETCHING:
            if force:
                should_spawn = True
            elif (
                trigger_if_stale
                and cache.get("stale")
                and not disable_auto
                and (now_mono - _QUOTA_LAST_ATTEMPT_TS) >= _QUOTA_AUTO_COOLDOWN_SECONDS
            ):
                should_spawn = True

        if should_spawn:
            _QUOTA_FETCHING = True
            _QUOTA_LAST_ATTEMPT_TS = now_mono
            thread = threading.Thread(target=_run_quota_fetch_worker, daemon=True)
            thread.start()

        cache["fetching"] = _QUOTA_FETCHING

    return cache


def _handle_status(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle context_status and status-mode app_refresh tool calls."""
    _optional_str(arguments, "conversationId")
    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    payload = context_core.build_status_payload(conv_id=conv_id, config=config)
    _attach_lens_state(payload, config, conv_id)
    payload["modelQuota"] = get_quota_snapshot(trigger_if_stale=True)
    return _tool_result(_format_status_summary(payload), payload)


def _handle_overview(arguments: dict[str, Any], _request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle context_overview and overview-mode app_refresh tool calls."""
    limit = _optional_int(arguments, "limit", min_value=1, max_value=MAX_OVERVIEW_LIMIT)
    config = context_core.load_config()
    payload = context_core.build_overview_payload(
        config=config,
        limit=limit if limit is not None else DEFAULT_OVERVIEW_LIMIT,
    )
    payload["modelQuota"] = get_quota_snapshot(trigger_if_stale=True)
    return _tool_result(_format_overview_summary(payload), payload)


def _handle_configure(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle context_configure and app_save_settings tool calls."""
    _optional_str(arguments, "conversationId")
    threshold_tokens = _optional_int(arguments, "thresholdTokens", min_value=1)
    warn_percent = _optional_int(arguments, "warnPercent", min_value=1, max_value=99)
    mode = _optional_str(arguments, "mode")
    enabled = _optional_bool(arguments, "enabled")
    auto_refresh_seconds = _optional_int(
        arguments,
        "autoRefreshSeconds",
        min_value=context_core.MIN_AUTO_REFRESH_SECONDS,
        max_value=context_core.MAX_AUTO_REFRESH_SECONDS,
    )
    custom_threshold = _optional_int(arguments, "customThresholdTokens", min_value=0)
    snooze_delta = _optional_int(arguments, "snoozeTokens", min_value=0)
    if snooze_delta is None:
        snooze_delta = _optional_int(arguments, "deltaTokens", min_value=0)
    snooze_until = _optional_int(arguments, "snoozeUntilTokens", min_value=0)
    reset_snooze = _optional_bool(arguments, "resetSnooze")
    loop_guard_patch = _optional_dict(arguments, "loopGuard")
    alerts_patch = _optional_dict(arguments, "alerts")

    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    updated_config = context_core.update_settings(
        config,
        threshold_tokens=threshold_tokens,
        warn_percent=warn_percent,
        mode=mode,
        enabled=enabled,
        auto_refresh_seconds=auto_refresh_seconds,
        conv_id=conv_id,
        custom_threshold_tokens=custom_threshold,
        snooze_delta=snooze_delta,
        snooze_until_tokens=snooze_until,
        reset_snooze=bool(reset_snooze),
        loop_guard_patch=loop_guard_patch,
        alerts_patch=alerts_patch,
    )
    status = context_core.build_status_payload(conv_id=conv_id, config=updated_config)
    _attach_lens_state(status, updated_config, conv_id)
    summary = (
        f"Agy Lens settings saved: enabled={status['enabled']}, "
        f"mode={status['mode']}, effectiveThreshold={status['effectiveThresholdTokens']:,}, "
        f"warnPercent={status['warnPercent']}%, autoRefresh={status['autoRefreshSeconds']}s, "
        f"snoozedUntil={status['snoozedUntilTokens']:,}."
    )
    return _tool_result(summary, status)


def _handle_snooze(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle app_snooze tool calls."""
    _optional_str(arguments, "conversationId")
    delta_tokens = _optional_int(arguments, "deltaTokens", min_value=0)
    if delta_tokens is None:
        delta_tokens = _optional_int(arguments, "snoozeTokens", min_value=0)
    snooze_until = _optional_int(arguments, "snoozeUntilTokens", min_value=0)
    reset_snooze = _optional_bool(arguments, "resetSnooze")

    if delta_tokens is None and snooze_until is None and not reset_snooze:
        delta_tokens = DEFAULT_SNOOZE_DELTA_TOKENS

    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    updated_config = context_core.update_settings(
        config,
        conv_id=conv_id,
        snooze_delta=delta_tokens,
        snooze_until_tokens=snooze_until,
        reset_snooze=bool(reset_snooze),
    )
    status = context_core.build_status_payload(conv_id=conv_id, config=updated_config)
    _attach_lens_state(status, updated_config, conv_id)
    target_label = status.get("conversationId") or conv_id or "active"
    if reset_snooze and delta_tokens is None and snooze_until is None:
        summary = (
            f"Context Guard snooze cleared for {target_label}; "
            f"effective threshold is {status['effectiveThresholdTokens']:,} tokens."
        )
    else:
        summary = (
            f"Context Guard alerts snoozed for {target_label} until "
            f"{status['snoozedUntilTokens']:,} tokens "
            f"(effective threshold {status['effectiveThresholdTokens']:,})."
        )
    return _tool_result(summary, status)


def _handle_refresh(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle app_refresh tool calls for either status or overview view."""
    view = _optional_str(arguments, "view")
    if view is not None and view not in ("status", "overview"):
        raise ValueError(f"Invalid view {view!r}; expected 'status' or 'overview'")
    if view == "overview":
        return _handle_overview(arguments, request_meta)
    return _handle_status(arguments, request_meta)


def _handle_handoff(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle context_handoff and app_build_handoff tool calls."""
    _optional_str(arguments, "conversationId")
    note = _optional_str(arguments, "note") or ""
    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    brief_payload = context_core.build_handoff_brief(
        conv_id=conv_id,
        config=config,
        note=note,
    )
    return _tool_result(brief_payload["brief"], brief_payload)


def _handle_quota(arguments: dict[str, Any], _request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle app_quota tool calls for immediate cached quota + optional background refresh."""
    force_refresh = bool(_optional_bool(arguments, "refresh"))
    snapshot = get_quota_snapshot(trigger_if_stale=not force_refresh, force=force_refresh)
    groups = snapshot.get("groups", [])
    if groups:
        summary = f"Model Quota ({snapshot.get('status', 'ok')}): {len(groups)} group(s) loaded."
    else:
        summary = f"Model Quota ({snapshot.get('status', 'idle')})."
    return _tool_result(summary, {"view": "quota", "modelQuota": snapshot})


def _handle_search(arguments: dict[str, Any], _request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle lens_search and app_search tool calls."""
    query = _optional_str(arguments, "query") or ""
    scope = _optional_str(arguments, "scope") or "all"
    if scope not in ("all", "messages", "artifacts", "files"):
        raise ValueError(f"Invalid scope {scope!r}; expected 'all', 'messages', 'artifacts', or 'files'")
    include_subagents = bool(_optional_bool(arguments, "includeSubagents"))
    limit = _optional_int(arguments, "limit", min_value=1, max_value=50)
    config = context_core.load_config()
    result = lens_search.search_conversations(
        query,
        include_subagents=include_subagents,
        scope=scope,
        limit=limit if limit is not None else 25,
        config=config,
    )
    payload = {"view": "search", **result}
    q_label = f" for '{query}'" if query.strip() else " (recent)"
    summary = f"Agy Lens Search{q_label}: {result['matchedCount']} match(es) across {result['totalIndexed']} indexed chat(s)."
    return _tool_result(summary, payload)


def _infer_observed_startup_tokens(
    conv_id: str | None,
    config: dict[str, Any],
) -> int | None:
    """Return token count of the active conversation's earliest planner turn if available."""
    if not conv_id:
        return None
    status = context_core.build_status_payload(conv_id=conv_id, config=config)
    first_turn = status.get("firstTurnTokens")
    if isinstance(first_turn, int) and first_turn > 0:
        return first_turn
    history = status.get("history")
    if isinstance(history, list) and history:
        first = history[0]
        if isinstance(first, dict):
            total = first.get("totalTokens")
            if isinstance(total, int) and total > 0:
                return total
    return None


def _handle_footprint(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle lens_footprint and app_footprint tool calls."""
    workspace_path = _optional_str(arguments, "workspacePath")
    _optional_str(arguments, "conversationId")
    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    observed = _infer_observed_startup_tokens(conv_id, config)
    result = lens_footprint.inspect_footprint(
        workspace_path=workspace_path,
        config=config,
        observed_startup_tokens=observed,
    )
    payload = {"view": "footprint", **result}
    sm = result["summary"]
    summary = (
        f"Agy Lens Footprint ({result['workspacePath']}): "
        f"~{sm['estimatedStartupTokens']:,} est. startup tokens "
        f"({sm['rulesCount']} rules, {sm['skillsCount']} custom skills, {sm['mcpServersCount']} MCP servers)."
    )
    return _tool_result(summary, payload)


def _handle_clear_loop(arguments: dict[str, Any], request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle app_clear_loop tool calls."""
    _optional_str(arguments, "conversationId")
    config = context_core.load_config()
    conv_id = resolve_conversation_id(arguments, request_meta, config)
    lens_guard.clear_active_loop(conv_id)
    updated_config = context_core.load_config()
    status = context_core.build_status_payload(conv_id=conv_id, config=updated_config)
    _attach_lens_state(status, updated_config, conv_id)
    return _tool_result(f"Agy Lens Loop Guard dismissed for {conv_id or 'active'}.", status)


def _handle_test_alert(_arguments: dict[str, Any], _request_meta: dict[str, Any]) -> dict[str, Any]:
    """Handle app_test_alert tool calls from the UI Settings drawer."""
    config = context_core.load_config()
    res = lens_guard.maybe_send_alert(
        "test",
        "Agy Lens Test Alert",
        "Smart sound & desktop notifications are active.",
        config,
        force=True,
    )
    return _tool_result(
        f"Test alert result: sent={res.get('sent')} ({res.get('reason', 'ok')}).",
        {"view": "alert_test", **res},
    )


_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "context_status": _handle_status,
    "context_overview": _handle_overview,
    "context_configure": _handle_configure,
    "context_handoff": _handle_handoff,
    "lens_search": _handle_search,
    "lens_footprint": _handle_footprint,
    "app_save_settings": _handle_configure,
    "app_snooze": _handle_snooze,
    "app_refresh": _handle_refresh,
    "app_build_handoff": _handle_handoff,
    "app_quota": _handle_quota,
    "app_search": _handle_search,
    "app_footprint": _handle_footprint,
    "app_clear_loop": _handle_clear_loop,
    "app_test_alert": _handle_test_alert,
}


def call_tool(
    name: str,
    arguments: dict[str, Any],
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a Context Guard MCP tool and return MCP tool content/structuredContent."""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _tool_error(f"Unknown tool: {name}")

    if arguments is None:
        parsed_args: dict[str, Any] = {}
    elif isinstance(arguments, dict):
        parsed_args = arguments
    else:
        return _tool_error("Tool arguments must be a JSON object")

    meta = request_meta if isinstance(request_meta, dict) else {}
    try:
        return handler(parsed_args, meta)
    except ValueError as exc:
        return _tool_error(str(exc))


def _jsonrpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Construct a JSON-RPC 2.0 success response."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    """Construct a JSON-RPC 2.0 error response."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _extract_request_meta(
    msg: dict[str, Any],
    params: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Collect request _meta across MCP params._meta and fallback locations."""
    merged: dict[str, Any] = {}
    for source in (msg.get("_meta"), arguments.get("_meta"), params.get("_meta")):
        if isinstance(source, dict):
            merged.update(source)
    return merged


def handle_jsonrpc_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single decoded JSON-RPC 2.0 message, returning None for notifications."""
    if not isinstance(msg, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request")

    has_id = "id" in msg
    msg_id = msg.get("id")

    if "jsonrpc" in msg and msg.get("jsonrpc") != "2.0":
        return _jsonrpc_error(msg_id if has_id else None, -32600, "Invalid Request")

    method = msg.get("method")
    if not isinstance(method, str) or not method.strip():
        return _jsonrpc_error(msg_id if has_id else None, -32600, "Invalid Request")

    # Notifications carry no id (or use notifications/*) and must emit zero stdout bytes.
    if not has_id or method.startswith("notifications/"):
        return None

    params = msg.get("params")

    if method == "initialize":
        client_version = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol_version = _non_empty_str(client_version) or DEFAULT_PROTOCOL_VERSION
        return _jsonrpc_result(
            msg_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": dict(SERVER_INFO),
            },
        )

    if method == "ping":
        return _jsonrpc_result(msg_id, {})

    if method == "tools/list":
        return _jsonrpc_result(msg_id, {"tools": copy.deepcopy(TOOL_DEFINITIONS)})

    if method == "tools/call":
        if not isinstance(params, dict) or not _non_empty_str(params.get("name")):
            return _jsonrpc_error(msg_id, -32602, "Invalid params: 'name' is required")
        raw_args = params.get("arguments")
        if "arguments" in params and raw_args is not None and not isinstance(raw_args, dict):
            return _jsonrpc_error(msg_id, -32602, "Invalid params: 'arguments' must be an object")
        arguments = raw_args if isinstance(raw_args, dict) else {}
        request_meta = _extract_request_meta(msg, params, arguments)
        tool_output = call_tool(params["name"].strip(), arguments, request_meta=request_meta)
        return _jsonrpc_result(msg_id, tool_output)

    if method == "resources/list":
        return _jsonrpc_result(
            msg_id,
            {
                "resources": [
                    {
                        "uri": APP_URI,
                        "name": "Agy Lens Dashboard",
                        "mimeType": RESOURCE_MIME_TYPE,
                        "_meta": copy.deepcopy(_META_RESOURCE_UI),
                    }
                ]
            },
        )

    if method == "resources/read":
        uri = params.get("uri") if isinstance(params, dict) else None
        if uri != APP_URI:
            return _jsonrpc_error(msg_id, -32602, f"Unknown resource URI: {uri!r}")
        return _jsonrpc_result(
            msg_id,
            {
                "contents": [
                    {
                        "uri": APP_URI,
                        "mimeType": RESOURCE_MIME_TYPE,
                        "text": load_app_html(),
                        "_meta": copy.deepcopy(_META_RESOURCE_UI),
                    }
                ]
            },
        )

    return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")


def serve_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Run the newline-delimited JSON-RPC 2.0 loop over stdin/stdout."""
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout

    while True:
        raw_line = in_stream.readline()
        if raw_line == "":
            break
        line = raw_line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _jsonrpc_error(None, -32700, "Parse error")
        else:
            try:
                response = handle_jsonrpc_message(msg)
            except Exception as exc:
                if isinstance(msg, dict) and "id" in msg:
                    response = _jsonrpc_error(msg.get("id"), -32603, f"Internal error: {exc}")
                else:
                    response = None

        if response is not None:
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()


if __name__ == "__main__":
    serve_stdio()
