"""Shared core engine for the context-guard Antigravity plugin.

Provides stdlib-only (Python 3.9+) transcript parsing, compaction detection,
bloat attribution, atomic config/state persistence, transcript discovery,
status/overview payload generation, and smart handoff brief construction.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD_TOKENS = 300_000
DEFAULT_WARN_PERCENT = 80
DEFAULT_MODE = "stop"
DEFAULT_AUTO_REFRESH_SECONDS = 5
MIN_AUTO_REFRESH_SECONDS = 1
MAX_AUTO_REFRESH_SECONDS = 300
VALID_MODES = ("warn", "stop", "gate")
DEFAULT_MODEL_WINDOWS: dict[str, int] = {
    "gemini-4": 1_048_576,
    "gemini-3": 1_048_576,
    "claude": 200_000,
    "gpt-4o": 128_000,
    "default": 1_048_576,
}

MIN_THRESHOLD_TOKENS = 1_000
MAX_CONVERSATIONS_STORED = 200
COMPACTION_MIN_PREV_TOKENS = 15_000
COMPACTION_DROP_RATIO = 0.65
CONTEXT_SUMMARY_MARKER = "<CONTEXT_SUMMARY>"
MAX_HISTORY_ITEMS = 60
MAX_TOP_BLOAT_STEPS = 8
MAX_AVG_DELTA_TURNS = 5
MAX_RECENT_PROMPTS = 3
MAX_RECENT_FILES = 15
MAX_RECENT_COMMANDS = 10

_FILE_ARG_KEYS = frozenset(
    {
        "path",
        "targetfile",
        "target_file",
        "absolutepath",
        "absolute_path",
        "filepath",
        "file_path",
        "file",
        "filename",
    }
)
_COMMAND_ARG_KEYS = frozenset({"commandline", "command_line", "command", "cmd"})
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
_ADDITIONAL_METADATA_RE = re.compile(
    r"<ADDITIONAL_METADATA>.*?(?:</ADDITIONAL_METADATA>|$)",
    re.DOTALL,
)
_SYSTEM_MESSAGE_WRAPPER_RE = re.compile(
    r"^The following is a <SYSTEM_MESSAGE>.*?<SYSTEM_MESSAGE>\s*",
    re.DOTALL,
)
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_PBTXT_RE = re.compile(r'title:"((?:[^"\\]|\\.)*)"')
_PBTXT_UNESCAPE = {"n": " ", "r": " ", "t": " ", '"': '"', "\\": "\\"}
MAX_TITLE_CHARS = 72


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce numeric values to int, ignoring booleans and invalid values."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _text_of(value: Any) -> str:
    """Convert string or structured content blocks into plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    return str(value)


def _unwrap_json_value(value: Any) -> Any:
    """Unwrap up to two layers of JSON-encoded strings (e.g. '\"/repo/app.py\"')."""
    current = value
    for _ in range(2):
        if not isinstance(current, str):
            break
        stripped = current.strip()
        if not stripped or stripped[0] not in ('"', "{", "["):
            break
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            break
        current = parsed
    return current


def _unwrap_user_prompt(text: str) -> str:
    """Extract <USER_REQUEST> body and strip <ADDITIONAL_METADATA> wrappers."""
    if not text:
        return ""
    match = _USER_REQUEST_RE.search(text)
    extracted = match.group(1) if match else text
    cleaned = _ADDITIONAL_METADATA_RE.sub("", extracted)
    return cleaned.strip()


def _sanitize_snippet(text: str, max_len: int = 100) -> str:
    """Collapse text to a clean single-line snippet bounded to max_len chars."""
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + "..."


def _append_unique_recent(items: list[str], value: str, max_items: int) -> None:
    """Append value as most recent unique entry, keeping list bounded."""
    if not value:
        return
    if value in items:
        items.remove(value)
    items.append(value)
    if len(items) > max_items:
        del items[0]


def _default_conversation_state() -> dict[str, Any]:
    """Return fresh per-conversation state with -1 epoch sentinels."""
    return {
        "transcriptPath": None,
        "lastCompactionIndex": 0,
        "warnedAtEpoch": -1,
        "alertedAtEpoch": -1,
        "snoozedUntilTokens": 0,
        "customThresholdTokens": None,
        "lastTokens": 0,
        "lastModel": "",
        "updatedAt": "",
    }


def _normalize_conversation_state(raw: Any) -> dict[str, Any]:
    """Normalize an existing conversation state dict, filling missing keys."""
    state = _default_conversation_state()
    if not isinstance(raw, dict):
        return state

    transcript_path = raw.get("transcriptPath")
    if isinstance(transcript_path, str) and transcript_path.strip():
        state["transcriptPath"] = transcript_path

    state["lastCompactionIndex"] = max(0, _safe_int(raw.get("lastCompactionIndex"), 0))
    state["warnedAtEpoch"] = _safe_int(raw.get("warnedAtEpoch"), -1)
    state["alertedAtEpoch"] = _safe_int(raw.get("alertedAtEpoch"), -1)
    state["snoozedUntilTokens"] = max(0, _safe_int(raw.get("snoozedUntilTokens"), 0))

    custom = raw.get("customThresholdTokens")
    if custom is not None and not isinstance(custom, bool):
        parsed_custom = _safe_int(custom, 0)
        if parsed_custom > 0:
            state["customThresholdTokens"] = max(MIN_THRESHOLD_TOKENS, parsed_custom)

    state["lastTokens"] = max(0, _safe_int(raw.get("lastTokens"), 0))
    last_model = raw.get("lastModel")
    if isinstance(last_model, str):
        state["lastModel"] = last_model
    updated_at = raw.get("updatedAt")
    if isinstance(updated_at, str):
        state["updatedAt"] = updated_at
    return state


def _default_config() -> dict[str, Any]:
    """Return fresh global configuration defaults."""
    return {
        "enabled": True,
        "thresholdTokens": DEFAULT_THRESHOLD_TOKENS,
        "warnPercent": DEFAULT_WARN_PERCENT,
        "mode": DEFAULT_MODE,
        "autoRefreshSeconds": DEFAULT_AUTO_REFRESH_SECONDS,
        "modelWindows": dict(DEFAULT_MODEL_WINDOWS),
        "lastActiveConversationId": None,
        "conversations": {},
    }


def get_config_path() -> Path:
    """Return config file path, honoring CONTEXT_GUARD_CONFIG_PATH if set."""
    override = os.environ.get("CONTEXT_GUARD_CONFIG_PATH", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".gemini" / "config" / "context-guard.json"


def load_config() -> dict[str, Any]:
    """Load config from disk, returning sanitized defaults on missing/corrupt files."""
    config = _default_config()
    path = get_config_path()
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return config

    if not isinstance(raw, dict):
        return config

    if isinstance(raw.get("enabled"), bool):
        config["enabled"] = raw["enabled"]

    thresh = _safe_int(raw.get("thresholdTokens"), 0)
    if thresh > 0:
        config["thresholdTokens"] = max(MIN_THRESHOLD_TOKENS, thresh)

    warn = _safe_int(raw.get("warnPercent"), 0)
    if warn > 0:
        config["warnPercent"] = max(1, min(99, warn))

    mode = raw.get("mode")
    if isinstance(mode, str) and mode in VALID_MODES:
        config["mode"] = mode

    refresh_secs = _safe_int(raw.get("autoRefreshSeconds"), 0)
    if refresh_secs > 0:
        config["autoRefreshSeconds"] = max(
            MIN_AUTO_REFRESH_SECONDS,
            min(MAX_AUTO_REFRESH_SECONDS, refresh_secs),
        )

    raw_windows = raw.get("modelWindows")
    if isinstance(raw_windows, dict):
        windows = dict(DEFAULT_MODEL_WINDOWS)
        for key, val in raw_windows.items():
            parsed_win = _safe_int(val, 0)
            if isinstance(key, str) and key and parsed_win > 0:
                windows[key] = parsed_win
        config["modelWindows"] = windows

    last_active = raw.get("lastActiveConversationId")
    if isinstance(last_active, str) and last_active.strip():
        config["lastActiveConversationId"] = last_active

    raw_convs = raw.get("conversations")
    if isinstance(raw_convs, dict):
        convs: dict[str, dict[str, Any]] = {}
        for cid, entry in raw_convs.items():
            if isinstance(cid, str) and cid and isinstance(entry, dict):
                convs[cid] = _normalize_conversation_state(entry)
        config["conversations"] = convs

    return config


def _is_protected_conversation(
    cid: str,
    entry: dict[str, Any],
    last_active_id: str | None,
) -> bool:
    """Return True if conversation entry must not be pruned."""
    if last_active_id and cid == last_active_id:
        return True
    if entry.get("customThresholdTokens") is not None:
        return True
    return _safe_int(entry.get("snoozedUntilTokens"), 0) > 0


def _prune_conversations(
    conversations: dict[str, dict[str, Any]],
    last_active_id: str | None,
    max_entries: int = MAX_CONVERSATIONS_STORED,
) -> dict[str, dict[str, Any]]:
    """Bound stored conversations to max_entries while preserving protected entries."""
    if len(conversations) <= max_entries:
        return conversations

    protected_ids: set[str] = set()
    evictable: list[tuple[str, int, str]] = []

    for idx, (cid, entry) in enumerate(conversations.items()):
        if not isinstance(entry, dict):
            continue
        if _is_protected_conversation(cid, entry, last_active_id):
            protected_ids.add(cid)
        else:
            updated_at = entry.get("updatedAt")
            sort_ts = updated_at if isinstance(updated_at, str) else ""
            evictable.append((sort_ts, idx, cid))

    remaining_slots = max(0, max_entries - len(protected_ids))
    evictable.sort(key=lambda item: (item[0], item[1]), reverse=True)
    kept_evictable_ids = {cid for _, _, cid in evictable[:remaining_slots]}
    kept_ids = protected_ids | kept_evictable_ids

    return {
        cid: entry
        for cid, entry in conversations.items()
        if cid in kept_ids and isinstance(entry, dict)
    }


def save_config(config: dict[str, Any]) -> Path:
    """Prune stale conversations and atomically persist config to disk."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    raw_convs = config.get("conversations")
    if isinstance(raw_convs, dict):
        last_active = config.get("lastActiveConversationId")
        active_id = last_active if isinstance(last_active, str) else None
        config["conversations"] = _prune_conversations(raw_convs, active_id)

    payload = json.dumps(config, indent=2, sort_keys=False) + "\n"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".context-guard.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return path


def get_conversation_state(config: dict[str, Any], conv_id: str) -> dict[str, Any]:
    """Ensure and return normalized mutable state for conv_id in config."""
    convs = config.get("conversations")
    if not isinstance(convs, dict):
        convs = {}
        config["conversations"] = convs

    existing = convs.get(conv_id)
    normalized = _normalize_conversation_state(existing)
    if isinstance(existing, dict):
        # Preserve dict identity so callers holding references stay in sync.
        existing.update(normalized)
        return existing
    convs[conv_id] = normalized
    return normalized


_SOURCE_APP_LABELS: dict[str, str] = {
    "antigravity": "Antigravity",
    "antigravity-cli": "Antigravity CLI",
    "antigravity-ide": "Antigravity IDE",
}
_STEP_TYPE_CATEGORY: dict[str, str] = {
    "USER_INPUT": "user",
    "PLANNER_RESPONSE": "model",
    "GENERIC": "toolOutputs",
    "SYSTEM_MESSAGE": "system",
}
_SOURCE_CATEGORY: dict[str, str] = {
    "USER_EXPLICIT": "user",
    "MODEL": "model",
    "SYSTEM": "system",
}


def _lookup_conversation(config: dict[str, Any], conv_id: str | None) -> dict[str, Any] | None:
    """Return raw conversation entry if present and a dict, without mutating config."""
    if not conv_id:
        return None
    convs = config.get("conversations")
    if not isinstance(convs, dict):
        return None
    entry = convs.get(conv_id)
    return entry if isinstance(entry, dict) else None


def _resolve_base_threshold(config: dict[str, Any], conv_id: str | None) -> int:
    """Return global or per-conversation custom threshold before snooze."""
    global_thresh = max(
        MIN_THRESHOLD_TOKENS,
        _safe_int(config.get("thresholdTokens"), DEFAULT_THRESHOLD_TOKENS),
    )
    entry = _lookup_conversation(config, conv_id)
    if entry is None:
        return global_thresh
    custom = entry.get("customThresholdTokens")
    if custom is not None and not isinstance(custom, bool):
        parsed = _safe_int(custom, 0)
        if parsed > 0:
            return max(MIN_THRESHOLD_TOKENS, parsed)
    return global_thresh


def resolve_effective_threshold(config: dict[str, Any], conv_id: str | None) -> int:
    """Return active token threshold, accounting for custom threshold and snooze."""
    base = _resolve_base_threshold(config, conv_id)
    entry = _lookup_conversation(config, conv_id)
    if entry is None:
        return base
    snooze = max(0, _safe_int(entry.get("snoozedUntilTokens"), 0))
    return snooze if snooze > base else base


def resolve_model_window(model_name: str, config: dict[str, Any]) -> int:
    """Resolve context window size for model_name using config.modelWindows."""
    raw_windows = config.get("modelWindows")
    windows = raw_windows if isinstance(raw_windows, dict) and raw_windows else DEFAULT_MODEL_WINDOWS
    default_window = max(
        1,
        _safe_int(windows.get("default"), DEFAULT_MODEL_WINDOWS["default"]),
    )
    if not model_name:
        return default_window

    lowered = model_name.lower()
    # Exact key match first, then longest substring match excluding "default".
    for key, val in windows.items():
        if isinstance(key, str) and key.lower() == lowered:
            parsed = _safe_int(val, 0)
            if parsed > 0:
                return parsed

    best_len = -1
    best_window = default_window
    for key, val in windows.items():
        if not isinstance(key, str) or not key or key.lower() == "default":
            continue
        if key.lower() in lowered and len(key) > best_len:
            parsed = _safe_int(val, 0)
            if parsed > 0:
                best_len = len(key)
                best_window = parsed
    return best_window


def get_brain_roots() -> list[Path]:
    """Return deduplicated Antigravity data root directories."""
    override = os.environ.get("CONTEXT_GUARD_BRAIN_ROOTS", "").strip()
    candidates: list[Path] = []
    if override:
        for chunk in override.split(os.pathsep):
            cleaned = chunk.strip()
            if cleaned:
                candidates.append(Path(os.path.expanduser(cleaned)))
    else:
        app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
        if app_data:
            candidates.append(Path(os.path.expanduser(app_data)))
        home = Path.home()
        candidates.extend(
            [
                home / ".gemini" / "antigravity",
                home / ".gemini" / "antigravity-cli",
                home / ".gemini" / "antigravity-ide",
            ]
        )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            norm = str(path.resolve())
        except OSError:
            norm = os.path.abspath(str(path))
        if norm not in seen:
            seen.add(norm)
            deduped.append(path)
    return deduped


def _infer_source_app(root: Path) -> str:
    """Map brain root directory name to a human-readable source application label."""
    return _SOURCE_APP_LABELS.get(root.name.lower(), root.name or "Unknown")


def _is_safe_conv_id(conv_id: str) -> bool:
    """Reject empty IDs or path-traversal segments before joining brain paths."""
    if not conv_id or conv_id in (".", ".."):
        return False
    return "/" not in conv_id and "\\" not in conv_id and os.sep not in conv_id


def list_recent_transcripts(limit: int = 20) -> list[dict[str, Any]]:
    """Scan all brain roots and return recent transcript metadata sorted newest-first."""
    if limit <= 0:
        return []

    by_conv: dict[str, dict[str, Any]] = {}
    for root in get_brain_roots():
        brain_dir = root / "brain"
        if not brain_dir.is_dir():
            continue
        source_app = _infer_source_app(root)
        try:
            conv_dirs = list(brain_dir.iterdir())
        except OSError:
            continue

        for conv_dir in conv_dirs:
            if not conv_dir.is_dir():
                continue
            transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            try:
                if not transcript.is_file():
                    continue
                mtime = transcript.stat().st_mtime
            except OSError:
                continue

            conv_id = conv_dir.name
            existing = by_conv.get(conv_id)
            if existing is None or mtime > existing["mtime"]:
                by_conv[conv_id] = {
                    "conversationId": conv_id,
                    "transcriptPath": str(transcript),
                    "mtime": mtime,
                    "sourceApp": source_app,
                }

    ranked = sorted(by_conv.values(), key=lambda item: item["mtime"], reverse=True)
    return ranked[:limit]


def find_transcript_path(
    conv_id: str | None = None,
    explicit_path: str | None = None,
    config: dict[str, Any] | None = None,
) -> Path | None:
    """Resolve transcript.jsonl path via explicit path, config, roots, or latest."""
    if explicit_path:
        try:
            candidate = Path(os.path.expanduser(str(explicit_path)))
            if candidate.is_file():
                return candidate
        except (OSError, ValueError):
            pass

    cfg = config if config is not None else load_config()
    if conv_id:
        entry = _lookup_conversation(cfg, conv_id)
        if entry is not None and entry.get("transcriptPath"):
            try:
                saved = Path(os.path.expanduser(str(entry["transcriptPath"])))
                if saved.is_file():
                    return saved
            except (OSError, ValueError):
                pass

        if _is_safe_conv_id(conv_id):
            best_path: Path | None = None
            best_mtime = -1.0
            for root in get_brain_roots():
                candidate = (
                    root / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
                )
                try:
                    if candidate.is_file():
                        mtime = candidate.stat().st_mtime
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best_path = candidate
                except (OSError, ValueError):
                    continue
            if best_path is not None:
                return best_path
        return None

    recent = list_recent_transcripts(1)
    if recent:
        return Path(recent[0]["transcriptPath"])
    return None


def _infer_conv_id_from_path(transcript_path: Path) -> str | None:
    """Extract <convId> from .../<root>/brain/<convId>/.system_generated/logs/transcript.jsonl."""
    parts = transcript_path.parts
    if len(parts) >= 5 and parts[-1] == "transcript.jsonl":
        if parts[-2] == "logs" and parts[-3] == ".system_generated" and parts[-5] == "brain":
            return parts[-4]
    return None


def _read_pbtxt_title(pbtxt_path: Path) -> str:
    """Extract a conversation title from an Antigravity annotations/*.pbtxt file."""
    try:
        if not pbtxt_path.is_file():
            return ""
        raw = pbtxt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = _TITLE_PBTXT_RE.search(raw)
    if not match:
        return ""
    unescaped = re.sub(
        r"\\(.)",
        lambda m: _PBTXT_UNESCAPE.get(m.group(1), m.group(1)),
        match.group(1),
    )
    return _WHITESPACE_RE.sub(" ", unescaped).strip()


def _load_sqlite_conv_metadata(root: Path) -> dict[str, dict[str, Any]]:
    """Read conversation titles and parent/subagent metadata from conversation_summaries.db."""
    db_path = root / "conversation_summaries.db"
    meta: dict[str, dict[str, Any]] = {}
    try:
        if not db_path.is_file():
            return meta
    except OSError:
        return meta

    con: sqlite3.Connection | None = None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=0.5)
        cur = con.cursor()
        tables = {
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "conversation_summaries" in tables:
            cols = {
                row[1]
                for row in cur.execute("PRAGMA table_info(conversation_summaries)").fetchall()
            }
            if "conversation_id" in cols:
                select_cols = [
                    "conversation_id",
                    "title" if "title" in cols else "''",
                    "preview" if "preview" in cols else "''",
                    "parent_conversation_id" if "parent_conversation_id" in cols else "''",
                    "agent_name" if "agent_name" in cols else "''",
                ]
                query = f"SELECT {', '.join(select_cols)} FROM conversation_summaries"
                for cid, title, preview, parent_id, agent_name in cur.execute(query).fetchall():
                    if not isinstance(cid, str) or not cid:
                        continue
                    clean_title = _WHITESPACE_RE.sub(" ", str(title or preview or "")).strip()
                    meta[cid] = {
                        "title": clean_title,
                        "parentConversationId": str(parent_id or "").strip(),
                        "agentName": str(agent_name or "").strip(),
                    }
        if "conversation_parents" in tables:
            for cid, parent_id in cur.execute(
                "SELECT conversation_id, parent_id FROM conversation_parents"
            ).fetchall():
                if not isinstance(cid, str) or not cid or not isinstance(parent_id, str):
                    continue
                entry = meta.setdefault(
                    cid, {"title": "", "parentConversationId": "", "agentName": ""}
                )
                if not entry["parentConversationId"]:
                    entry["parentConversationId"] = parent_id.strip()
    except (sqlite3.Error, OSError, ValueError):
        return meta
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
    return meta


def _collect_conv_metadata(
    roots: list[Path],
    conv_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect title and parent/subagent metadata across Antigravity brain roots."""
    merged: dict[str, dict[str, Any]] = {}
    for root in roots:
        for cid, info in _load_sqlite_conv_metadata(root).items():
            target = merged.setdefault(
                cid, {"title": "", "parentConversationId": "", "agentName": ""}
            )
            if not target["title"] and info["title"]:
                target["title"] = info["title"]
            if not target["parentConversationId"] and info["parentConversationId"]:
                target["parentConversationId"] = info["parentConversationId"]
            if not target["agentName"] and info["agentName"]:
                target["agentName"] = info["agentName"]

        ann_dir = root / "annotations"
        try:
            if not ann_dir.is_dir():
                continue
        except OSError:
            continue

        if conv_ids is not None:
            candidates = [ann_dir / f"{cid}.pbtxt" for cid in conv_ids if _is_safe_conv_id(cid)]
        else:
            try:
                candidates = list(ann_dir.glob("*.pbtxt"))
            except OSError:
                candidates = []

        for pbtxt in candidates:
            pb_title = _read_pbtxt_title(pbtxt)
            if not pb_title:
                continue
            cid = pbtxt.stem
            target = merged.setdefault(
                cid, {"title": "", "parentConversationId": "", "agentName": ""}
            )
            # Annotation .pbtxt carries the latest user/auto-generated title.
            target["title"] = pb_title
    return merged


def _resolve_conv_display_meta(
    conv_id: str | None,
    first_prompt: str,
    meta_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve human-readable title and subagent attribution for a conversation."""
    info = meta_map.get(conv_id, {}) if conv_id else {}
    title = str(info.get("title") or "").strip()
    parent_id = str(info.get("parentConversationId") or "").strip()
    agent_name = str(info.get("agentName") or "").strip()

    if not title and first_prompt:
        compact = _WHITESPACE_RE.sub(" ", first_prompt).strip()
        if len(compact) > MAX_TITLE_CHARS:
            compact = compact[: MAX_TITLE_CHARS - 3].rstrip() + "..."
        title = compact
    if not title and conv_id:
        title = conv_id

    return {
        "title": title,
        "agentName": agent_name,
        "parentConversationId": parent_id,
        "isSubagent": bool(parent_id or agent_name),
    }


def _empty_analysis(transcript_path: str) -> dict[str, Any]:
    """Return zeroed analysis payload for missing or empty transcripts."""
    return {
        "transcriptPath": transcript_path,
        "model": "",
        "stepCount": 0,
        "turnCount": 0,
        "inputTokens": 0,
        "cacheReadTokens": 0,
        "outputTokens": 0,
        "promptTokens": 0,
        "totalTokens": 0,
        "pendingDeltaTokens": 0,
        "effectiveTokens": 0,
        "cacheHitPercent": 0.0,
        "compactionCount": 0,
        "activeEpochStep": 0,
        "compactions": [],
        "history": [],
        "bloatByCategory": {
            "user": 0,
            "model": 0,
            "toolOutputs": 0,
            "system": 0,
        },
        "topBloatSteps": [],
        "avgDeltaPerTurn": 0,
        "firstUserPrompt": "",
        "recentUserPrompts": [],
        "recentFiles": [],
        "recentCommands": [],
    }


def _parse_transcript_steps(path: Path) -> list[dict[str, Any]]:
    """Stream JSONL file line-by-line, skipping blank or malformed lines."""
    steps: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    steps.append(obj)
    except OSError:
        return []
    return steps


def _extract_tool_artifacts(tool_calls: Any) -> tuple[list[str], list[str], list[str]]:
    """Extract tool names, file paths, and commands from PLANNER_RESPONSE.tool_calls."""
    names: list[str] = []
    files: list[str] = []
    commands: list[str] = []

    unwrapped_calls = _unwrap_json_value(tool_calls)
    if not isinstance(unwrapped_calls, list):
        return names, files, commands

    for call in unwrapped_calls:
        if not isinstance(call, dict):
            continue
        func = call.get("function") if isinstance(call.get("function"), dict) else None
        raw_name = call.get("name") or (func.get("name") if func else "")
        name = str(raw_name) if raw_name else ""
        names.append(name)

        raw_args = call.get("args")
        if raw_args is None and func is not None:
            raw_args = func.get("arguments")
        if raw_args is None:
            raw_args = call.get("arguments")

        args = _unwrap_json_value(raw_args)
        if not isinstance(args, dict):
            continue

        for key, val in args.items():
            if not isinstance(key, str):
                continue
            key_lower = key.lower()
            unwrapped_val = _unwrap_json_value(val)
            if not isinstance(unwrapped_val, str):
                continue
            cleaned = unwrapped_val.strip()
            if not cleaned:
                continue
            if key_lower in _FILE_ARG_KEYS:
                files.append(cleaned)
            elif key_lower in _COMMAND_ARG_KEYS:
                commands.append(cleaned)

    return names, files, commands


def _categorize_step(step_type: str, source: str) -> str:
    """Classify a transcript step into one of the 4 bloat categories."""
    if step_type in _STEP_TYPE_CATEGORY:
        return _STEP_TYPE_CATEGORY[step_type]
    return _SOURCE_CATEGORY.get(source, "system")


def _detect_compaction_marker(step_type: str, content_text: str) -> str | None:
    """Identify structural compaction boundary steps without matching prose mentions."""
    if step_type == "CHECKPOINT":
        return "checkpoint"
    if step_type == "SYSTEM_MESSAGE":
        body = _SYSTEM_MESSAGE_WRAPPER_RE.sub("", content_text, count=1).lstrip()
        if body.startswith(CONTEXT_SUMMARY_MARKER):
            return "marker"
    return None


def _summarize_epoch_bloat(
    step_records: list[dict[str, Any]],
    active_epoch_pos: int,
    last_valid_planner_pos: int,
) -> tuple[int, dict[str, int], list[dict[str, Any]]]:
    """Compute pending post-response delta, category totals, and top bloat steps."""
    after_planner = last_valid_planner_pos + 1 if last_valid_planner_pos >= 0 else 0
    post_response_start = max(active_epoch_pos, after_planner)
    pending_delta = sum(rec["rawContentLen"] // 4 for rec in step_records[post_response_start:])

    bloat_by_cat = {"user": 0, "model": 0, "toolOutputs": 0, "system": 0}
    epoch_records = step_records[active_epoch_pos:]
    for rec in epoch_records:
        bloat_by_cat[rec["category"]] += rec["estimatedTokens"]

    top_bloat = sorted(
        (
            {
                "stepIndex": rec["stepIndex"],
                "source": rec["source"],
                "type": rec["type"],
                "category": rec["category"],
                "tools": rec["tools"],
                "estimatedTokens": rec["estimatedTokens"],
                "snippet": rec["snippet"],
            }
            for rec in epoch_records
            if rec["estimatedTokens"] > 0
        ),
        key=lambda item: item["estimatedTokens"],
        reverse=True,
    )[:MAX_TOP_BLOAT_STEPS]
    return pending_delta, bloat_by_cat, top_bloat


def _compute_avg_delta(history: list[dict[str, Any]], active_epoch_step: int) -> int:
    """Average positive turn-to-turn token deltas across up to the last 5 active-epoch turns."""
    positive_deltas = [
        item["deltaTokens"]
        for item in history
        if item["stepIndex"] >= active_epoch_step and item["deltaTokens"] > 0
    ]
    recent = positive_deltas[-MAX_AVG_DELTA_TURNS:]
    if not recent:
        return 0
    return int(round(sum(recent) / len(recent)))


def analyze_transcript(transcript_path: str | Path) -> dict[str, Any]:
    """Analyze an Antigravity transcript.jsonl into metrics, bloat, and artifacts."""
    path = Path(transcript_path)
    result = _empty_analysis(str(path))
    raw_steps = _parse_transcript_steps(path)
    if not raw_steps:
        return result

    result["stepCount"] = len(raw_steps)

    # Positional tool attribution tracks tool_calls across subsequent GENERIC steps.
    pending_tools: list[str] = []
    tool_cursor = 0

    prev_total: int | None = None
    last_valid_planner_pos = -1
    active_epoch_pos = 0
    awaiting_post_marker_drop = False

    history: list[dict[str, Any]] = []
    compactions: list[dict[str, Any]] = []
    step_records: list[dict[str, Any]] = []
    user_prompts: list[str] = []
    recent_files: list[str] = []
    recent_commands: list[str] = []

    for pos, step in enumerate(raw_steps):
        raw_idx = step.get("step_index")
        step_index = raw_idx if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) else pos
        step_type = str(step.get("type") or "")
        source = str(step.get("source") or "")
        status = str(step.get("status") or "")
        created_at = str(step.get("created_at") or "")
        content_text = _text_of(step.get("content"))
        thinking_text = _text_of(step.get("thinking"))

        marker_reason = _detect_compaction_marker(step_type, content_text)
        marker_detected = marker_reason is not None
        drop_detected = False
        prior_total: int | None = None
        step_tools: list[str] = []
        est_tokens = len(content_text) // 4

        if step_type == "USER_INPUT":
            pending_tools = []
            tool_cursor = 0
            unwrapped = _unwrap_user_prompt(content_text)
            if unwrapped:
                user_prompts.append(unwrapped)
                snippet_source = unwrapped
            else:
                snippet_source = content_text
        elif step_type == "PLANNER_RESPONSE":
            pending_tools = []
            tool_cursor = 0

            in_tok = max(0, _safe_int(step.get("input_tokens"), 0))
            cache_tok = max(0, _safe_int(step.get("cache_read_tokens"), 0))
            out_tok = max(0, _safe_int(step.get("output_tokens"), 0))
            prompt_tok = in_tok + cache_tok
            total_tok = prompt_tok + out_tok

            text_est = (len(content_text) + len(thinking_text)) // 4
            valid_names: list[str] = []
            tool_names: list[str] = []
            if status != "ERROR":
                tool_names, files, commands = _extract_tool_artifacts(step.get("tool_calls"))
                for fpath in files:
                    _append_unique_recent(recent_files, fpath, MAX_RECENT_FILES)
                for cmd in commands:
                    _append_unique_recent(recent_commands, cmd, MAX_RECENT_COMMANDS)
                valid_names = [n for n in tool_names if n]
                step_tools = valid_names
                est_tokens = max(out_tok, text_est)
            else:
                est_tokens = text_est

            snippet_source = content_text or thinking_text or ", ".join(valid_names)

            # Treat PLANNER_RESPONSE as valid telemetry only when non-errored with real prompt/initial tokens.
            is_valid_telemetry = status != "ERROR" and (
                prompt_tok > 0 or ((prev_total or 0) == 0 and total_tok > 0)
            )
            if is_valid_telemetry:
                prior_total = prev_total
                if awaiting_post_marker_drop and prompt_tok > 0:
                    awaiting_post_marker_drop = False
                    if compactions and (
                        (prior_total is not None and prompt_tok < prior_total * COMPACTION_DROP_RATIO)
                        or compactions[-1]["newPromptTokens"] == 0
                    ):
                        compactions[-1]["newPromptTokens"] = prompt_tok
                elif (
                    prior_total is not None
                    and prior_total >= COMPACTION_MIN_PREV_TOKENS
                    and 0 < prompt_tok < prior_total * COMPACTION_DROP_RATIO
                ):
                    drop_detected = True

                delta = total_tok - prior_total if prior_total is not None else 0
                model_name = str(step.get("model") or result["model"] or "")

                result["model"] = model_name
                result["inputTokens"] = in_tok
                result["cacheReadTokens"] = cache_tok
                result["outputTokens"] = out_tok
                result["promptTokens"] = prompt_tok
                result["totalTokens"] = total_tok
                result["turnCount"] += 1

                history.append(
                    {
                        "stepIndex": step_index,
                        "inputTokens": in_tok,
                        "cacheReadTokens": cache_tok,
                        "outputTokens": out_tok,
                        "totalTokens": total_tok,
                        "deltaTokens": delta,
                        "model": model_name,
                        "createdAt": created_at,
                    }
                )
                prev_total = total_tok
                last_valid_planner_pos = pos

                if tool_names:
                    pending_tools = tool_names
                    tool_cursor = 0
        elif step_type == "GENERIC":
            if tool_cursor < len(pending_tools):
                attributed = pending_tools[tool_cursor]
                if attributed:
                    step_tools = [attributed]
                tool_cursor += 1
            snippet_source = content_text
        else:
            snippet_source = content_text

        if drop_detected or marker_detected:
            prev_tokens = prior_total if drop_detected else result["totalTokens"]
            if marker_detected and not drop_detected:
                awaiting_post_marker_drop = True
                # Clear stale pre-compaction telemetry until the next PLANNER_RESPONSE runs.
                result["inputTokens"] = 0
                result["cacheReadTokens"] = 0
                result["outputTokens"] = 0
                result["promptTokens"] = 0
                result["totalTokens"] = 0

            compactions.append(
                {
                    "stepIndex": step_index,
                    "reason": "drop" if drop_detected else (marker_reason or "marker"),
                    "prevTotalTokens": prev_tokens,
                    "newPromptTokens": result["promptTokens"] if drop_detected else 0,
                }
            )
            result["compactionCount"] += 1
            result["activeEpochStep"] = step_index
            active_epoch_pos = pos

        category = _categorize_step(step_type, source)
        step_records.append(
            {
                "pos": pos,
                "stepIndex": step_index,
                "source": source,
                "type": step_type,
                "category": category,
                "tools": step_tools,
                "estimatedTokens": est_tokens,
                "snippet": _sanitize_snippet(snippet_source),
                "rawContentLen": len(content_text),
            }
        )

    pending_delta, bloat_by_cat, top_bloat = _summarize_epoch_bloat(
        step_records,
        active_epoch_pos,
        last_valid_planner_pos,
    )

    prompt_tokens = result["promptTokens"]
    cache_read = result["cacheReadTokens"]
    cache_hit = round((cache_read / prompt_tokens) * 100.0, 1) if prompt_tokens > 0 else 0.0

    result["pendingDeltaTokens"] = pending_delta
    result["effectiveTokens"] = result["totalTokens"] + pending_delta
    result["cacheHitPercent"] = cache_hit
    result["compactions"] = compactions
    result["history"] = history[-MAX_HISTORY_ITEMS:]
    result["bloatByCategory"] = bloat_by_cat
    result["topBloatSteps"] = top_bloat
    result["avgDeltaPerTurn"] = _compute_avg_delta(history, result["activeEpochStep"])
    result["firstUserPrompt"] = user_prompts[0] if user_prompts else ""
    result["recentUserPrompts"] = user_prompts[-MAX_RECENT_PROMPTS:]
    result["recentFiles"] = recent_files
    result["recentCommands"] = recent_commands

    return result


def _normalize_global_settings(config: dict[str, Any]) -> tuple[bool, str, int, int]:
    """Return sanitized (enabled, mode, threshold_tokens, warn_percent) from config."""
    raw_enabled = config.get("enabled", True)
    enabled = raw_enabled if isinstance(raw_enabled, bool) else True
    raw_mode = config.get("mode", DEFAULT_MODE)
    mode = raw_mode if isinstance(raw_mode, str) and raw_mode in VALID_MODES else DEFAULT_MODE
    threshold_tokens = max(
        MIN_THRESHOLD_TOKENS,
        _safe_int(config.get("thresholdTokens"), DEFAULT_THRESHOLD_TOKENS),
    )
    warn_percent = max(1, min(99, _safe_int(config.get("warnPercent"), DEFAULT_WARN_PERCENT)))
    return enabled, mode, threshold_tokens, warn_percent


def _normalize_auto_refresh_seconds(config: dict[str, Any]) -> int:
    """Return sanitized dashboard auto-refresh interval in seconds."""
    return max(
        MIN_AUTO_REFRESH_SECONDS,
        min(
            MAX_AUTO_REFRESH_SECONDS,
            _safe_int(config.get("autoRefreshSeconds"), DEFAULT_AUTO_REFRESH_SECONDS),
        ),
    )


def _status_label(
    *,
    enabled: bool,
    has_data: bool,
    effective_tokens: int,
    warn_threshold: int,
    effective_threshold: int,
) -> str:
    """Compute status state in {'ok', 'warn', 'limit', 'disabled', 'no_data'}."""
    if not enabled:
        return "disabled"
    if not has_data:
        return "no_data"
    if effective_tokens >= effective_threshold:
        return "limit"
    if effective_tokens >= warn_threshold:
        return "warn"
    return "ok"


def build_status_payload(
    conv_id: str | None = None,
    explicit_path: str | None = None,
    config: dict[str, Any] | None = None,
    _meta_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build full status dashboard payload for a conversation."""
    cfg = config if config is not None else load_config()
    transcript = find_transcript_path(conv_id=conv_id, explicit_path=explicit_path, config=cfg)

    target_conv_id = conv_id
    if target_conv_id is None and transcript is not None:
        target_conv_id = _infer_conv_id_from_path(transcript)
    if target_conv_id is None:
        last_active = cfg.get("lastActiveConversationId")
        if isinstance(last_active, str) and last_active:
            target_conv_id = last_active

    conv_entry = _lookup_conversation(cfg, target_conv_id)
    snoozed_until = (
        max(0, _safe_int(conv_entry.get("snoozedUntilTokens"), 0))
        if conv_entry is not None
        else 0
    )

    enabled, mode, _, warn_percent = _normalize_global_settings(cfg)
    auto_refresh_seconds = _normalize_auto_refresh_seconds(cfg)
    base_threshold = _resolve_base_threshold(cfg, target_conv_id)
    effective_threshold = resolve_effective_threshold(cfg, target_conv_id)
    warn_threshold = max(1, int(effective_threshold * warn_percent / 100))

    if transcript is not None:
        analysis = analyze_transcript(transcript)
    else:
        analysis = _empty_analysis(str(explicit_path) if explicit_path else "")

    effective_tokens = analysis["effectiveTokens"]
    model_window = resolve_model_window(analysis["model"], cfg)
    threshold_percent = (
        round((effective_tokens / effective_threshold) * 100.0, 1)
        if effective_threshold > 0
        else 0.0
    )
    window_percent = (
        round((effective_tokens / model_window) * 100.0, 1)
        if model_window > 0
        else 0.0
    )
    remaining = max(0, effective_threshold - effective_tokens)

    avg_delta = analysis["avgDeltaPerTurn"]
    if remaining <= 0:
        est_turns: int | None = 0
    elif avg_delta > 0:
        est_turns = remaining // avg_delta
    else:
        est_turns = None

    has_data = analysis["stepCount"] > 0
    status = _status_label(
        enabled=enabled,
        has_data=has_data,
        effective_tokens=effective_tokens,
        warn_threshold=warn_threshold,
        effective_threshold=effective_threshold,
    )

    meta_map = (
        _meta_map
        if _meta_map is not None
        else _collect_conv_metadata(
            get_brain_roots(),
            conv_ids=[target_conv_id] if target_conv_id else None,
        )
    )
    display_meta = _resolve_conv_display_meta(
        target_conv_id,
        str(analysis.get("firstUserPrompt") or ""),
        meta_map,
    )

    payload = dict(analysis)
    payload.update(
        {
            "view": "status",
            "conversationId": target_conv_id,
            "title": display_meta["title"],
            "agentName": display_meta["agentName"],
            "parentConversationId": display_meta["parentConversationId"],
            "isSubagent": display_meta["isSubagent"],
            "status": status,
            "enabled": enabled,
            "mode": mode,
            "warnPercent": warn_percent,
            "autoRefreshSeconds": auto_refresh_seconds,
            "baseThresholdTokens": base_threshold,
            "snoozedUntilTokens": snoozed_until,
            "effectiveThresholdTokens": effective_threshold,
            "warnThresholdTokens": warn_threshold,
            "modelWindowTokens": model_window,
            "thresholdPercent": threshold_percent,
            "windowPercent": window_percent,
            "remainingToThreshold": remaining,
            "estimatedTurnsRemaining": est_turns,
        }
    )
    return payload


def build_overview_payload(
    config: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Build multi-conversation overview payload across all discovered brain roots."""
    cfg = config if config is not None else load_config()
    enabled, mode, global_threshold, warn_percent = _normalize_global_settings(cfg)
    auto_refresh_seconds = _normalize_auto_refresh_seconds(cfg)

    recent = list_recent_transcripts(limit=limit)
    meta_map = _collect_conv_metadata(
        get_brain_roots(),
        conv_ids=[item["conversationId"] for item in recent],
    )
    summaries: list[dict[str, Any]] = []
    last_active_id = cfg.get("lastActiveConversationId")
    active_summary: dict[str, Any] | None = None

    for item in recent:
        cid = item["conversationId"]
        status_data = build_status_payload(
            conv_id=cid,
            explicit_path=item["transcriptPath"],
            config=cfg,
            _meta_map=meta_map,
        )
        summary = {
            "conversationId": cid,
            "title": status_data["title"],
            "agentName": status_data["agentName"],
            "parentConversationId": status_data["parentConversationId"],
            "isSubagent": status_data["isSubagent"],
            "sourceApp": item["sourceApp"],
            "model": status_data["model"],
            "effectiveTokens": status_data["effectiveTokens"],
            "thresholdTokens": status_data["effectiveThresholdTokens"],
            "thresholdPercent": status_data["thresholdPercent"],
            "status": status_data["status"],
            "compactionCount": status_data["compactionCount"],
            "mtime": item["mtime"],
            "transcriptPath": item["transcriptPath"],
        }
        summaries.append(summary)
        if last_active_id and cid == last_active_id:
            active_summary = summary

    if active_summary is None and summaries:
        active_summary = summaries[0]

    return {
        "view": "overview",
        "enabled": enabled,
        "mode": mode,
        "thresholdTokens": global_threshold,
        "warnPercent": warn_percent,
        "autoRefreshSeconds": auto_refresh_seconds,
        "conversations": summaries,
        "active": active_summary,
    }


def build_handoff_brief(
    conv_id: str | None = None,
    explicit_path: str | None = None,
    config: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Build a structured continuation brief for handing off to a fresh conversation."""
    status = build_status_payload(
        conv_id=conv_id,
        explicit_path=explicit_path,
        config=config,
    )
    target_conv_id = status["conversationId"]
    effective_tokens = status["effectiveTokens"]
    threshold_tokens = status["effectiveThresholdTokens"]

    conv_title = str(status.get("title") or "").strip()
    if conv_title and conv_title != target_conv_id:
        prev_label = f"{conv_title} (`{target_conv_id or 'unknown'}`)"
    else:
        prev_label = f"`{target_conv_id or 'unknown'}`"

    lines: list[str] = [
        "# Session Continuation Handoff",
        "",
        f"- **Previous Conversation:** {prev_label}",
        (
            f"- **Context Usage:** {effective_tokens:,} / {threshold_tokens:,} tokens "
            f"({status['thresholdPercent']}%)"
        ),
    ]
    if status["model"]:
        lines.append(f"- **Model:** `{status['model']}`")
    if status["compactionCount"] > 0:
        lines.append(f"- **Compactions Observed:** {status['compactionCount']}")

    first_prompt = status["firstUserPrompt"]
    if first_prompt:
        lines.extend(["", "## Original Goal", first_prompt])

    recent_prompts = status["recentUserPrompts"]
    if recent_prompts:
        lines.extend(["", "## Recent User Requests"])
        for prompt in recent_prompts:
            lines.append(f"- {prompt}")

    recent_files = status["recentFiles"]
    if recent_files:
        lines.extend(["", "## Recent Files Touched"])
        for fpath in recent_files:
            lines.append(f"- `{fpath}`")

    recent_commands = status["recentCommands"]
    if recent_commands:
        lines.extend(["", "## Recent Commands Run"])
        for cmd in recent_commands:
            lines.append(f"- `{cmd}`")

    clean_note = note.strip()
    if clean_note:
        lines.extend(["", "## Additional Notes", clean_note])

    lines.extend(
        [
            "",
            "## Next Step",
            "Continue from the context above. Verify relevant files and current workspace state before editing.",
        ]
    )

    return {
        "conversationId": target_conv_id,
        "effectiveTokens": effective_tokens,
        "thresholdTokens": threshold_tokens,
        "brief": "\n".join(lines),
    }


def _current_effective_tokens(config: dict[str, Any], conv_id: str) -> int:
    """Resolve current effective token count for conv_id from transcript or cached state."""
    transcript = find_transcript_path(conv_id=conv_id, config=config)
    if transcript is not None:
        return analyze_transcript(transcript)["effectiveTokens"]
    entry = _lookup_conversation(config, conv_id)
    return max(0, _safe_int(entry.get("lastTokens"), 0)) if entry is not None else 0


def _rearm_epochs_on_increase(
    config: dict[str, Any],
    old_thresholds: dict[str, int],
    *,
    target_conv_id: str | None,
    target_tokens: int | None,
) -> None:
    """Reset epoch dedup sentinels for conversations whose effective threshold rose."""
    convs = config.get("conversations")
    if not isinstance(convs, dict):
        return
    _, _, _, warn_percent = _normalize_global_settings(config)

    for cid, entry in convs.items():
        if not isinstance(entry, dict):
            continue
        old_effective = old_thresholds.get(cid)
        if old_effective is None:
            continue
        new_effective = resolve_effective_threshold(config, cid)
        if new_effective <= old_effective:
            continue

        entry["alertedAtEpoch"] = -1
        current_tokens = (
            target_tokens
            if cid == target_conv_id and target_tokens is not None
            else (
                _current_effective_tokens(config, cid)
                if cid == target_conv_id
                else max(0, _safe_int(entry.get("lastTokens"), 0))
            )
        )
        new_warn = max(1, int(new_effective * warn_percent / 100))
        if new_warn > current_tokens:
            entry["warnedAtEpoch"] = -1


def update_settings(
    config: dict[str, Any],
    *,
    threshold_tokens: int | None = None,
    warn_percent: int | None = None,
    mode: str | None = None,
    enabled: bool | None = None,
    auto_refresh_seconds: int | None = None,
    conv_id: str | None = None,
    custom_threshold_tokens: int | None = None,
    snooze_delta: int | None = None,
    snooze_until_tokens: int | None = None,
    reset_snooze: bool = False,
) -> dict[str, Any]:
    """Validate, update, and persist global or per-conversation Context Guard settings."""
    # Validate inputs before mutating config or touching disk.
    if mode is not None and mode not in VALID_MODES:
        raise ValueError(f"Invalid mode {mode!r}; expected one of {VALID_MODES}")
    if snooze_delta is not None and (isinstance(snooze_delta, bool) or _safe_int(snooze_delta, -1) < 0):
        raise ValueError("snooze_delta must be a non-negative integer")
    if snooze_until_tokens is not None and (
        isinstance(snooze_until_tokens, bool) or _safe_int(snooze_until_tokens, -1) < 0
    ):
        raise ValueError("snooze_until_tokens must be a non-negative integer")

    needs_conv = any(
        (
            custom_threshold_tokens is not None,
            snooze_delta is not None,
            snooze_until_tokens is not None,
            reset_snooze,
        )
    )
    target_conv_id: str | None = conv_id
    if needs_conv and not target_conv_id:
        last_active = config.get("lastActiveConversationId")
        if isinstance(last_active, str) and last_active.strip():
            target_conv_id = last_active
        else:
            recent = list_recent_transcripts(1)
            if recent:
                target_conv_id = recent[0]["conversationId"]
    if needs_conv and not target_conv_id:
        raise ValueError("conv_id is required when updating per-conversation settings")

    existing_convs = config.get("conversations")
    old_thresholds: dict[str, int] = (
        {
            cid: resolve_effective_threshold(config, cid)
            for cid in existing_convs
            if isinstance(cid, str)
        }
        if isinstance(existing_convs, dict)
        else {}
    )
    if target_conv_id and target_conv_id not in old_thresholds:
        old_thresholds[target_conv_id] = resolve_effective_threshold(config, target_conv_id)

    if threshold_tokens is not None and not isinstance(threshold_tokens, bool):
        config["thresholdTokens"] = max(MIN_THRESHOLD_TOKENS, _safe_int(threshold_tokens, DEFAULT_THRESHOLD_TOKENS))

    if warn_percent is not None and not isinstance(warn_percent, bool):
        config["warnPercent"] = max(1, min(99, _safe_int(warn_percent, DEFAULT_WARN_PERCENT)))

    if mode is not None:
        config["mode"] = mode

    if enabled is not None:
        config["enabled"] = bool(enabled)

    if auto_refresh_seconds is not None and not isinstance(auto_refresh_seconds, bool):
        config["autoRefreshSeconds"] = max(
            MIN_AUTO_REFRESH_SECONDS,
            min(
                MAX_AUTO_REFRESH_SECONDS,
                _safe_int(auto_refresh_seconds, DEFAULT_AUTO_REFRESH_SECONDS),
            ),
        )

    cached_target_tokens: int | None = None
    if needs_conv and target_conv_id:
        conv_state = get_conversation_state(config, target_conv_id)

        if custom_threshold_tokens is not None and not isinstance(custom_threshold_tokens, bool):
            parsed_custom = _safe_int(custom_threshold_tokens, 0)
            conv_state["customThresholdTokens"] = (
                max(MIN_THRESHOLD_TOKENS, parsed_custom) if parsed_custom > 0 else None
            )

        if reset_snooze:
            conv_state["snoozedUntilTokens"] = 0
            conv_state["alertedAtEpoch"] = -1

        if snooze_until_tokens is not None:
            conv_state["snoozedUntilTokens"] = max(0, _safe_int(snooze_until_tokens, 0))

        if snooze_delta is not None:
            delta_val = max(0, _safe_int(snooze_delta, 0))
            cached_target_tokens = _current_effective_tokens(config, target_conv_id)
            current_threshold = resolve_effective_threshold(config, target_conv_id)
            conv_state["snoozedUntilTokens"] = max(cached_target_tokens, current_threshold) + delta_val

    _rearm_epochs_on_increase(
        config,
        old_thresholds,
        target_conv_id=target_conv_id,
        target_tokens=cached_target_tokens,
    )

    save_config(config)
    return config
