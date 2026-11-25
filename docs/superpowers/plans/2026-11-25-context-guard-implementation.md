# Context Guard (`context-guard`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete, zero-runtime-dependency `context-guard` Antigravity plugin — transcript token analyzer, deduplicated lifecycle hooks, stdio JSON-RPC MCP server, pre-bundled MCP App dashboard, skill, and documentation — passing all unit/integration tests and `agy plugin validate --strict`.

**Architecture:** A pure Python 3.9+ stdlib core (`scripts/context_core.py`) reads Antigravity `transcript.jsonl` logs and shares atomic state at `~/.gemini/config/context-guard.json` (overridable via `CONTEXT_GUARD_CONFIG_PATH`). Lifecycle hooks (`scripts/hook.py`) evaluate thresholds at `SessionStart`, `PreInvocation`, `PostInvocation`, and `Stop`, while a zero-dep stdio MCP server (`server/mcp_server.py`) serves tools and a pre-bundled single-file `@modelcontextprotocol/ext-apps` dashboard (`ui/app.html`, compiled from `ui-src/`).

**Tech Stack:** Python 3.9+ standard library (`json`, `os`, `sys`, `re`, `glob`, `time`, `pathlib`, `tempfile`, `unittest`), TypeScript 7 (`ui-src/node_modules/.bin/tsc`), `esbuild`, `@modelcontextprotocol/ext-apps` (`2.0.4`), `agy plugin validate --strict`.

**Spec:** `/Users/user/Development/Plugins/context-guard/docs/superpowers/specs/2026-11-25-context-guard-design.md`

---

## Global Constraints

- **Python 3.9+ stdlib only at runtime:** `scripts/context_core.py`, `scripts/hook.py`, and `server/mcp_server.py` MUST NOT import third-party packages or use Python 3.10+ runtime syntax (no `match`/`case`, no runtime `A | B` in `isinstance()`; use `from __future__ import annotations` and `typing` generics where needed). Tests MUST use stdlib `unittest`.
- **Zero hardcoded user paths:** Never hardcode `/Users/user/...` or any user-specific path in code, manifests, hooks, skill, or README; use `Path.home()`, `${PLUGIN_ROOT}`, `${PLUGIN_DATA}`, and relative `./scripts/...` paths.
- **Hooks always fail open:** `scripts/hook.py` MUST always exit `0` and emit valid JSON (`{}` on internal errors, corrupt config, bad stdin, or missing transcript).
- **Safe slash-command bypass:** Prompts whose trimmed lowercased text starts with `/compact`, `/btw`, `/rewind`, `/new`, or `/clear` (exact match or followed by whitespace) MUST never be blocked or receive injected warnings.
- **Strict validation clean:** `agy plugin validate --strict /Users/user/Development/Plugins/context-guard` MUST exit `0` with `0` errors and `0` warnings.
- **No git commits or pushes:** Per user rule, NEVER run `git commit` or `git push`. Leave working tree uncommitted upon completion.

## Review Focus

1. **Corrupt/partial JSONL lines & 0-token error steps:** Live streaming writes can leave a partial final line or an errored `PLANNER_RESPONSE` with `prompt_tokens == 0`; `analyze_transcript` must skip malformed lines and require `prev_total >= 15000` and `0 < prompt_tokens < prev_total * 0.65` before counting a drop as compaction (`tests/test_context_core.py::TestTranscriptAnalysis::test_malformed_lines_and_zero_token_error_step`).
2. **First-epoch warning/alert deduplication (`-1` sentinel):** `warnedAtEpoch` and `alertedAtEpoch` MUST initialize to `-1` (not `0`) so alerts fire once when `compactionCount == 0`, suppress duplicates for the rest of epoch `0`, and re-arm when `compactionCount` increments to `1` (`tests/test_context_core.py::TestConfigAndState::test_epoch_dedup_sentinel_and_snooze_rearm` and `tests/test_hook.py::TestHooks::test_dedup_across_turns_and_reset_after_compaction`).
3. **Double-JSON-encoded `tool_calls` args & `<USER_REQUEST>` wrappers:** Real `transcript.jsonl` files serialize string tool args as JSON strings inside strings (e.g. `"\"/path/file.py\""`) and wrap user prompts in `<USER_REQUEST>...</USER_REQUEST><ADDITIONAL_METADATA>...`; parser and handoff builder must unwrap both cleanly (`tests/test_context_core.py::TestHandoff::test_handoff_unwraps_quoted_tool_args_and_user_request`).
4. **Fail-open hook execution on invalid stdin / corrupt config:** `scripts/hook.py` invoked as a subprocess with malformed stdin or unreadable/invalid JSON config must still emit `{}` on stdout and exit `0` (`tests/test_hook.py::TestHooks::test_fail_open_on_bad_stdin_and_corrupt_config`).
5. **MCP stdio JSON-RPC notifications & multi-source conversation resolution:** Notifications (messages without `id`) must produce zero stdout bytes, and `context_status` with `{}` input must resolve `conversationId` via arg -> `_meta["antigravity.google/conversation_id"]` -> `ANTIGRAVITY_CONVERSATION_ID` -> `lastActiveConversationId` -> most recent `transcript.jsonl` (`tests/test_mcp_server.py::TestMcpServer::test_notifications_no_reply_and_meta_resolution`).

---

## Verified Protocol & Schema Reference

### 1. Antigravity `transcript.jsonl` Schema (Observed)
- Every line is JSON with `step_index` (int), `source` (`"USER_EXPLICIT"` | `"MODEL"` | `"SYSTEM"`), `type` (`"USER_INPUT"` | `"PLANNER_RESPONSE"` | `"GENERIC"` | `"SYSTEM_MESSAGE"`), `status` (`"DONE"` | `"ERROR"`), `created_at` (ISO-8601).
- `PLANNER_RESPONSE`: carries `model` (str, e.g. `"gemini-4-hydrogen-high"`), `input_tokens` (int), `cache_read_tokens` (int), `output_tokens` (int), optional `content` (str), optional `thinking` (str), optional `tool_calls` (`[{"name": str, "args": dict}]`). Subsequent `GENERIC` steps before the next `PLANNER_RESPONSE`/`USER_INPUT` map positionally (`0..len(tool_calls)-1`) to `tool_calls` names.
- `USER_INPUT` / `GENERIC` / `SYSTEM_MESSAGE`: carry `content` (str) and omit token fields (`input_tokens` etc.).

### 2. Antigravity Lifecycle Hook I/O Contract (Verified)
- `stdin` JSON (camelCase): `conversationId`, `workspacePaths`, `transcriptPath`, `artifactDirectoryPath`, `modelName`, `lastUserInput`, `hookEventName`, `cwd`, plus `invocationNum`/`initialNumSteps` (`PreInvocation`, `PostInvocation`) or `executionNum`/`terminationReason` (`Stop`).
- `stdout` JSON:
  - `SessionStart` / `Stop`: `{}` (note: `Stop` only blocks stop when `"decision": "continue"`).
  - `PreInvocation` / `PostInvocation` warning/alert step: `{"injectSteps": [{"systemMessage": {"systemMessage": "<text>", "metadata": {"user_markdown": "<markdown>"}}}]}` (note nested `systemMessage` object; add `"terminationBehavior": "terminate"` on `PostInvocation` in `stop`/`gate` mode).
  - `PreInvocation` hard block (`gate` mode): `{"injectSteps": [{"errorMessage": {"userMessage": "<text>"}}], "terminationBehavior": "terminate"}`.

### 3. `@modelcontextprotocol/ext-apps` (`2.0.4`) & Antigravity `_meta` Contract (Verified)
- Server resource: URI `"ui://context-guard/dashboard.html"`, `mimeType: "text/html;profile=mcp-app"`, `_meta: {"ui": {"prefersBorder": False}}`.
- Server tool `_meta`:
  - `context_status`: `{"ui": {"resourceUri": "ui://context-guard/dashboard.html", "visibility": ["model", "app"]}, "com.google.antigravity/ui": {"entrypoints": [{"type": "aux_pane", "title": "Context Guard"}]}}`
  - `context_overview`: `{"ui": {"resourceUri": "ui://context-guard/dashboard.html", "visibility": ["model", "app"]}, "com.google.antigravity/ui": {"entrypoints": [{"type": "global", "title": "Context Guard"}]}}`
  - Model+app config/handoff tools: `{"ui": {"visibility": ["model", "app"]}}`
  - App-only tools: `{"ui": {"visibility": ["app"]}}`
- App SDK (`ui-src/src/main.ts`):
  - Register `app.ontoolinput`, `app.ontoolresult`, `app.ontoolcancelled`, `app.onhostcontextchanged` **before** `await app.connect()`.
  - Apply theme/vars/fonts via `applyDocumentTheme`, `applyHostStyleVariables`, `applyHostFonts` from `app.getHostContext()` and inside `onhostcontextchanged`.
  - Check `app.getHostCapabilities()` (`message`, `serverTools`, `updateModelContext`) before calling `app.sendMessage` / `app.callServerTool`.
  - `McpUiMessageRequest["params"]` omits `_meta` in `.d.ts` while `app.js` forwards `params` verbatim without stripping; cast `sendMessage` payload `as unknown as Parameters<App["sendMessage"]>[0]` when passing `_meta: {"com.google.antigravity/message": {"target": "new"}}` so `tsc --noEmit` passes under `strict: true`.
  - Guard `app.callServerTool` with an in-flight mutex (host caps at 4 concurrent in-flight calls).

---

### Task 1: Shared Core Engine (`scripts/context_core.py`)

**Files:**
- Create: `scripts/context_core.py`
- Test: `tests/test_context_core.py`

**Interfaces:**
- Consumes: Environment variables `CONTEXT_GUARD_CONFIG_PATH`, `CONTEXT_GUARD_BRAIN_ROOTS` (`os.pathsep`-delimited `<app_data_dir>` roots), `ANTIGRAVITY_APP_DATA_DIR`; JSONL transcripts at `<root>/brain/<convId>/.system_generated/logs/transcript.jsonl`.
- Produces (`scripts/context_core.py`):
  - Constants: `DEFAULT_THRESHOLD_TOKENS = 300_000`, `DEFAULT_WARN_PERCENT = 80`, `DEFAULT_MODE = "stop"`, `VALID_MODES = ("warn", "stop", "gate")`, `DEFAULT_MODEL_WINDOWS`
  - `get_config_path() -> Path`
  - `load_config() -> dict[str, Any]` (returns `{"enabled": bool, "thresholdTokens": int, "warnPercent": int, "mode": str, "modelWindows": dict[str, int], "lastActiveConversationId": str | None, "conversations": dict[str, dict[str, Any]]}`)
  - `save_config(config: dict[str, Any]) -> Path`
  - `get_conversation_state(config: dict[str, Any], conv_id: str) -> dict[str, Any]` (ensures entry with keys `transcriptPath`, `lastCompactionIndex`, `warnedAtEpoch` (default `-1`), `alertedAtEpoch` (default `-1`), `snoozedUntilTokens` (default `0`), `customThresholdTokens` (default `None`), `lastTokens` (default `0`), `lastModel` (default `""`), `updatedAt` (default `""`))
  - `resolve_effective_threshold(config: dict[str, Any], conv_id: str | None) -> int`
  - `resolve_model_window(model_name: str, config: dict[str, Any]) -> int`
  - `get_brain_roots() -> list[Path]`
  - `find_transcript_path(conv_id: str | None = None, explicit_path: str | None = None, config: dict[str, Any] | None = None) -> Path | None`
  - `list_recent_transcripts(limit: int = 20) -> list[dict[str, Any]]` (each item: `{"conversationId": str, "transcriptPath": str, "mtime": float, "sourceApp": str}` sorted newest-first)
  - `analyze_transcript(transcript_path: str | Path) -> dict[str, Any]` (returns `{"transcriptPath": str, "model": str, "stepCount": int, "turnCount": int, "inputTokens": int, "cacheReadTokens": int, "outputTokens": int, "promptTokens": int, "totalTokens": int, "pendingDeltaTokens": int, "effectiveTokens": int, "cacheHitPercent": float, "compactionCount": int, "activeEpochStep": int, "compactions": list[dict[str, Any]], "history": list[dict[str, Any]], "bloatByCategory": dict[str, int], "topBloatSteps": list[dict[str, Any]], "avgDeltaPerTurn": int, "firstUserPrompt": str, "recentUserPrompts": list[str], "recentFiles": list[str], "recentCommands": list[str]}`; `bloatByCategory` keys: `"user"`, `"model"`, `"toolOutputs"`, `"system"`; `history` items up to last 60 turns: `{"stepIndex": int, "inputTokens": int, "cacheReadTokens": int, "outputTokens": int, "totalTokens": int, "deltaTokens": int, "model": str, "createdAt": str}`; `topBloatSteps` up to 8 steps `>= activeEpochStep`: `{"stepIndex": int, "source": str, "type": str, "category": str, "tools": list[str], "estimatedTokens": int, "snippet": str}`)
  - `build_status_payload(conv_id: str | None = None, explicit_path: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]` (merges `analyze_transcript` metrics with `"view": "status"`, `"conversationId"`, `"status"` (`"ok"|"warn"|"limit"|"disabled"|"no_data"`), `"enabled"`, `"mode"`, `"warnPercent"`, `"baseThresholdTokens"`, `"snoozedUntilTokens"`, `"effectiveThresholdTokens"`, `"warnThresholdTokens"`, `"modelWindowTokens"`, `"thresholdPercent"`, `"windowPercent"`, `"remainingToThreshold"`, `"estimatedTurnsRemaining"`)
  - `build_overview_payload(config: dict[str, Any] | None = None, limit: int = 20) -> dict[str, Any]` (returns `{"view": "overview", "enabled": bool, "mode": str, "thresholdTokens": int, "warnPercent": int, "conversations": list[dict[str, Any]], "active": dict[str, Any] | None}`; each conversation summary has `conversationId`, `sourceApp`, `model`, `effectiveTokens`, `thresholdTokens`, `thresholdPercent`, `status`, `compactionCount`, `mtime`, `transcriptPath`)
  - `build_handoff_brief(conv_id: str | None = None, explicit_path: str | None = None, config: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]` (returns `{"conversationId": str | None, "effectiveTokens": int, "thresholdTokens": int, "brief": str}`)
  - `update_settings(config: dict[str, Any], *, threshold_tokens: int | None = None, warn_percent: int | None = None, mode: str | None = None, enabled: bool | None = None, conv_id: str | None = None, custom_threshold_tokens: int | None = None, snooze_delta: int | None = None, snooze_until_tokens: int | None = None, reset_snooze: bool = False) -> dict[str, Any]`

- [ ] **Step 1: Write failing unit tests for transcript analysis, compaction detection, bloat ranking, config/epoch state, and smart handoff**

Create `tests/test_context_core.py` using `unittest` and `tempfile.TemporaryDirectory`, adding `scripts/` to `sys.path`:

```python
import json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_core as core


class TestTranscriptAnalysis(unittest.TestCase):
    def test_token_math_pending_delta_and_positional_bloat(self):
        # Write JSONL: USER_INPUT(400 chars), PLANNER_RESPONSE(in=20000, cache=80000, out=1000, tool_calls=[read_file, run_command]),
        # GENERIC(8000 chars -> read_file), GENERIC(4000 chars -> run_command)
        # Assert promptTokens == 100000, totalTokens == 101000, pendingDeltaTokens == 3000,
        # effectiveTokens == 104000, cacheHitPercent == 80.0, topBloatSteps[0]["tools"] == ["read_file"]
        ...

    def test_compaction_detection_by_drop_and_marker(self):
        # Turn 1 total=100000, Turn 2 prompt=25000 (<65% drop), Turn 3 has "<CONTEXT_SUMMARY>..."
        # Assert compactionCount == 2, bloatByCategory only sums steps >= latest activeEpochStep
        ...

    def test_malformed_lines_and_zero_token_error_step(self):
        # Include truncated JSON line at EOF and PLANNER_RESPONSE with status="ERROR", input_tokens=0, cache_read_tokens=0
        # Assert no exception, compactionCount == 0, last valid PLANNER_RESPONSE metrics preserved
        ...


class TestConfigAndState(unittest.TestCase):
    def test_epoch_dedup_sentinel_and_snooze_rearm(self):
        # Fresh config via CONTEXT_GUARD_CONFIG_PATH: warnedAtEpoch == -1, alertedAtEpoch == -1
        # update_settings(snooze_delta=50000, conv_id="c1") sets snoozedUntilTokens >= 350000 and alertedAtEpoch == -1
        # update_settings(mode="invalid") raises ValueError
        ...

    def test_discovery_status_and_overview_payload(self):
        # Create fake root/brain/conv-1/.system_generated/logs/transcript.jsonl via CONTEXT_GUARD_BRAIN_ROOTS
        # Assert build_status_payload("conv-1")["status"] == "warn" at 250k/300k and build_overview_payload()["conversations"][0]["conversationId"] == "conv-1"
        ...


class TestHandoff(unittest.TestCase):
    def test_handoff_unwraps_quoted_tool_args_and_user_request(self):
        # USER_INPUT wrapped in <USER_REQUEST>\nFix bug\n</USER_REQUEST>\n<ADDITIONAL_METADATA>ts</ADDITIONAL_METADATA>
        # PLANNER_RESPONSE tool_calls: read_file Path="\"/repo/app.py\"", run_command CommandLine="\"pytest -q\""
        # Assert brief contains "Fix bug", "/repo/app.py" (without extra quotes), "pytest -q", and excludes "<ADDITIONAL_METADATA>"
        ...
```

- [ ] **Step 2: Run unit tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_context_core.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'context_core'`.

- [ ] **Step 3: Implement `scripts/context_core.py`**

Implement all functions listed in `Produces` using Python 3.9+ stdlib only (`from __future__ import annotations`):
- **Config store (`load_config`, `save_config`, `get_conversation_state`, `update_settings`):**
  - Default path `Path.home() / ".gemini" / "config" / "context-guard.json"`, overridden by `CONTEXT_GUARD_CONFIG_PATH`.
  - On corrupt/unreadable JSON, merge/return fresh defaults without raising.
  - `save_config` prunes `conversations` beyond 200 entries (preserving `lastActiveConversationId` and entries with `customThresholdTokens` or `snoozedUntilTokens > 0`) and writes atomically (`tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` -> `flush` + `os.fsync` -> `os.replace`).
  - `get_conversation_state` initializes missing entries with `warnedAtEpoch: -1`, `alertedAtEpoch: -1`, `snoozedUntilTokens: 0`, `lastCompactionIndex: 0`, `customThresholdTokens: None`, `transcriptPath: None`.
  - `update_settings` validates `mode in VALID_MODES`, clamps `thresholdTokens >= 1000` and `1 <= warnPercent <= 99`, applies `snooze_delta` relative to `max(current_effective_tokens, current_effective_threshold)`, and resets `alertedAtEpoch = -1` when snooze increases the threshold (or `reset_snooze=True`).
- **Discovery (`get_brain_roots`, `find_transcript_path`, `list_recent_transcripts`):**
  - `get_brain_roots` reads `CONTEXT_GUARD_BRAIN_ROOTS` (`os.pathsep`-delimited) if set; otherwise deduplicates `[ANTIGRAVITY_APP_DATA_DIR, ~/.gemini/antigravity, ~/.gemini/antigravity-cli, ~/.gemini/antigravity-ide]`.
  - `find_transcript_path` checks `explicit_path` -> `conversations[conv_id].transcriptPath` -> `<root>/brain/<conv_id>/.system_generated/logs/transcript.jsonl` across roots -> most recent transcript in `list_recent_transcripts(1)` if `conv_id is None`.
- **Transcript analyzer (`analyze_transcript`, `build_status_payload`, `build_overview_payload`, `build_handoff_brief`):**
  - Streams JSONL line-by-line, skipping blank or `json.JSONDecodeError` lines.
  - Tracks latest valid `PLANNER_RESPONSE` (`status != "ERROR"` or `(input_tokens + cache_read_tokens + output_tokens) > 0`), accumulating `pendingDeltaTokens = sum(len(content or "") // 4)` for steps after the latest `PLANNER_RESPONSE`.
  - Detects compaction when `(prev_total >= 15000 and prompt_tokens > 0 and prompt_tokens < prev_total * 0.65)` OR `"<CONTEXT_SUMMARY>"` is in `step.get("content") or ""` (recording `activeEpochStep` and incrementing `compactionCount`).
  - Maps `GENERIC` steps following a `PLANNER_RESPONSE` positionally to `tool_calls[idx]["name"]` for `topBloatSteps` (top 8 steps with `step_index >= activeEpochStep`, sanitized 100-char single-line snippet), and computes `bloatByCategory`, `avgDeltaPerTurn` (average delta across up to last 5 turns), `firstUserPrompt` / `recentUserPrompts` (unwrapping `<USER_REQUEST>...</USER_REQUEST>` and stripping metadata blocks), and `recentFiles` / `recentCommands` (unwrapping JSON-quoted string args in `tool_calls`).

- [ ] **Step 4: Run `tests/test_context_core.py` to verify all pass**

Run: `python3 -m unittest discover -s tests -p "test_context_core.py" -v`
Expected: `Ran 6 tests ... OK` (exit code `0`).

---

### Task 2: Lifecycle Hooks (`scripts/hook.py` & `hooks.json`)

**Files:**
- Create: `scripts/hook.py`
- Verify: `hooks.json` (already at plugin root; verify command paths & events)
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes (`context_core.py`): `load_config`, `save_config`, `get_conversation_state`, `resolve_effective_threshold`, `find_transcript_path`, `analyze_transcript`, `build_status_payload`.
- Produces (`scripts/hook.py`):
  - `BYPASS_PREFIXES: tuple[str, ...] = ("/compact", "/btw", "/rewind", "/new", "/clear")`
  - `is_bypass_command(last_user_input: str) -> bool`
  - `handle_hook(event_name: str, payload: dict[str, Any]) -> dict[str, Any]`
  - `main(argv: list[str] | None = None) -> int` (CLI entrypoint `python3 ./scripts/hook.py <Event>`)

- [ ] **Step 1: Write failing hook integration tests covering all 4 events, modes, bypasses, dedup, and fail-open behavior**

Create `tests/test_hook.py` invoking both `hook.handle_hook` and `subprocess.run([sys.executable, "scripts/hook.py", event], ...)`:

```python
class TestHooks(unittest.TestCase):
    def test_session_start_registers_conversation_and_returns_empty(self):
        # Invoke SessionStart with conversationId + transcriptPath; assert stdout == {} and config saved lastActiveConversationId
        ...

    def test_dedup_across_turns_and_reset_after_compaction(self):
        # PreInvocation at 250k/300k (warn 80%): fires nested {"injectSteps": [{"systemMessage": {"systemMessage": ..., "metadata": {"user_markdown": ...}}}]}
        # Second PreInvocation at 260k/300k (same epoch): returns {}
        # Transcript undergoes compaction -> compactionCount=1, later rises to 250k again -> warns once more
        ...

    def test_post_invocation_stop_mode_terminates_once_per_epoch(self):
        # PostInvocation at 310k/300k in mode="stop": returns injectSteps(systemMessage) + terminationBehavior == "terminate"
        # Next PreInvocation at 310k/300k in mode="stop": returns {} (not trapped)
        ...

    def test_pre_invocation_gate_mode_blocks_until_snooze_or_bypass(self):
        # PreInvocation at 310k/300k in mode="gate": returns injectSteps([{"errorMessage": {"userMessage": ...}}]) + terminationBehavior == "terminate"
        # After snoozing to 360k: returns {}
        ...

    def test_safe_bypass_commands_in_gate_mode(self):
        # In gate mode at 350k/300k, test "/compact", "/compact focus on auth", " /BTW ", "/rewind", "/new", "/clear"
        # Assert all return {} and do NOT terminate
        ...

    def test_stop_records_turn_stats_and_returns_empty(self):
        # Stop event updates lastTokens / lastModel in conv state and returns {}
        ...

    def test_fail_open_on_bad_stdin_and_corrupt_config(self):
        # Run subprocess hook.py PreInvocation with "{not-json" on stdin and corrupt config file
        # Assert returncode == 0 and json.loads(stdout) == {}
        ...
```

- [ ] **Step 2: Run hook tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_hook.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hook'`.

- [ ] **Step 3: Implement `scripts/hook.py` (chmod `+x`)**

Implement `is_bypass_command`, `handle_hook`, and `main`:
- Import `context_core` via `Path(__file__).resolve().parent`.
- `is_bypass_command(last_user_input: str) -> bool`: splits `last_user_input.strip().lower()` on whitespace; returns `True` if first token is in `BYPASS_PREFIXES`.
- `handle_hook(event_name: str, payload: dict[str, Any]) -> dict[str, Any]`:
  - Resolves `event = event_name or str(payload.get("hookEventName") or "")`.
  - Extracts `conv_id`, `transcript_path` (`os.path.expanduser` if present), `last_user_input`.
  - Updates `config["lastActiveConversationId"]` and `conv_state["transcriptPath"]` when present.
  - Checks `enabled` and `is_bypass_command`; if disabled or bypassed, saves updated discovery state and returns `{}`.
  - Computes `status = core.build_status_payload(...)` and compares `effectiveTokens` against `effectiveThresholdTokens` and `warnThresholdTokens` using `warnedAtEpoch < compactionCount` and `alertedAtEpoch < compactionCount`.
  - Injects nested `{"systemMessage": {"systemMessage": text, "metadata": {"user_markdown": md}}}` for warn/stop alerts, `{"errorMessage": {"userMessage": text}}` + `"terminationBehavior": "terminate"` for `PreInvocation` `gate` blocks, and `"terminationBehavior": "terminate"` for `PostInvocation` threshold crossings in `stop` or `gate` mode.
  - On `Stop`, stores `lastTokens`, `lastModel`, `updatedAt` and returns `{}`.
- `main`: wraps everything in `try/except Exception` so stdout is always `{}` and exit code is always `0`.

- [ ] **Step 4: Run `tests/test_hook.py` to verify all pass**

Run: `python3 -m unittest discover -s tests -p "test_hook.py" -v`
Expected: `Ran 7 tests ... OK` (exit code `0`).

---

### Task 3: Zero-Dep Python MCP Server (`server/mcp_server.py`)

**Files:**
- Create: `server/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes (`scripts/context_core.py`): `load_config`, `update_settings`, `build_status_payload`, `build_overview_payload`, `build_handoff_brief`, `list_recent_transcripts`.
- Consumes (Resource HTML): `CONTEXT_GUARD_APP_HTML` env var path if set and readable, else `Path(__file__).resolve().parent.parent / "ui" / "app.html"`.
- Produces (`server/mcp_server.py`):
  - Constants: `APP_URI = "ui://context-guard/dashboard.html"`, `RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"`, `TOOL_DEFINITIONS`
  - `resolve_conversation_id(arguments: dict[str, Any], request_meta: dict[str, Any], config: dict[str, Any]) -> str | None`
  - `call_tool(name: str, arguments: dict[str, Any], request_meta: dict[str, Any] | None = None) -> dict[str, Any]`
  - `handle_jsonrpc_message(msg: dict[str, Any]) -> dict[str, Any] | None`
  - `serve_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None`

- [ ] **Step 1: Write failing MCP stdio protocol & tool tests**

Create `tests/test_mcp_server.py` testing both direct `handle_jsonrpc_message` and full subprocess stdio round-trips:

```python
class TestMcpServer(unittest.TestCase):
    def test_initialize_ping_and_capabilities(self):
        # Send initialize (protocolVersion="2025-11-25") + ping over subprocess stdin
        # Assert capabilities has "tools" and "resources", serverInfo["name"] == "context-guard-monitor"
        ...

    def test_tools_list_metadata_and_visibility(self):
        # Call tools/list; assert all 8 tools exist:
        # context_status has _meta.ui.resourceUri == APP_URI and aux_pane entrypoint, required == []
        # context_overview has _meta.ui.resourceUri == APP_URI and global entrypoint, required == []
        # app_save_settings, app_snooze, app_refresh, app_build_handoff have _meta.ui.visibility == ["app"]
        ...

    def test_resources_list_and_read_html_with_override(self):
        # Point CONTEXT_GUARD_APP_HTML to temp HTML file; call resources/list and resources/read
        # Assert mimeType == "text/html;profile=mcp-app", _meta.ui.prefersBorder is False, text matches file
        ...

    def test_notifications_no_reply_and_meta_resolution(self):
        # Send notifications/initialized (no id) -> assert 0 stdout lines returned
        # Call context_status with {} args and _meta={"antigravity.google/conversation_id": "conv-meta"}
        # Assert structuredContent["conversationId"] == "conv-meta" and tokens match fixture transcript
        ...

    def test_configure_snooze_and_handoff_tools(self):
        # Call context_configure(thresholdTokens=250000, mode="gate") -> persisted in config
        # Call app_snooze(conversationId="conv-1", deltaTokens=50000) -> snoozedUntilTokens updated
        # Call app_build_handoff(conversationId="conv-1") -> structuredContent["brief"] non-empty
        ...
```

- [ ] **Step 2: Run MCP server tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_mcp_server.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'`.

- [ ] **Step 3: Implement `server/mcp_server.py` (chmod `+x`)**

Implement the stdio JSON-RPC 2.0 server:
- Add `Path(__file__).resolve().parent.parent / "scripts"` to `sys.path` and import `context_core`.
- Implement `resolve_conversation_id` priority: `arguments.get("conversationId")` -> `request_meta.get("antigravity.google/conversation_id")` -> `request_meta.get("com.google.antigravity/conversation_id")` -> `os.environ.get("ANTIGRAVITY_CONVERSATION_ID")` -> `config.get("lastActiveConversationId")` -> `list_recent_transcripts(1)[0]["conversationId"]` (if non-empty).
- Define schemas & `_meta` for all 8 tools (`context_status`, `context_overview`, `context_configure`, `context_handoff`, `app_save_settings`, `app_snooze`, `app_refresh`, `app_build_handoff`); ensure entrypoint tools (`context_status`, `context_overview`) accept `{}` with `required: []`. Map camelCase tool inputs (`thresholdTokens`, `warnPercent`, `mode`, `enabled`, `conversationId`, `customThresholdTokens`, `snoozeTokens` / `deltaTokens`, `resetSnooze`) cleanly onto `context_core.update_settings(...)`.
- Implement `call_tool` returning concise `content` text + full `structuredContent` dict (`isError: True` on validation errors or unknown tool). Note: `app_refresh` returns `build_overview_payload` when `arguments.get("view") == "overview"`, otherwise `build_status_payload`.
- Implement `handle_jsonrpc_message` for `initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`, `resources/read` (`-32601` for unknown methods, `-32602` for unknown resource URI), returning `None` whenever `"id"` is absent in request.
- Implement `serve_stdio` reading line-by-line from `sys.stdin`, returning JSON-RPC parse error `-32700` on invalid JSON lines, writing single-line JSON with `flush=True`.

- [ ] **Step 4: Run `tests/test_mcp_server.py` to verify all pass**

Run: `python3 -m unittest discover -s tests -p "test_mcp_server.py" -v`
Expected: `Ran 5 tests ... OK` (exit code `0`).

---

### Task 4: Pre-bundled Single-File MCP App Dashboard (`ui-src/` -> `ui/app.html`)

**Files:**
- Create: `ui-src/tsconfig.json`
- Create: `ui-src/src/main.ts`
- Create: `ui-src/build.mjs`
- Create (generated artifact): `ui/app.html`
- Modify: `ui-src/package.json` (add `"typecheck"` and `"build"` scripts)

**Interfaces:**
- Consumes (`server/mcp_server.py` `structuredContent`):
  - Single-conversation `StatusPayload`: `conversationId`, `status` (`"ok"|"warn"|"limit"|"disabled"|"no_data"`), `enabled`, `mode`, `warnPercent`, `model`, `effectiveTokens`, `inputTokens`, `cacheReadTokens`, `outputTokens`, `pendingDeltaTokens`, `cacheHitPercent`, `effectiveThresholdTokens`, `warnThresholdTokens`, `baseThresholdTokens`, `snoozedUntilTokens`, `modelWindowTokens`, `thresholdPercent`, `windowPercent`, `remainingToThreshold`, `avgDeltaPerTurn`, `estimatedTurnsRemaining`, `compactionCount`, `history`, `bloatByCategory`, `topBloatSteps`.
  - Overview `OverviewPayload`: `view: "overview"`, `enabled`, `mode`, `thresholdTokens`, `warnPercent`, `conversations`, `active`.
  - App-only tools via `app.callServerTool`: `app_refresh`, `app_snooze`, `app_save_settings`, `app_build_handoff`.
- Produces (`ui/app.html`): Self-contained HTML5 document (`text/html;profile=mcp-app`) bundling `@modelcontextprotocol/ext-apps` with zero external requests (`default-src 'none'` CSP compliant).

- [ ] **Step 1: Add `ui-src/tsconfig.json`, `ui-src/build.mjs`, and scripts in `ui-src/package.json`**

Configure `ui-src/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"]
}
```
Create `ui-src/build.mjs` using `esbuild` (`bundle: true, minify: true, format: "iife", platform: "browser", target: ["es2020"], write: false`) to inline bundled JS (escaping `</script` -> `<\/script`) + inline host-variable-themed CSS (`--color-background-*`, `--color-text-*`, `--color-border-*`, `--font-sans` with clean light/dark fallbacks) into `../ui/app.html`. Update `ui-src/package.json` `"scripts"` with `"typecheck": "tsc -p tsconfig.json --noEmit"` and `"build": "node build.mjs"`.

- [ ] **Step 2: Implement `ui-src/src/main.ts`**

Implement the interactive dashboard using `@modelcontextprotocol/ext-apps`:
- Instantiate `const app = new App({ name: "Context Guard", version: "1.0.0" }, {});`.
- Register `app.ontoolinput`, `app.ontoolresult`, `app.ontoolcancelled`, and `app.onhostcontextchanged` **before** `await app.connect()`.
- On connect & `onhostcontextchanged`: call `applyDocumentTheme`, `applyHostStyleVariables`, `applyHostFonts` when present.
- Render both **Detail View** (gauge + dual progress bars for Guard Threshold and Model Window, 4 metric cards, SVG turn-by-turn sparkline with dashed threshold line, Bloat Inspector category chips + top 8 steps table, inline Settings drawer) and **Overview View** (multi-conversation list when `view === "overview"`, clicking any row switches to that conversation via `app_refresh`).
- Gate actions on `app.getHostCapabilities()`:
  - `Refresh` + `Auto (5s)` toggle guarded by an `inFlight` boolean so concurrent `callServerTool` calls never pile up.
  - `Snooze +50k` / `Clear Snooze` -> `app.callServerTool({ name: "app_snooze", arguments: ... })`.
  - `Save Settings` -> `app.callServerTool({ name: "app_save_settings", arguments: ... })`.
  - `Try /compact` (only shown/enabled if `caps?.message`) -> `app.sendMessage({ role: "user", content: [{ type: "text", text: "/compact" }] })`.
  - `Smart Handoff` -> calls `app_build_handoff`; if `caps?.message`, calls `app.sendMessage({ role: "user", content: [{ type: "text", text: brief }], _meta: { "com.google.antigravity/message": { target: "new" } } } as unknown as Parameters<App["sendMessage"]>[0])` and displays returned `conversationId`; always supports `Copy Brief` via `navigator.clipboard` / copyable preview fallback.

- [ ] **Step 3: Run TypeScript typecheck, build `ui/app.html`, and verify CSP/bundle constraints**

Run:
```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd /Users/user/Development/Plugins/context-guard/ui-src && npm run typecheck && npm run build
```
And verify `ui/app.html` properties with Python:
```bash
python3 -c '
from pathlib import Path
html = Path("/Users/user/Development/Plugins/context-guard/ui/app.html").read_text(encoding="utf-8")
assert "<!doctype html>" in html.lower()
assert len(html.encode("utf-8")) < 512 * 1024
assert "<script src=" not in html and "<link " not in html
print("OK bytes:", len(html.encode("utf-8")))
'
```
Expected: `tsc` exits `0`, `build.mjs` writes `ui/app.html`, verification prints `OK bytes: ...`.

- [ ] **Step 4: Re-run `tests/test_mcp_server.py` against the real bundled `ui/app.html`**

Run: `python3 -m unittest discover -s tests -p "test_mcp_server.py" -v`
Expected: PASS (serving real `ui/app.html` from `resources/read` without override).

---

### Task 5: Skill, README, and End-to-End Strict Validation

**Files:**
- Create: `skills/context-guard/SKILL.md`
- Create: `README.md`
- Verify: entire plugin directory `/Users/user/Development/Plugins/context-guard`

**Interfaces:**
- Consumes: MCP tools `context_status`, `context_overview`, `context_configure`, `context_handoff` from server `monitor` (namespaced `context-guard_monitor`), `plugin.json`, `hooks.json`, `mcp_config.json`, `ui/app.html`.
- Produces: Discoverable Antigravity skill `context-guard` (`skills/context-guard/SKILL.md`) and marketplace/GitHub documentation (`README.md`).

- [ ] **Step 1: Create `skills/context-guard/SKILL.md`**

Include valid YAML frontmatter:
```yaml
---
name: context-guard
description: >-
  Monitor active context window token usage, inspect context bloat, configure
  Context Guard thresholds or modes, snooze alerts, or prepare a clean
  handoff brief for a fresh conversation. Use when the user invokes
  /context-guard or asks about context usage, token bloat, compaction, or
  session handoff.
---
```
Keep instructions concise: map user intents to `context_status` (opens interactive side-pane dashboard), `context_overview` (all sessions), `context_configure` (threshold/warn%/mode/snooze), and `context_handoff` (fresh-chat brief); remind the agent not to dump raw multi-kilobyte transcripts into chat and to suggest `/compact` or the dashboard's **Smart Handoff** button when near limit.

- [ ] **Step 2: Create `README.md`**

Document: overview, zero-runtime-dependency architecture, installation (`agy plugin install` and manual `~/.gemini/config/plugins/context-guard/`), hook enforcement modes (`warn`, `stop`, `gate`) + safe `/compact` bypass, configuration file (`~/.gemini/config/context-guard.json`), MCP tools & Dashboard UI guide, UI development rebuild steps (`ui-src/`), and known platform limitation (programmatic `/compact` requires user action or `sendMessage`).

- [ ] **Step 3: Run complete Python test suite and UI check**

Run:
```bash
python3 -m unittest discover -s /Users/user/Development/Plugins/context-guard/tests -v
```
Expected: All tests across `test_context_core.py`, `test_hook.py`, and `test_mcp_server.py` PASS with exit code `0`.

- [ ] **Step 4: Run `agy plugin validate --strict --json` on the complete plugin**

Run:
```bash
agy plugin validate --strict --json /Users/user/Development/Plugins/context-guard
```
Expected: Exit code `0`, `"findings": []`, `"components": {"hooks": 1, "mcpServers": 1, "skills": 1, ...}` (0 errors, 0 warnings).
