#!/usr/bin/env python3
"""Startup context and customizations footprint inspector for Agy Lens.

Scans documented Antigravity global, plugin, and workspace paths for rules,
plugins, skills (separating always-loaded frontmatter from on-demand bodies),
MCP servers (strictly masking all env values, headers, and bearer/token flags),
and hooks, producing startup token estimates and diagnostic warnings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core as core

MAX_ANCESTOR_LEVELS = 8
MCP_BASELINE_TOKENS = 350
HEAVY_RULE_THRESHOLD_TOKENS = 2000
HIGH_MCP_COUNT_THRESHOLD = 8

_FRONTMATTER_RE = re.compile(r"^\ufeff?---[ \t]*\r?\n(.*?)\r?\n---(?:[ \t]*(?:\r?\n|$))", re.DOTALL)
_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*:[ \t]*(.*)$")

_SECRET_FLAG_WITH_VALUE = frozenset(
    {
        "-h",
        "--header",
        "--token",
        "--api-key",
        "--apikey",
        "--secret",
        "--password",
        "--auth",
        "--bearer",
        "--credential",
        "--credentials",
    }
)
_HEADER_FLAGS = frozenset({"-h", "--header"})
_SECRET_KV_RE = re.compile(
    r"^(--?(?:header|h|token|api[-_]?key|apikey|secret|password|auth|bearer|credentials?))=(.+)$",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"^(Authorization\s*:\s*)(.+)$", re.IGNORECASE)
_URL_CREDS_RE = re.compile(r"(https?://)([^/@\s]+@)", re.IGNORECASE)
_URL_QUERY_SECRET_RE = re.compile(
    r"([?&](?:token|key|api[-_]?key|apikey|secret|password|auth)=)([^&\s]+)",
    re.IGNORECASE,
)
_INLINE_SECRET_ENV_RE = re.compile(
    r"^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS|PASSWORD|AUTH|CREDENTIAL)[A-Z0-9_]*)=(.+)$"
)
_HOOK_META_KEYS = frozenset({"enabled", "disabled", "name", "description", "$schema"})


def _estimate_tokens(text_or_bytes: str | int) -> int:
    """Estimate token count using the deterministic bytes/4 heuristic."""
    if isinstance(text_or_bytes, bool):
        byte_len = 0
    elif isinstance(text_or_bytes, int):
        byte_len = max(0, text_or_bytes)
    else:
        byte_len = len(str(text_or_bytes).encode("utf-8"))
    if byte_len <= 0:
        return 0
    return max(1, round(byte_len / 4))


def _unquote_yaml_scalar(value: str) -> str:
    """Strip matching surrounding single or double quotes from a YAML scalar."""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1].strip()
    return stripped


def _parse_skill_frontmatter(raw_text: str) -> tuple[str, str, int, int]:
    """Parse YAML frontmatter name and description using stdlib regex/line parsing.

    Returns ``(name, description, frontmatter_tokens, total_body_tokens)``.
    """
    total_body_tokens = _estimate_tokens(raw_text)
    match = _FRONTMATTER_RE.match(raw_text)
    if not match:
        return ("", "", 0, total_body_tokens)

    fields: dict[str, str] = {}
    current_key: str | None = None
    block_mode: str | None = None
    block_lines: list[str] = []

    def _flush_block() -> None:
        nonlocal current_key, block_mode, block_lines
        if current_key in ("name", "description"):
            if block_mode and block_mode.startswith("|"):
                joined = "\n".join(line.strip() for line in block_lines if line.strip())
            else:
                joined = " ".join(line.strip() for line in block_lines if line.strip())
            if joined:
                existing = fields.get(current_key, "")
                fields[current_key] = f"{existing} {joined}".strip() if existing else joined
        current_key = None
        block_mode = None
        block_lines = []

    for line in match.group(1).splitlines():
        if line and line[0] in (" ", "\t"):
            if current_key in ("name", "description"):
                block_lines.append(line)
            continue

        _flush_block()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key_match = _YAML_KEY_RE.match(stripped)
        if not key_match:
            continue

        key = key_match.group(1).lower()
        val = key_match.group(2).strip()
        if key not in ("name", "description"):
            continue

        if val in (">", ">-", "|", "|-"):
            current_key = key
            block_mode = val
        elif val:
            fields[key] = _unquote_yaml_scalar(val)
            current_key = key
            block_mode = "plain"
        else:
            current_key = key
            block_mode = "plain"

    _flush_block()

    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    fm_text = "\n".join(part for part in (name, description) if part)
    frontmatter_tokens = _estimate_tokens(fm_text)
    return (name, description, frontmatter_tokens, total_body_tokens)


def _resolve_path_str(path: Path) -> str:
    """Resolve a path to a canonical string without raising on broken symlinks."""
    try:
        return str(path.resolve())
    except OSError:
        return os.path.abspath(str(path))


def _read_bytes_safe(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _read_text_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_json_safe(path: Path) -> Any:
    raw = _read_text_safe(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _valid_dir_path(candidate: Any) -> Path | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    try:
        path = Path(os.path.expanduser(candidate.strip()))
        if path.is_dir():
            return path
    except (OSError, ValueError):
        return None
    return None


def _extract_workspace_from_obj(obj: Any) -> Path | None:
    """Extract an existing workspace directory from transcript or state metadata."""
    if not isinstance(obj, dict):
        return None
    for key in ("cwd", "workspacePath", "workspace_path"):
        found = _valid_dir_path(obj.get(key))
        if found is not None:
            return found
    for list_key in ("workspacePaths", "workspace_paths"):
        items = obj.get(list_key)
        if isinstance(items, list):
            for item in items:
                found = _valid_dir_path(item)
                if found is not None:
                    return found
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        return _extract_workspace_from_obj(metadata)
    return None


def _scan_transcript_for_workspace(transcript_path: Path) -> Path | None:
    """Inspect initial and tail JSONL records of a transcript for workspace hints."""
    raw = _read_text_safe(transcript_path)
    if not raw:
        return None
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return None
    sample = lines[:10] + (lines[-10:] if len(lines) > 10 else [])
    for line in sample:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        found = _extract_workspace_from_obj(entry)
        if found is not None:
            return found
    return None


def _infer_workspace_from_transcripts(config: dict[str, Any] | None) -> Path | None:
    """Infer workspace root from config conversations or recent Antigravity transcripts."""
    cfg = config if config is not None else core.load_config()
    if isinstance(cfg, dict):
        convs = cfg.get("conversations")
        if isinstance(convs, dict):
            active_id = cfg.get("activeConversationId") or cfg.get("lastActiveConversationId")
            if isinstance(active_id, str) and active_id in convs:
                found = _extract_workspace_from_obj(convs[active_id])
                if found is not None:
                    return found
            for entry in convs.values():
                found = _extract_workspace_from_obj(entry)
                if found is not None:
                    return found

    candidates: list[Path] = []
    explicit_tx = core.find_transcript_path(config=cfg)
    if explicit_tx is not None:
        candidates.append(explicit_tx)
    for item in core.list_recent_transcripts(limit=3):
        tx_str = item.get("transcriptPath")
        if isinstance(tx_str, str) and tx_str:
            p = Path(tx_str)
            if p not in candidates:
                candidates.append(p)

    for tx_path in candidates:
        found = _scan_transcript_for_workspace(tx_path)
        if found is not None:
            return found
    return None


def _resolve_workspace_root(
    workspace_path: str | None,
    config: dict[str, Any] | None,
) -> Path:
    """Resolve workspace root: explicit arg -> AGY_LENS_WORKSPACE -> transcript -> cwd."""
    if workspace_path is not None and str(workspace_path).strip():
        return Path(_resolve_path_str(Path(os.path.expanduser(str(workspace_path).strip()))))

    env_ws = os.environ.get("AGY_LENS_WORKSPACE", "").strip()
    if env_ws:
        return Path(_resolve_path_str(Path(os.path.expanduser(env_ws))))

    inferred = _infer_workspace_from_transcripts(config)
    if inferred is not None:
        return Path(_resolve_path_str(inferred))

    return Path(_resolve_path_str(Path(os.getcwd())))


def _collect_workspace_ancestors(workspace_root: Path) -> list[Path]:
    """Walk upward up to MAX_ANCESTOR_LEVELS stopping after .git, ordered root-to-leaf."""
    chain: list[Path] = []
    cur = workspace_root
    for _ in range(MAX_ANCESTOR_LEVELS):
        chain.append(cur)
        try:
            if (cur / ".git").exists():
                break
        except OSError:
            pass
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    chain.reverse()
    return chain


def _load_plugins_overrides(plugins_json_path: Path) -> dict[str, bool]:
    """Parse plugins.json disabled/enabled overrides into a {plugin_id: enabled} map."""
    data = _read_json_safe(plugins_json_path)
    if not isinstance(data, dict):
        return {}

    overrides: dict[str, bool] = {}
    disabled_list = data.get("disabled")
    if isinstance(disabled_list, list):
        for item in disabled_list:
            if isinstance(item, str) and item.strip():
                overrides[item.strip()] = False

    nested = data.get("plugins")
    entries = nested if isinstance(nested, dict) else data
    for key, val in entries.items():
        if key in ("disabled", "plugins", "entries", "inherits", "$schema"):
            continue
        if isinstance(val, bool):
            overrides[str(key)] = val
        elif isinstance(val, dict):
            if val.get("disabled") is True:
                overrides[str(key)] = False
            elif "enabled" in val and isinstance(val["enabled"], bool):
                overrides[str(key)] = val["enabled"]
    return overrides


def _load_config_json_plugin_overrides(config_json_path: Path) -> dict[str, bool]:
    """Extract {plugin_id: enabled} overrides from Antigravity config.json['plugins']."""
    data = _read_json_safe(config_json_path)
    if not isinstance(data, dict):
        return {}
    plugins_map = data.get("plugins")
    if not isinstance(plugins_map, dict):
        return {}
    overrides: dict[str, bool] = {}
    for key, val in plugins_map.items():
        if isinstance(val, bool):
            overrides[str(key)] = val
        elif isinstance(val, dict):
            if val.get("disabled") is True:
                overrides[str(key)] = False
            elif "enabled" in val and isinstance(val["enabled"], bool):
                overrides[str(key)] = val["enabled"]
    return overrides


def _count_files(directory: Path, pattern: str) -> int:
    try:
        if not directory.is_dir():
            return 0
        return sum(1 for p in directory.glob(pattern) if p.is_file())
    except OSError:
        return 0


def _scan_plugins_dir(
    plugins_root: Path,
    scope: str,
    overrides: dict[str, bool],
    plugins_out: list[tuple[dict[str, Any], Path]],
    seen_dirs: set[str],
) -> None:
    """Scan `<plugins_root>/*/plugin.json` and record plugin descriptors."""
    try:
        if not plugins_root.is_dir():
            return
        subdirs = sorted((p for p in plugins_root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return

    for plugin_dir in subdirs:
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.is_file():
            continue
        dir_key = _resolve_path_str(plugin_dir)
        if dir_key in seen_dirs:
            continue
        seen_dirs.add(dir_key)

        data = _read_json_safe(manifest_path)
        manifest = data if isinstance(data, dict) else {}
        plugin_id = plugin_dir.name
        plugin_name = str(manifest.get("name") or plugin_id)
        version = str(manifest.get("version") or "")

        enabled = True
        if manifest.get("disabled") is True or manifest.get("enabled") is False:
            enabled = False
        if plugin_id in overrides:
            enabled = overrides[plugin_id]
        elif plugin_name in overrides:
            enabled = overrides[plugin_name]

        entry = {
            "id": plugin_id,
            "name": plugin_name,
            "version": version,
            "enabled": enabled,
            "scope": scope,
            "path": _resolve_path_str(plugin_dir),
            "hasMcp": (plugin_dir / "mcp_config.json").is_file(),
            "hasHooks": (plugin_dir / "hooks.json").is_file(),
            "skillsCount": _count_files(plugin_dir / "skills", "*/SKILL.md"),
            "rulesCount": _count_files(plugin_dir / "rules", "*.md"),
        }
        plugins_out.append((entry, plugin_dir))


def _add_rule(
    rules_out: list[dict[str, Any]],
    seen_paths: set[str],
    path: Path,
    name: str,
    scope: str,
) -> None:
    """Read a rule file and append its normalized entry if readable and unseen."""
    try:
        if not path.is_file():
            return
    except OSError:
        return
    resolved = _resolve_path_str(path)
    if resolved in seen_paths:
        return
    raw_bytes = _read_bytes_safe(path)
    if raw_bytes is None:
        return
    seen_paths.add(resolved)
    byte_len = len(raw_bytes)
    rules_out.append(
        {
            "name": name,
            "scope": scope,
            "path": resolved,
            "bytes": byte_len,
            "estTokens": _estimate_tokens(byte_len),
            "alwaysLoaded": True,
        }
    )


def _scan_rules_glob(
    rules_dir: Path,
    scope: str,
    rules_out: list[dict[str, Any]],
    seen_paths: set[str],
) -> None:
    try:
        if not rules_dir.is_dir():
            return
        files = sorted((p for p in rules_dir.glob("*.md") if p.is_file()), key=lambda p: p.name)
    except OSError:
        return
    for md_file in files:
        _add_rule(rules_out, seen_paths, md_file, md_file.name, scope)


def _matches_any_pattern(name: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return False
    for raw in patterns:
        if not isinstance(raw, str) or not raw:
            continue
        try:
            if re.search(raw, name):
                return True
        except re.error:
            if raw == name:
                return True
    return False


def _scan_skills_dir(
    skills_root: Path,
    scope: str,
    source: str,
    skills_out: list[dict[str, Any]],
    seen_paths: set[str],
    *,
    include_only: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    """Scan `<skills_root>/*/SKILL.md` for non-builtin skills."""
    try:
        if not skills_root.is_dir():
            return
        skill_dirs = sorted((p for p in skills_root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return

    for skill_dir in skill_dirs:
        if include_only and not _matches_any_pattern(skill_dir.name, include_only):
            continue
        if exclude and _matches_any_pattern(skill_dir.name, exclude):
            continue
        skill_md = skill_dir / "SKILL.md"
        try:
            if not skill_md.is_file():
                continue
        except OSError:
            continue
        path_key = _resolve_path_str(skill_md)
        if path_key in seen_paths:
            continue
        raw_text = _read_text_safe(skill_md)
        if raw_text is None:
            continue
        seen_paths.add(path_key)

        fm_name, description, fm_tokens, body_tokens = _parse_skill_frontmatter(raw_text)
        skills_out.append(
            {
                "name": fm_name or skill_dir.name,
                "description": description,
                "scope": scope,
                "source": source,
                "path": path_key,
                "frontmatterTokens": fm_tokens,
                "bodyTokens": body_tokens,
                "enabled": True,
            }
        )


def _resolve_manifest_path(raw_path: str, base_dir: Path, fallback_dir: Path) -> Path | None:
    candidate_str = raw_path.strip()
    if not candidate_str:
        return None
    if candidate_str.startswith("~/") or candidate_str == "~":
        return Path(os.path.expanduser(candidate_str))
    p = Path(candidate_str)
    if p.is_absolute():
        return p
    rel_base = base_dir / p
    if rel_base.exists():
        return rel_base
    return fallback_dir / p


def _scan_skills_manifest(
    manifest_path: Path,
    scope: str,
    fallback_dir: Path,
    skills_out: list[dict[str, Any]],
    seen_paths: set[str],
    *,
    visited_manifests: set[str] | None = None,
    include_only: list[str] | None = None,
    exclude: list[str] | None = None,
    depth: int = 0,
) -> None:
    """Scan declared skill paths in `skills.json` (with `entries` and `inherits`)."""
    if depth > 4:
        return
    try:
        if not manifest_path.is_file():
            return
    except OSError:
        return
    resolved_manifest = _resolve_path_str(manifest_path)
    visited = visited_manifests if visited_manifests is not None else set()
    if resolved_manifest in visited:
        return
    visited.add(resolved_manifest)

    data = _read_json_safe(manifest_path)
    if not isinstance(data, dict):
        return

    base_dir = manifest_path.parent
    raw_inherits = data.get("inherits")
    if isinstance(raw_inherits, list):
        for item in raw_inherits:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            target = _resolve_manifest_path(item["path"], base_dir, fallback_dir)
            if target is None:
                continue
            child_inc = [str(x) for x in item.get("include_only", []) if isinstance(x, str)] if isinstance(item.get("include_only"), list) else None
            child_exc = list(exclude or [])
            if isinstance(item.get("exclude"), list):
                child_exc.extend(str(x) for x in item["exclude"] if isinstance(x, str))
            _scan_skills_manifest(
                target,
                scope,
                fallback_dir,
                skills_out,
                seen_paths,
                visited_manifests=visited,
                include_only=child_inc or include_only,
                exclude=child_exc or None,
                depth=depth + 1,
            )

    raw_entries = data.get("entries")
    if isinstance(raw_entries, list):
        for item in raw_entries:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            target_dir = _resolve_manifest_path(item["path"], base_dir, fallback_dir)
            if target_dir is None:
                continue
            entry_inc = [str(x) for x in item.get("include_only", []) if isinstance(x, str)] if isinstance(item.get("include_only"), list) else include_only
            entry_exc = list(exclude or [])
            if isinstance(item.get("exclude"), list):
                entry_exc.extend(str(x) for x in item["exclude"] if isinstance(x, str))
            _scan_skills_dir(
                target_dir,
                scope,
                manifest_path.name,
                skills_out,
                seen_paths,
                include_only=entry_inc,
                exclude=entry_exc or None,
            )


def _scan_builtin_skills(builtin_root: Path) -> dict[str, int]:
    """Aggregate builtin `SKILL.md` frontmatter and on-demand token totals."""
    summary = {"count": 0, "frontmatterTokens": 0, "onDemandTokens": 0}
    try:
        if not builtin_root.is_dir():
            return summary
        skill_dirs = sorted((p for p in builtin_root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return summary

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        try:
            if not skill_md.is_file():
                continue
        except OSError:
            continue
        raw_text = _read_text_safe(skill_md)
        if raw_text is None:
            continue
        _, _, fm_tokens, body_tokens = _parse_skill_frontmatter(raw_text)
        summary["count"] += 1
        summary["frontmatterTokens"] += fm_tokens
        summary["onDemandTokens"] += body_tokens
    return summary


def _sanitize_arg(arg: str) -> str:
    """Mask inline tokens, bearer credentials, and URL userinfo inside a single arg."""
    kv_match = _SECRET_KV_RE.match(arg)
    if kv_match:
        return f"{kv_match.group(1)}=[REDACTED]"
    env_match = _INLINE_SECRET_ENV_RE.match(arg)
    if env_match:
        return f"{env_match.group(1)}=[REDACTED]"
    if _AUTH_HEADER_RE.match(arg):
        return _AUTH_HEADER_RE.sub(r"\1[REDACTED]", arg)
    sanitized = _BEARER_RE.sub(r"\1[REDACTED]", arg)
    sanitized = _URL_CREDS_RE.sub(r"\1[REDACTED]@", sanitized)
    sanitized = _URL_QUERY_SECRET_RE.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def _sanitize_command_preview(entry: dict[str, Any]) -> str:
    """Build a secret-free command preview from an MCP server definition."""
    command = str(entry.get("command") or "").strip()
    raw_args = entry.get("args")
    args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    if not command:
        raw_url = str(entry.get("serverUrl") or entry.get("url") or "").strip()
        if raw_url:
            cleaned = _URL_CREDS_RE.sub(r"\1[REDACTED]@", raw_url)
            return cleaned.split("?", 1)[0]
        return ""

    parts: list[str] = [_sanitize_arg(command)]
    skip_next = False
    redact_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if redact_next:
            parts.append("[REDACTED]")
            redact_next = False
            continue

        lowered = arg.strip().lower()
        if lowered in _HEADER_FLAGS:
            skip_next = True
            continue
        if lowered in _SECRET_FLAG_WITH_VALUE:
            parts.append(arg)
            redact_next = True
            continue
        if _SECRET_KV_RE.match(arg) and arg.split("=", 1)[0].lower() in _HEADER_FLAGS:
            continue

        parts.append(_sanitize_arg(arg))
    return " ".join(parts)


def _scan_mcp_config(
    mcp_path: Path,
    scope: str,
    source: str,
    servers_out: list[dict[str, Any]],
    seen_paths: set[str],
) -> None:
    """Scan an mcp_config.json file while strictly omitting all env and header values."""
    try:
        if not mcp_path.is_file():
            return
    except OSError:
        return
    resolved = _resolve_path_str(mcp_path)
    if resolved in seen_paths:
        return
    data = _read_json_safe(mcp_path)
    if not isinstance(data, dict):
        return
    seen_paths.add(resolved)

    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return

    for name, raw_entry in mcp_servers.items():
        if not isinstance(raw_entry, dict):
            continue
        disabled = bool(raw_entry.get("disabled", False)) or (raw_entry.get("enabled") is False)
        enabled = not disabled

        raw_env = raw_entry.get("env")
        if isinstance(raw_env, dict):
            env_keys = sorted(str(k) for k in raw_env.keys())
        else:
            env_keys = []

        servers_out.append(
            {
                "name": str(name),
                "scope": scope,
                "source": source,
                "enabled": enabled,
                "commandPreview": _sanitize_command_preview(raw_entry),
                "envKeys": env_keys,
                "envKeyCount": len(env_keys),
                "estTokens": MCP_BASELINE_TOKENS if enabled else 0,
                "path": resolved,
            }
        )


def _extract_event_timeout(handlers: list[Any]) -> int:
    """Return max positive integer timeout across flat or grouped hook entries."""
    max_timeout = 0
    for item in handlers:
        if not isinstance(item, dict):
            continue
        t = item.get("timeout")
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > max_timeout:
            max_timeout = int(t)
        nested = item.get("hooks")
        if isinstance(nested, list):
            nested_timeout = _extract_event_timeout(nested)
            if nested_timeout > max_timeout:
                max_timeout = nested_timeout
    return max_timeout


def _extract_events_and_timeout(group_obj: dict[str, Any]) -> tuple[list[str], int]:
    """Collect hook event names and max timeoutSeconds from a hook group dictionary."""
    events: list[str] = []
    max_timeout = 0
    for key, val in group_obj.items():
        if key in _HOOK_META_KEYS or not isinstance(val, list):
            continue
        events.append(str(key))
        t = _extract_event_timeout(val)
        if t > max_timeout:
            max_timeout = t
    return (events, max_timeout)


def _scan_hooks_config(
    hooks_path: Path,
    scope: str,
    hooks_out: list[dict[str, Any]],
    seen_paths: set[str],
    default_name: str,
) -> None:
    """Parse a hooks.json file supporting named-group and event-keyed formats."""
    try:
        if not hooks_path.is_file():
            return
    except OSError:
        return
    resolved = _resolve_path_str(hooks_path)
    if resolved in seen_paths:
        return
    data = _read_json_safe(hooks_path)
    if not isinstance(data, dict):
        return
    seen_paths.add(resolved)

    if isinstance(data.get("hooks"), dict):
        events, timeout_s = _extract_events_and_timeout(data["hooks"])
        if events:
            enabled = not bool(data.get("disabled", False)) and (data.get("enabled", True) is not False)
            hooks_out.append(
                {
                    "name": str(data.get("name") or default_name),
                    "scope": scope,
                    "enabled": enabled,
                    "events": events,
                    "timeoutSeconds": timeout_s,
                    "path": resolved,
                }
            )
        return

    direct_events, direct_timeout = _extract_events_and_timeout(data)
    if direct_events:
        enabled = not bool(data.get("disabled", False)) and (data.get("enabled", True) is not False)
        hooks_out.append(
            {
                "name": str(data.get("name") or default_name),
                "scope": scope,
                "enabled": enabled,
                "events": direct_events,
                "timeoutSeconds": direct_timeout,
                "path": resolved,
            }
        )
        return

    for group_name, group_val in data.items():
        if group_name in _HOOK_META_KEYS or not isinstance(group_val, dict):
            continue
        events, timeout_s = _extract_events_and_timeout(group_val)
        if not events:
            continue
        enabled = not bool(group_val.get("disabled", False)) and (group_val.get("enabled", True) is not False)
        hooks_out.append(
            {
                "name": str(group_name),
                "scope": scope,
                "enabled": enabled,
                "events": events,
                "timeoutSeconds": timeout_s,
                "path": resolved,
            }
        )


def _build_warnings(
    *,
    plugins: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    mcp_servers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build diagnostic warnings for duplicate plugins, heavy rules, skill overrides, and MCP count."""
    warnings: list[dict[str, Any]] = []

    global_enabled = {
        p["id"]: p
        for p in plugins
        if p.get("scope") == "global" and p.get("enabled")
    }
    legacy_guard = global_enabled.get("agy-context-guard") or next(
        (p for p in global_enabled.values() if p.get("name") == "agy-context-guard"),
        None,
    )
    has_lens = "agy-lens" in global_enabled or any(
        p.get("name") == "agy-lens" for p in global_enabled.values()
    )
    if legacy_guard is not None and has_lens:
        warnings.append(
            {
                "code": "duplicate_plugin",
                "severity": "warning",
                "title": "Duplicate context plugin installed",
                "detail": (
                    "Both agy-context-guard and agy-lens are enabled. "
                    "Run `agy plugin uninstall agy-context-guard` to avoid duplicate hooks and context overhead."
                ),
                "path": legacy_guard["path"],
            }
        )

    for rule in rules:
        if rule["estTokens"] > HEAVY_RULE_THRESHOLD_TOKENS:
            warnings.append(
                {
                    "code": "rule_heavy",
                    "severity": "warning",
                    "title": f"Heavy rule file: {rule['name']}",
                    "detail": (
                        f"Rule estimates ~{rule['estTokens']} always-loaded tokens "
                        f"(threshold: {HEAVY_RULE_THRESHOLD_TOKENS})."
                    ),
                    "path": rule["path"],
                }
            )

    skills_by_name: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        skills_by_name.setdefault(skill["name"], []).append(skill)
    for skill_name, matches in skills_by_name.items():
        if len(matches) > 1:
            sources = ", ".join(f"{m['scope']} ({m['source']})" for m in matches)
            warnings.append(
                {
                    "code": "skill_override",
                    "severity": "info",
                    "title": f"Skill override: {skill_name}",
                    "detail": f"Multiple definitions found across {sources}.",
                    "path": matches[-1]["path"],
                }
            )

    enabled_mcps = [m for m in mcp_servers if m.get("enabled")]
    if len(enabled_mcps) > HIGH_MCP_COUNT_THRESHOLD:
        warnings.append(
            {
                "code": "high_mcp_count",
                "severity": "warning",
                "title": f"High active MCP server count ({len(enabled_mcps)})",
                "detail": (
                    f"{len(enabled_mcps)} enabled MCP servers add "
                    f"~{len(enabled_mcps) * MCP_BASELINE_TOKENS} estimated startup tokens."
                ),
                "path": enabled_mcps[0]["path"],
            }
        )

    return warnings


def inspect_footprint(
    *,
    workspace_path: str | None = None,
    config: dict[str, Any] | None = None,
    observed_startup_tokens: int | None = None,
) -> dict[str, Any]:
    """Scan global, plugin, and workspace Antigravity customizations and token footprint."""
    home = Path.home()
    gemini_dir = home / ".gemini"
    gemini_cfg_dir = gemini_dir / "config"

    workspace_root = _resolve_workspace_root(workspace_path, config)
    ancestors = _collect_workspace_ancestors(workspace_root)
    home_resolved = _resolve_path_str(home)

    # 1. Plugins (global + workspace .agents/plugins)
    plugin_records: list[tuple[dict[str, Any], Path]] = []
    seen_plugin_dirs: set[str] = set()
    global_overrides = _load_config_json_plugin_overrides(gemini_cfg_dir / "config.json")
    global_overrides.update(_load_plugins_overrides(gemini_cfg_dir / "plugins.json"))
    _scan_plugins_dir(
        gemini_cfg_dir / "plugins",
        "global",
        global_overrides,
        plugin_records,
        seen_plugin_dirs,
    )
    for ancestor in ancestors:
        if _resolve_path_str(ancestor) == home_resolved:
            continue
        ws_overrides = _load_plugins_overrides(ancestor / ".agents" / "plugins.json")
        _scan_plugins_dir(
            ancestor / ".agents" / "plugins",
            "workspace",
            ws_overrides,
            plugin_records,
            seen_plugin_dirs,
        )

    plugins = [entry for entry, _ in plugin_records]
    active_plugins = [(entry, pdir) for entry, pdir in plugin_records if entry["enabled"]]

    # 2. Rules (global -> active plugins -> workspace ancestors root-to-leaf)
    rules: list[dict[str, Any]] = []
    seen_rule_paths: set[str] = set()
    _add_rule(rules, seen_rule_paths, gemini_dir / "GEMINI.md", "GEMINI.md", "global")
    _add_rule(rules, seen_rule_paths, gemini_dir / "AGENTS.md", "AGENTS.md", "global")
    _scan_rules_glob(gemini_cfg_dir / "rules", "global", rules, seen_rule_paths)

    for _, plugin_dir in active_plugins:
        _scan_rules_glob(plugin_dir / "rules", "plugin", rules, seen_rule_paths)

    for ancestor in ancestors:
        _add_rule(rules, seen_rule_paths, ancestor / "GEMINI.md", "GEMINI.md", "workspace")
        _add_rule(rules, seen_rule_paths, ancestor / "AGENTS.md", "AGENTS.md", "workspace")
        if _resolve_path_str(ancestor) != home_resolved:
            _scan_rules_glob(ancestor / ".agents" / "rules", "workspace", rules, seen_rule_paths)
            _add_rule(
                rules,
                seen_rule_paths,
                ancestor / ".agents" / "rules.json",
                "rules.json",
                "workspace",
            )

    # 3. Skills (global -> active plugins -> workspace ancestors; builtin aggregated)
    skills: list[dict[str, Any]] = []
    seen_skill_paths: set[str] = set()
    _scan_skills_dir(
        gemini_cfg_dir / "skills",
        "global",
        "~/.gemini/config/skills",
        skills,
        seen_skill_paths,
    )
    _scan_skills_dir(
        home / ".agents" / "skills",
        "global",
        "~/.agents/skills",
        skills,
        seen_skill_paths,
    )
    _scan_skills_manifest(
        gemini_cfg_dir / "skills.json",
        "global",
        gemini_cfg_dir,
        skills,
        seen_skill_paths,
    )
    for plugin_entry, plugin_dir in active_plugins:
        _scan_skills_dir(
            plugin_dir / "skills",
            "plugin",
            plugin_entry["id"],
            skills,
            seen_skill_paths,
        )
    for ancestor in ancestors:
        if _resolve_path_str(ancestor) == home_resolved:
            continue
        _scan_skills_dir(
            ancestor / ".agents" / "skills",
            "workspace",
            ancestor.name or str(ancestor),
            skills,
            seen_skill_paths,
        )
        _scan_skills_manifest(
            ancestor / ".agents" / "skills.json",
            "workspace",
            workspace_root,
            skills,
            seen_skill_paths,
        )

    builtin_skills_summary = _scan_builtin_skills(
        gemini_dir / "antigravity" / "builtin" / "skills"
    )

    # 4. MCP Servers (global -> active plugins -> workspace ancestors)
    mcp_servers: list[dict[str, Any]] = []
    seen_mcp_paths: set[str] = set()
    _scan_mcp_config(
        gemini_cfg_dir / "mcp_config.json",
        "global",
        "~/.gemini/config/mcp_config.json",
        mcp_servers,
        seen_mcp_paths,
    )
    for plugin_entry, plugin_dir in active_plugins:
        _scan_mcp_config(
            plugin_dir / "mcp_config.json",
            "plugin",
            plugin_entry["id"],
            mcp_servers,
            seen_mcp_paths,
        )
    for ancestor in ancestors:
        if _resolve_path_str(ancestor) == home_resolved:
            continue
        _scan_mcp_config(
            ancestor / ".agents" / "mcp_config.json",
            "workspace",
            ancestor.name or str(ancestor),
            mcp_servers,
            seen_mcp_paths,
        )

    # 5. Hooks (global -> active plugins -> workspace ancestors)
    hooks: list[dict[str, Any]] = []
    seen_hook_paths: set[str] = set()
    _scan_hooks_config(
        gemini_cfg_dir / "hooks.json",
        "global",
        hooks,
        seen_hook_paths,
        default_name="global",
    )
    for plugin_entry, plugin_dir in active_plugins:
        _scan_hooks_config(
            plugin_dir / "hooks.json",
            "plugin",
            hooks,
            seen_hook_paths,
            default_name=plugin_entry["id"],
        )
    for ancestor in ancestors:
        if _resolve_path_str(ancestor) == home_resolved:
            continue
        _scan_hooks_config(
            ancestor / ".agents" / "hooks.json",
            "workspace",
            hooks,
            seen_hook_paths,
            default_name=ancestor.name or "workspace",
        )

    estimated_startup_tokens = (
        sum(r["estTokens"] for r in rules)
        + sum(s["frontmatterTokens"] for s in skills if s.get("enabled", True))
        + builtin_skills_summary["frontmatterTokens"]
        + sum(m["estTokens"] for m in mcp_servers if m.get("enabled", True))
    )
    on_demand_skills_tokens = (
        sum(s["bodyTokens"] for s in skills if s.get("enabled", True))
        + builtin_skills_summary["onDemandTokens"]
    )

    warnings = _build_warnings(
        plugins=plugins,
        rules=rules,
        skills=skills,
        mcp_servers=mcp_servers,
    )

    return {
        "workspacePath": str(workspace_root),
        "summary": {
            "estimatedStartupTokens": estimated_startup_tokens,
            "onDemandSkillsTokens": on_demand_skills_tokens,
            "observedStartupTokens": observed_startup_tokens,
            "rulesCount": len(rules),
            "pluginsCount": len(plugins),
            "skillsCount": len(skills),
            "builtinSkillsCount": builtin_skills_summary["count"],
            "mcpServersCount": len(mcp_servers),
            "hooksCount": len(hooks),
        },
        "builtinSkillsSummary": builtin_skills_summary,
        "warnings": warnings,
        "rules": rules,
        "plugins": plugins,
        "skills": skills,
        "mcpServers": mcp_servers,
        "hooks": hooks,
    }
