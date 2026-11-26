#!/usr/bin/env python3
"""Cross-chat full-text search index and query engine for agy-lens.

Indexes Antigravity conversation transcripts (`.system_generated/logs/transcript.jsonl`)
and non-hidden markdown artifacts (`*.md`, `sdd/*.md`, etc.) across all discovered
brain roots into an mtime-fingerprinted JSON cache, and executes ranked multi-term
and quoted-phrase searches with exact character-offset highlight spans.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterator

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_core as core

CACHE_VERSION = 1
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_TRANSCRIPT_LINE_BYTES = 200 * 1024
MAX_CHUNK_CHARS = 1500
MAX_CHUNKS_PER_CHAT = 120
MAX_ARTIFACT_CHUNKS_RESERVED = 40
MAX_EDITED_FILES = 20
MAX_SNIPPETS_PER_CHAT = 3
MAX_EMPTY_PREVIEW_SNIPPETS = 2
SNIPPET_TARGET_CHARS = 140
DEFAULT_SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 50

VALID_SCOPES = ("all", "messages", "artifacts", "files")
_MESSAGE_SOURCES = frozenset({"user", "assistant", "tool"})
_EDIT_TOOL_HINTS = ("edit", "write", "replace", "create", "patch")

_BASE64_DATA_URI_RE = re.compile(r"data:[^;\s\"']+;base64,[A-Za-z0-9+/=_-]+")
_QUERY_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')


def get_search_cache_path() -> Path:
    """Return search cache path, honoring AGY_LENS_SEARCH_CACHE_PATH override."""
    override = os.environ.get("AGY_LENS_SEARCH_CACHE_PATH", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return core.get_config_path().parent / "agy-lens-search-cache.json"


def _strip_base64_and_clean(text: str, max_len: int | None = None) -> str:
    """Strip base64 data URIs, normalize whitespace, and optionally truncate."""
    if not text:
        return ""
    if "base64," in text:
        text = _BASE64_DATA_URI_RE.sub("[base64 omitted]", text)
    cleaned = core._WHITESPACE_RE.sub(" ", text).strip()
    if max_len is not None and len(cleaned) > max_len:
        if max_len <= 3:
            return cleaned[:max_len]
        return cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def _split_text_chunks(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split cleaned text into bounded chunks at word boundaries when possible."""
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        end = min(total, start + max_len)
        if end < total:
            split_at = text.rfind(" ", start + max_len // 2, end)
            if split_at > start:
                end = split_at
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        start = end + 1 if end < total and text[end] == " " else end
    return chunks


def _collect_conv_files(conv_dir: Path) -> tuple[Path | None, list[tuple[str, Path]], list[list[Any]], int]:
    """Collect transcript/artifact paths, mtime+size fingerprint, and latest mtime_ns."""
    fingerprint: list[list[Any]] = []
    max_mtime_ns = 0

    transcript_path = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    found_transcript: Path | None = None
    try:
        if transcript_path.is_file():
            st = transcript_path.stat()
            found_transcript = transcript_path
            rel = ".system_generated/logs/transcript.jsonl"
            mtime_ns = int(st.st_mtime_ns)
            fingerprint.append([rel, mtime_ns, int(st.st_size)])
            if mtime_ns > max_mtime_ns:
                max_mtime_ns = mtime_ns
    except OSError:
        pass

    artifact_candidates: list[tuple[str, Path]] = []
    try:
        for entry in conv_dir.iterdir():
            name = entry.name
            if name.startswith("."):
                continue
            try:
                if entry.is_file() and name.lower().endswith(".md"):
                    artifact_candidates.append((name, entry))
                elif entry.is_dir():
                    for sub in entry.iterdir():
                        sub_name = sub.name
                        if sub_name.startswith("."):
                            continue
                        if sub.is_file() and sub_name.lower().endswith(".md"):
                            artifact_candidates.append((f"{name}/{sub_name}", sub))
            except OSError:
                continue
    except OSError:
        pass

    artifact_candidates.sort(key=lambda item: item[0])
    artifact_files: list[tuple[str, Path]] = []
    for rel, path in artifact_candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        mtime_ns = int(st.st_mtime_ns)
        fingerprint.append([rel, mtime_ns, int(st.st_size)])
        artifact_files.append((rel, path))
        if mtime_ns > max_mtime_ns:
            max_mtime_ns = mtime_ns

    fingerprint.sort(key=lambda item: item[0])
    return found_transcript, artifact_files, fingerprint, max_mtime_ns


def _discover_conversations(roots: list[Path]) -> dict[str, dict[str, Any]]:
    """Discover all indexable conversations across Antigravity brain roots."""
    discovered: dict[str, dict[str, Any]] = {}
    for root in roots:
        brain_dir = root / "brain"
        try:
            if not brain_dir.is_dir():
                continue
            conv_dirs = list(brain_dir.iterdir())
        except OSError:
            continue

        source_app = core._infer_source_app(root)
        for conv_dir in conv_dirs:
            conv_id = conv_dir.name
            if not core._is_safe_conv_id(conv_id):
                continue
            try:
                if not conv_dir.is_dir():
                    continue
            except OSError:
                continue

            transcript_path, artifact_files, fingerprint, max_mtime_ns = _collect_conv_files(conv_dir)
            if not fingerprint:
                continue

            existing = discovered.get(conv_id)
            if existing is None or max_mtime_ns > existing["updatedAtNs"]:
                discovered[conv_id] = {
                    "convId": conv_id,
                    "sourceApp": source_app,
                    "transcriptPath": transcript_path,
                    "artifactFiles": artifact_files,
                    "fingerprint": fingerprint,
                    "updatedAtNs": max_mtime_ns,
                }
    return discovered


def _iter_transcript_steps(transcript_path: Path) -> Iterator[dict[str, Any]]:
    """Stream transcript JSONL safely, skipping corrupt or >200KB non-base64 lines."""
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                if "base64," in stripped:
                    stripped = _BASE64_DATA_URI_RE.sub("[base64 omitted]", stripped)
                if len(stripped) > MAX_TRANSCRIPT_LINE_BYTES:
                    continue
                if len(stripped) > MAX_TRANSCRIPT_LINE_BYTES // 4:
                    if len(stripped.encode("utf-8", errors="ignore")) > MAX_TRANSCRIPT_LINE_BYTES:
                        continue
                try:
                    obj = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _format_utc_iso(mtime_ns: int) -> str:
    """Format nanosecond epoch timestamp as UTC ISO-8601 string."""
    epoch_secs = mtime_ns / 1_000_000_000 if mtime_ns > 0 else 0.0
    return datetime.fromtimestamp(epoch_secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _index_conversation(
    conv_info: dict[str, Any],
    meta_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Parse transcript and markdown artifacts into a compact cached conversation record."""
    conv_id = str(conv_info["convId"])
    transcript_path: Path | None = conv_info.get("transcriptPath")
    artifact_files: list[tuple[str, Path]] = conv_info.get("artifactFiles") or []
    updated_at_ns = int(conv_info.get("updatedAtNs") or 0)

    first_prompt = ""
    pending_tools: list[str] = []
    tool_cursor = 0
    edited_files: list[str] = []
    seen_files: set[str] = set()
    transcript_chunks: list[dict[str, str]] = []

    if transcript_path is not None:
        for step in _iter_transcript_steps(transcript_path):
            step_type = str(step.get("type") or "")
            status = str(step.get("status") or "")

            if step_type == "USER_INPUT":
                pending_tools = []
                tool_cursor = 0
                raw_text = core._text_of(step.get("content"))
                unwrapped = core._unwrap_user_prompt(raw_text)
                prompt_text = unwrapped or raw_text
                if not first_prompt:
                    first_prompt = _strip_base64_and_clean(prompt_text)
                cleaned = _strip_base64_and_clean(prompt_text, MAX_CHUNK_CHARS)
                if cleaned:
                    transcript_chunks.append({"source": "user", "label": "Prompt", "text": cleaned})

            elif step_type == "PLANNER_RESPONSE":
                pending_tools = []
                tool_cursor = 0
                content_text = core._text_of(step.get("content"))
                cleaned = _strip_base64_and_clean(content_text, MAX_CHUNK_CHARS)
                if cleaned:
                    transcript_chunks.append({"source": "assistant", "label": "Assistant", "text": cleaned})

                if status != "ERROR" and step.get("tool_calls") is not None:
                    raw_calls = core._unwrap_json_value(step.get("tool_calls"))
                    if isinstance(raw_calls, list):
                        for call in raw_calls:
                            names, files, commands = core._extract_tool_artifacts([call])
                            tool_name = names[0] if names else ""
                            pending_tools.append(tool_name)
                            name_lower = tool_name.lower()
                            if name_lower and any(hint in name_lower for hint in _EDIT_TOOL_HINTS):
                                for fpath in files:
                                    if fpath not in seen_files and len(edited_files) < MAX_EDITED_FILES:
                                        seen_files.add(fpath)
                                        edited_files.append(fpath)
                            for cmd in commands:
                                cmd_clean = _strip_base64_and_clean(cmd, MAX_CHUNK_CHARS)
                                if cmd_clean:
                                    transcript_chunks.append(
                                        {"source": "tool", "label": tool_name or "Command", "text": cmd_clean}
                                    )

            elif step_type in ("GENERIC", "TOOL_RESULT", "TOOL_OUTPUT"):
                tool_label = "Tool"
                if step_type == "GENERIC" and tool_cursor < len(pending_tools):
                    attributed = pending_tools[tool_cursor]
                    tool_cursor += 1
                    if attributed:
                        tool_label = attributed
                cleaned = _strip_base64_and_clean(core._text_of(step.get("content")), MAX_CHUNK_CHARS)
                if cleaned:
                    transcript_chunks.append({"source": "tool", "label": tool_label, "text": cleaned})

    artifact_chunks: list[dict[str, str]] = []
    for relpath, art_path in artifact_files:
        try:
            with art_path.open("rb") as handle:
                raw_bytes = handle.read(MAX_ARTIFACT_BYTES)
        except OSError:
            continue
        decoded = raw_bytes.decode("utf-8", errors="replace")
        cleaned_art = _strip_base64_and_clean(decoded)
        for part in _split_text_chunks(cleaned_art, MAX_CHUNK_CHARS):
            artifact_chunks.append({"source": "artifact", "label": relpath, "text": part})

    art_budget = max(MAX_ARTIFACT_CHUNKS_RESERVED, MAX_CHUNKS_PER_CHAT - len(transcript_chunks))
    kept_artifacts = artifact_chunks[:art_budget]
    tx_budget = MAX_CHUNKS_PER_CHAT - len(kept_artifacts)
    if len(transcript_chunks) <= tx_budget:
        kept_transcript = transcript_chunks
    elif tx_budget <= 0:
        kept_transcript = []
    elif tx_budget == 1:
        kept_transcript = transcript_chunks[:1]
    else:
        kept_transcript = [transcript_chunks[0], *transcript_chunks[-(tx_budget - 1) :]]

    display_meta = core._resolve_conv_display_meta(conv_id, first_prompt, meta_map)
    return {
        "fingerprint": conv_info["fingerprint"],
        "convId": conv_id,
        "title": display_meta["title"],
        "firstUserPrompt": first_prompt,
        "updatedAt": _format_utc_iso(updated_at_ns),
        "updatedAtNs": updated_at_ns,
        "sourceApp": str(conv_info.get("sourceApp") or ""),
        "isSubagent": bool(display_meta["isSubagent"]),
        "editedFiles": edited_files,
        "chunks": kept_transcript + kept_artifacts,
    }


def _load_search_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    """Load persisted search cache conversations map safely."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        return {}
    raw_convs = raw.get("conversations")
    if not isinstance(raw_convs, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for cid, entry in raw_convs.items():
        if isinstance(cid, str) and cid and isinstance(entry, dict):
            cleaned[cid] = entry
    return cleaned


def _sync_index() -> dict[str, dict[str, Any]]:
    """Synchronize mtime-fingerprinted search cache against brain roots and persist changes."""
    roots = core.get_brain_roots()
    discovered = _discover_conversations(roots)
    conv_ids = list(discovered.keys())
    meta_map = core._collect_conv_metadata(roots, conv_ids=conv_ids) if conv_ids else {}

    cache_path = get_search_cache_path()
    cached = _load_search_cache(cache_path)

    updated_cache: dict[str, dict[str, Any]] = {}
    dirty = not cache_path.is_file()

    for cid, existing in cached.items():
        if cid not in discovered:
            dirty = True

    for cid, conv_info in discovered.items():
        cached_entry = cached.get(cid)
        if cached_entry is not None and cached_entry.get("fingerprint") == conv_info["fingerprint"]:
            entry = dict(cached_entry)
            display_meta = core._resolve_conv_display_meta(
                cid,
                str(entry.get("firstUserPrompt") or ""),
                meta_map,
            )
            source_app = str(conv_info.get("sourceApp") or "")
            if (
                entry.get("title") != display_meta["title"]
                or bool(entry.get("isSubagent")) != bool(display_meta["isSubagent"])
                or entry.get("sourceApp") != source_app
            ):
                entry["title"] = display_meta["title"]
                entry["isSubagent"] = bool(display_meta["isSubagent"])
                entry["sourceApp"] = source_app
                dirty = True
            updated_cache[cid] = entry
        else:
            updated_cache[cid] = _index_conversation(conv_info, meta_map)
            dirty = True

    if dirty:
        core.save_json_locked(
            cache_path,
            {
                "version": CACHE_VERSION,
                "conversations": updated_cache,
            },
        )
    return updated_cache


def _parse_query_terms(query: str) -> list[str]:
    """Parse query string into deduplicated lowercase phrases and tokens."""
    terms: list[str] = []
    seen: set[str] = set()
    for match in _QUERY_TOKEN_RE.finditer(query or ""):
        if match.group(1) is not None:
            raw = match.group(1)
        else:
            raw = (match.group(2) or "").strip('"')
        cleaned = core._WHITESPACE_RE.sub(" ", raw).strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
    return terms


def _normalize_scope(scope: str) -> str:
    """Normalize search scope to one of all|messages|artifacts|files."""
    cleaned = str(scope or "all").strip().lower()
    return cleaned if cleaned in VALID_SCOPES else "all"


def _build_snippet_with_spans(
    chunk: dict[str, Any],
    pattern: re.Pattern[str],
) -> tuple[dict[str, Any] | None, int, set[str]]:
    """Build a ~140-char snippet centered on first match with exact highlight spans."""
    text = str(chunk.get("text") or "")
    if not text:
        return None, 0, set()

    matches = [(m.start(), m.end(), m.group(0).lower()) for m in pattern.finditer(text)]
    if not matches:
        return None, 0, set()

    first_start, first_end, _ = matches[0]
    if len(text) <= SNIPPET_TARGET_CHARS:
        snippet_text = text
        spans = [[s, e] for s, e, _ in matches]
    else:
        first_mid = (first_start + first_end) // 2
        win_start = max(0, first_mid - SNIPPET_TARGET_CHARS // 2)
        win_end = min(len(text), win_start + SNIPPET_TARGET_CHARS)
        if win_end == len(text):
            win_start = max(0, len(text) - SNIPPET_TARGET_CHARS)

        if win_start > 0 and win_start < first_start:
            left_space = text.find(" ", win_start, first_start)
            if left_space != -1 and left_space + 1 <= first_start:
                win_start = left_space + 1

        if win_end < len(text) and win_end > first_end:
            right_space = text.rfind(" ", first_end, win_end)
            if right_space != -1 and right_space >= first_end:
                win_end = right_space

        if win_end <= win_start:
            win_start = first_start
            win_end = min(len(text), first_start + SNIPPET_TARGET_CHARS)

        prefix = "..." if win_start > 0 else ""
        suffix = "..." if win_end < len(text) else ""
        snippet_text = f"{prefix}{text[win_start:win_end]}{suffix}"
        offset = len(prefix) - win_start
        spans = []
        for s, e, _ in matches:
            is_first = s == first_start and e == first_end
            if not is_first and (s < win_start or e > win_end):
                continue
            cs = max(s, win_start)
            ce = min(e, win_end)
            if cs < ce:
                spans.append([cs + offset, ce + offset])

    matched_terms = {matched for _, _, matched in matches}
    snippet = {
        "source": str(chunk.get("source") or "user"),
        "label": str(chunk.get("label") or ""),
        "text": snippet_text,
        "spans": spans,
    }
    return snippet, len(matches), matched_terms


def _build_preview_snippets(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build up to 2 truncated preview snippets (first user + first artifact) for empty query."""
    previews: list[dict[str, Any]] = []
    first_user = next((c for c in chunks if c.get("source") == "user" and c.get("text")), None)
    first_artifact = next((c for c in chunks if c.get("source") == "artifact" and c.get("text")), None)

    candidates = [c for c in (first_user, first_artifact) if c is not None]
    if not candidates:
        candidates = [c for c in chunks if c.get("text")][:MAX_EMPTY_PREVIEW_SNIPPETS]

    for chunk in candidates[:MAX_EMPTY_PREVIEW_SNIPPETS]:
        previews.append(
            {
                "source": str(chunk.get("source") or "user"),
                "label": str(chunk.get("label") or ""),
                "text": _strip_base64_and_clean(str(chunk.get("text") or ""), SNIPPET_TARGET_CHARS),
                "spans": [],
            }
        )
    return previews


def _scoped_chunks(chunks: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    """Return conversation chunks eligible for the requested scope."""
    if scope == "all":
        return chunks
    if scope == "messages":
        return [c for c in chunks if c.get("source") in _MESSAGE_SOURCES]
    if scope == "artifacts":
        return [c for c in chunks if c.get("source") == "artifact"]
    return []


def search_conversations(
    query: str = "",
    *,
    include_subagents: bool = False,
    scope: str = "all",
    limit: int = DEFAULT_SEARCH_LIMIT,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search indexed Antigravity conversations using AND term/phrase matching."""
    del config  # Reserved for future config-driven preferences; brain roots come from core.
    norm_scope = _normalize_scope(scope)
    clamped_limit = max(1, min(MAX_SEARCH_LIMIT, core._safe_int(limit, DEFAULT_SEARCH_LIMIT)))
    terms = _parse_query_terms(query)

    index = _sync_index()
    total_indexed = len(index)

    eligible = [
        entry
        for entry in index.values()
        if include_subagents or not bool(entry.get("isSubagent"))
    ]

    if not terms:
        ordered = sorted(
            eligible,
            key=lambda item: int(item.get("updatedAtNs") or 0),
            reverse=True,
        )
        results = [
            {
                "convId": str(item.get("convId") or ""),
                "title": str(item.get("title") or ""),
                "updatedAt": str(item.get("updatedAt") or ""),
                "sourceApp": str(item.get("sourceApp") or ""),
                "isSubagent": bool(item.get("isSubagent")),
                "matchCount": 0,
                "editedFiles": list(item.get("editedFiles") or []),
                "snippets": _build_preview_snippets(list(item.get("chunks") or [])),
            }
            for item in ordered[:clamped_limit]
        ]
        return {
            "query": query,
            "scope": norm_scope,
            "includeSubagents": bool(include_subagents),
            "totalIndexed": total_indexed,
            "matchedCount": len(ordered),
            "results": results,
        }

    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)),
        re.IGNORECASE,
    )
    scored: list[tuple[int, int, dict[str, Any]]] = []

    for entry in eligible:
        title = str(entry.get("title") or "")
        title_lower = title.lower() if norm_scope == "all" else ""
        edited_files = list(entry.get("editedFiles") or [])
        searchable_files = [f.lower() for f in edited_files] if norm_scope in ("all", "files") else []
        chunks = _scoped_chunks(list(entry.get("chunks") or []), norm_scope)
        chunk_lowers = [str(c.get("text") or "").lower() for c in chunks]

        # Enforce AND semantics: every term must appear in at least one eligible field.
        all_terms_present = True
        for term in terms:
            in_title = bool(title_lower and term in title_lower)
            in_files = any(term in fpath for fpath in searchable_files)
            in_chunks = any(term in ctext for ctext in chunk_lowers)
            if not (in_title or in_files or in_chunks):
                all_terms_present = False
                break
        if not all_terms_present:
            continue

        title_hit_count = sum(1 for t in terms if title_lower and t in title_lower)
        file_hit_count = sum(1 for fpath in searchable_files if any(t in fpath for t in terms))

        chunk_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
        total_chunk_hits = 0
        for idx, chunk in enumerate(chunks):
            snippet, hit_count, matched_terms = _build_snippet_with_spans(chunk, pattern)
            if snippet is None:
                continue
            total_chunk_hits += hit_count
            chunk_candidates.append((len(matched_terms), hit_count, -idx, snippet))

        chunk_candidates.sort(reverse=True)
        top_snippets = [item[3] for item in chunk_candidates[:MAX_SNIPPETS_PER_CHAT]]

        score = 0
        if title_hit_count > 0:
            score += 20
        if file_hit_count > 0:
            score += 10
        score += min(15, len(chunk_candidates))

        match_count = title_hit_count + file_hit_count + total_chunk_hits
        updated_ns = int(entry.get("updatedAtNs") or 0)
        scored.append(
            (
                score,
                updated_ns,
                {
                    "convId": str(entry.get("convId") or ""),
                    "title": title,
                    "updatedAt": str(entry.get("updatedAt") or ""),
                    "sourceApp": str(entry.get("sourceApp") or ""),
                    "isSubagent": bool(entry.get("isSubagent")),
                    "matchCount": match_count,
                    "editedFiles": edited_files,
                    "snippets": top_snippets,
                },
            )
        )

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return {
        "query": query,
        "scope": norm_scope,
        "includeSubagents": bool(include_subagents),
        "totalIndexed": total_indexed,
        "matchedCount": len(scored),
        "results": [item[2] for item in scored[:clamped_limit]],
    }
