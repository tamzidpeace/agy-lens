# Context Guard (`context-guard`) — Design Specification

**Date:** 2026-11-25  
**Target Directory:** `/Users/user/Development/Plugins/context-guard`  
**Status:** Approved for Planning & Execution

---

## 1. Intent & Why

Long-running agent sessions in Antigravity (Desktop App, CLI `agy`, and IDE) accumulate context across file reads, command outputs, web searches, and multi-turn reasoning. While Antigravity performs automatic background compaction near the model's hard limit (e.g., ~1M tokens for Gemini models), waiting until near-limit degrades reasoning quality ("context rot"), increases latency, and triggers lossy automatic summarization mid-task. Furthermore, the Antigravity Desktop App UI does not display a live token meter, per-turn bloat breakdown, or a user-configurable threshold (e.g. `300k` tokens).

`context-guard` is a shareable, GitHub-ready Antigravity plugin that:
1. Observes exact per-turn context token usage from Antigravity's `transcript.jsonl` logs.
2. Enforces a user-configurable context threshold (default `300,000` tokens) with warning, turn-end stop, or strict pre-call gate modes.
3. Renders a live, interactive **MCP App Dashboard** in the Side Pane (`aux_pane`), Left Sidebar (`global` multi-conversation view), and inline chat cards.
4. Helps users reclaim or transfer context in one click (`/compact` trigger, `+50k` snooze, or Smart Handoff to a fresh conversation).

## 2. Goals, Non-Goals, and Anti-Goals

### Goals
- **Zero-runtime-dependency distribution**: End users install via `agy plugin install <github-url-or-path>` or dropping into `~/.gemini/config/plugins/context-guard/` with zero `npm install` or `pip install` steps at runtime (Python 3.9+ stdlib backend + pre-bundled single-file HTML MCP App UI).
- **Accurate token & compaction tracking**: Parse `PLANNER_RESPONSE` token metrics (`input_tokens + cache_read_tokens + output_tokens`) plus estimate any pending post-response tool/user delta, and auto-detect compaction events (token drops `>= 35%` or `<CONTEXT_SUMMARY>` markers) so guard state resets cleanly.
- **Non-spammy lifecycle hooks**: `SessionStart`, `PreInvocation`, `PostInvocation`, and `Stop` hooks inject warnings **at most once per threshold crossing** (never repeating identical text on every turn), fail open on unexpected errors (always exit `0`), and never block `/compact`, `/btw`, `/rewind`, `/new`, or `/clear`.
- **Rich interactive MCP App Dashboard**: Live gauge vs guard threshold and model window, token breakdown (`input`, `cache_read`, `output`, cache hit %), turn-by-turn SVG sparkline, Context Bloat Inspector (heaviest steps/tools since last compaction), settings editor, snooze control, `/compact` sender, and 1-click Smart Handoff (`target: "new"`).
- **Passes `agy plugin validate --strict`** with 0 errors and 0 warnings.

### Non-Goals
- Silently invoking Antigravity's internal `/compact` RPC (no public hook or API exists to trigger host-side compaction without user composer action or `sendMessage`).
- Replacing Antigravity's built-in compaction algorithm; instead, `context-guard` acts as an early-warning guardrail, diagnostic inspector, and fresh-chat handoff assistant.

### Anti-Goals
- Injecting verbose system messages on every turn (which would accelerate context bloat).
- Blocking `/compact` or trapping the user in an unrecoverable gate.
- Hardcoding machine-specific paths (`/Users/user/...`) anywhere in plugin manifests, hooks, or server code.

## 3. Approaches Considered

1. **Hooks + Skill only (No MCP App)**: Minimal, but gives no live visual dashboard, no bloat breakdown, no multi-conversation view, and forces token-heavy text messages into chat. Rejected.
2. **Node.js MCP Server + Vite UI**: Standard TS ecosystem, but breaks for GUI users whose `node` lives in `nvm` outside system `PATH`, or requires end-user `npm install`. Rejected.
3. **Chosen — Pure Python 3 Stdlib Backend + Pre-bundled Single-File `@modelcontextprotocol/ext-apps` UI**:
   - Python 3 (`python3`) is present natively on macOS/Linux and uses only stdlib (`json`, `os`, `sys`, `re`, `glob`, `time`, `pathlib`, `tempfile`).
   - UI is written in TypeScript using `@modelcontextprotocol/ext-apps` and bundled at build time via `esbuild` into `ui/app.html` (committed artifact), complying with Antigravity's strict default CSP (`default-src 'none'`).

## 4. Architecture & File Layout

```text
context-guard/
├── plugin.json                 # Strict-validated manifest (name: context-guard)
├── mcp_config.json             # Launches python3 ${PLUGIN_ROOT}/server/mcp_server.py
├── hooks.json                  # SessionStart, PreInvocation, PostInvocation, Stop
├── README.md                   # User & Marketplace documentation
├── assets/
│   └── logo.svg                # 128x128 square SVG icon (~10% padding)
├── scripts/
│   ├── context_core.py         # Shared analyzer, config store, bloat & handoff engine
│   └── hook.py                 # Lifecycle hook entrypoint (stdin JSON -> stdout JSON)
├── server/
│   └── mcp_server.py           # Zero-dep MCP stdio JSON-RPC 2.0 server + MCP App host
├── skills/
│   └── context-guard/
│       └── SKILL.md            # /context-guard skill for chat usage
├── ui-src/                     # Dev-only TS source & esbuild config (node_modules gitignored)
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       └── main.ts             # @modelcontextprotocol/ext-apps dashboard UI
├── ui/
│   └── app.html                # Pre-bundled self-contained HTML served at ui://context-guard/dashboard.html
└── tests/
    ├── test_context_core.py    # Unit tests for parser, compaction, bloat, config, handoff
    ├── test_hook.py            # Integration tests for all 4 hook events & guardrails
    └── test_mcp_server.py      # End-to-end stdio JSON-RPC protocol & tool tests
```

## 5. Component Specifications

### 5.1 Shared Core (`scripts/context_core.py`)
- **Config & State Path**:
  - Reads `CONTEXT_GUARD_CONFIG_PATH` env var if set; otherwise `~/.gemini/config/context-guard.json`.
  - Default global settings:
    - `enabled`: `true`
    - `thresholdTokens`: `300000`
    - `warnPercent`: `80` (240k at 300k threshold)
    - `mode`: `"stop"` (`"warn"` | `"stop"` | `"gate"`)
    - `modelWindows`: mapping (`gemini-4`: 1048576, `gemini-3`: 1048576, `claude`: 200000, `gpt-4o`: 128000, `default`: 1048576)
  - Per-conversation state (`conversations[convId]`):
    - `transcriptPath`, `lastCompactionIndex`, `warnedAtEpoch`, `alertedAtEpoch`, `snoozedUntilTokens`, `customThresholdTokens` (optional override).
  - Atomic writes via `tempfile.NamedTemporaryFile` in same directory + `os.replace`.
- **Transcript Discovery**:
  - Searches explicit `transcriptPath` -> `conversations[convId].transcriptPath` -> `<root>/brain/<convId>/.system_generated/logs/transcript.jsonl` across `ANTIGRAVITY_APP_DATA_DIR`, `~/.gemini/antigravity`, `~/.gemini/antigravity-cli`, `~/.gemini/antigravity-ide` (overridable via `CONTEXT_GUARD_BRAIN_ROOTS` for tests).
- **Transcript Analysis (`analyze_transcript`)**:
  - Streams JSONL safely (skips malformed partial lines gracefully).
  - Extracts turn history from `PLANNER_RESPONSE` lines: `promptTokens = input_tokens + cache_read_tokens`, `outputTokens = output_tokens`, `totalTokens = promptTokens + outputTokens`.
  - Computes `pendingDeltaTokens = sum(len(content or '') // 4)` for steps after the last `PLANNER_RESPONSE`.
  - `effectiveTokens = totalTokens + pendingDeltaTokens`.
  - Detects compaction events (`promptTokens < prev_total * 0.65` when `prev_total >= 15000`, or `<CONTEXT_SUMMARY>` in content). Compaction increments `compactionCount` and updates `activeEpoch` (step index of latest compaction).
  - Computes **Bloat Inspector** over steps `>= activeEpoch`:
    - Category breakdown (User, Model/Reasoning, Tool Outputs, System).
    - Top 8 heaviest steps with step index, source/type, tool names (matched from preceding `PLANNER_RESPONSE`), estimated tokens, and a sanitized 100-char snippet.
  - Computes `avgDeltaPerTurn` (last 5 turns) and `estimatedTurnsRemaining`.
- **Smart Handoff (`build_handoff_brief`)**:
  - Extracts first user prompt, last 3 user prompts, unique file paths/commands from recent `tool_calls`, current token stats, and formats a structured continuation prompt ready for a fresh conversation.

### 5.2 Lifecycle Hooks (`scripts/hook.py`)
- CLI: `python3 ./scripts/hook.py <SessionStart|PreInvocation|PostInvocation|Stop>`
- Reads JSON from `stdin`, always exits `0` (printing `{}` on internal errors so Antigravity is never crashed).
- Records `conversationId` -> `transcriptPath` and `lastActiveConversationId` in config/state.
- **Deduplication**: Tracks `warnedEpoch` and `alertedEpoch` per conversation against `analysis.compactionCount` (so warnings fire once per compaction epoch!).
- **Safe bypass**: If `lastUserInput` (trimmed, lowercased) begins with `/compact`, `/btw`, `/rewind`, `/new`, or `/clear`, never blocks or injects warnings.
- **Behavior by mode** (using `effectiveThreshold = snoozedUntilTokens` if `> threshold`, else `customThreshold or thresholdTokens`):
  - `SessionStart`: Registers session; returns `{}`.
  - `PreInvocation`:
    - If `effectiveTokens >= effectiveThreshold`:
      - In `"gate"` mode: returns `{"injectSteps": [{"errorMessage": {"userMessage": "..."}}], "terminationBehavior": "terminate"}`.
      - In `"stop"` or `"warn"` mode: if `alertedEpoch < compactionCount`, marks `alertedEpoch` and injects a concise `systemMessage` warning.
    - Else if `effectiveTokens >= effectiveThreshold * warnPercent / 100` and `warnedEpoch < compactionCount`:
      - Marks `warnedEpoch` and injects a concise 1-line `systemMessage` warning.
  - `PostInvocation`:
    - Re-analyzes transcript after the model turn.
    - If `effectiveTokens >= effectiveThreshold` and `mode in ("stop", "gate")` and `alertedEpoch < compactionCount`:
      - Marks `alertedEpoch`, injects a concise `systemMessage` alert, and returns `"terminationBehavior": "terminate"` to pause cleanly at turn end before runaway tool loops!
    - Else if crossing `warnPercent` for the first time in this epoch: injects concise `systemMessage` warning.
  - `Stop`: Records final turn stats; returns `{}`.

### 5.3 MCP Server (`server/mcp_server.py`)
- Pure Python 3 stdlib newline-delimited JSON-RPC 2.0 server over `sys.stdin` / `sys.stdout` (line-buffered).
- Declares capabilities: `tools: {}`, `resources: {}`.
- Serves `ui://context-guard/dashboard.html` with MIME `text/html;profile=mcp-app`, `_meta.ui.prefersBorder = False`.
- **Tools**:
  1. `context_status` (visibility: `model`, `app`; `_meta.ui.resourceUri = "ui://context-guard/dashboard.html"`, entrypoints: `[{"type": "aux_pane", "title": "Context Guard"}]`):
     - Optional `conversationId`. Resolves target conversation via argument -> `_meta["antigravity.google/conversation_id"]` -> `ANTIGRAVITY_CONVERSATION_ID` -> `lastActiveConversationId` -> most recent `transcript.jsonl`.
     - Returns concise text summary in `content` and full structured dashboard payload in `structuredContent`.
  2. `context_overview` (visibility: `model`, `app`; `_meta.ui.resourceUri = "ui://context-guard/dashboard.html"`, entrypoints: `[{"type": "global", "title": "Context Guard"}]`):
     - Lists up to 20 most recent conversations across App/CLI/IDE with live token usage, % of threshold, mode, and health status.
  3. `context_configure` (visibility: `model`, `app`):
     - Updates `thresholdTokens`, `warnPercent`, `mode`, `enabled`, or per-conversation `snoozeTokens` / `resetSnooze`.
  4. `context_handoff` (visibility: `model`, `app`):
     - Generates a structured continuation brief for a conversation.
  5. `app_save_settings`, `app_snooze`, `app_refresh`, `app_build_handoff` (visibility: `["app"]` only):
     - Fast UI-driven tools called by the MCP App via `app.callServerTool`.

### 5.4 MCP App UI (`ui-src/src/main.ts` -> `ui/app.html`)
- Built with `@modelcontextprotocol/ext-apps` (`App`, `applyDocumentTheme`, `applyHostStyleVariables`).
- Uses Antigravity CSS theme variables (`--color-background-*`, `--color-text-*`, `--color-border-*`, `--font-sans`) with clean fallbacks so both Light and Dark themes look native.
- Compact, high-signal layout:
  - Header: Status pill (`OK` / `WARN` / `LIMIT`), model badge, conversation selector, live auto-refresh toggle.
  - Primary Gauge + Progress Bar (`current / threshold` + `current / modelWindow`), remaining tokens & estimated turns left.
  - 4 Metric Cards: `Input`, `Cache Read`, `Output`, `Cache Hit %`.
  - Turn-by-Turn SVG Growth Sparkline with dashed threshold line.
  - Bloat Inspector table (heaviest steps/tools since last compaction).
  - Action Bar (capability-gated via `app.getHostCapabilities()`):
    - `Try /compact` (`app.sendMessage`)
    - `Snooze +50k` / `Clear Snooze` (`app.callServerTool`)
    - `Smart Handoff` (builds brief via `app_build_handoff` -> sends to new chat via `app.sendMessage` with `_meta: {"com.google.antigravity/message": {"target": "new"}}`, with copy-to-clipboard fallback!).
    - `Settings` inline drawer (`threshold`, `warn %`, `mode`: `warn` / `stop` / `gate`).

### 5.5 Skill (`skills/context-guard/SKILL.md`) & Docs (`README.md`)
- Skill triggers on `/context-guard` or questions about context usage, token bloat, threshold config, or session handoff.
- `README.md` covers features, installation (`agy plugin install`, manual copy), configuration, dashboard guide, and limitations.

## 6. Verification Strategy
- Unit tests (`tests/test_context_core.py`): token math, compaction detection, bloat ranking, atomic config, handoff brief.
- Hook tests (`tests/test_hook.py`): `SessionStart`, `PreInvocation`, `PostInvocation`, `Stop`, `/compact` bypass, dedup across turns, mode behavior (`warn`, `stop`, `gate`), snooze.
- MCP Server tests (`tests/test_mcp_server.py`): JSON-RPC `initialize`, `tools/list`, `resources/list`, `resources/read`, `tools/call` across all tools & `_meta` resolution.
- Strict Antigravity validation: `agy plugin validate --strict /Users/user/Development/Plugins/context-guard` must exit `0` with `0` errors and `0` warnings.
- UI TypeScript typecheck & single-file `esbuild` bundle check.
