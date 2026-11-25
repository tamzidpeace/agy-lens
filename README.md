# AGY Context Guard (`agy-context-guard`)

<p align="left">
  <img src="assets/logo.svg" width="64" height="64" alt="Context Guard logo" />
</p>

Real-time context window monitor, token bloat inspector, and configurable threshold guard for **Antigravity** (Desktop App, CLI `agy`, and IDE), featuring a live interactive MCP App dashboard and one-click Smart Handoff.

---

## Why Context Guard?

Long-running Antigravity sessions accumulate tokens across file reads, terminal outputs, search results, and multi-turn reasoning. Waiting until the model hits its hard window limit (e.g., ~1M tokens for Gemini models or 200k for Claude) increases latency, degrades reasoning quality ("context rot"), and triggers lossy background compaction mid-task.

`agy-context-guard` observes exact per-turn token metrics from `transcript.jsonl`, warns or pauses at a threshold you control (default `300,000` tokens), attributes bloat to specific tool calls, and helps you compact or hand off to a clean conversation in one click.

## Features

- **Exact Per-Turn Telemetry:** Parses `PLANNER_RESPONSE` token counts (`input_tokens + cache_read_tokens + output_tokens`) and estimates pending post-response deltas (`len(content) // 4`) from Antigravity `transcript.jsonl` logs.
- **Automatic Compaction Tracking:** Detects compaction events via `>= 35%` prompt-token drops (when prior total `>= 15,000` tokens) or `<CONTEXT_SUMMARY>` markers, resetting bloat attribution and re-arming alert deduplication per compaction epoch.
- **Configurable Guardrails (`warn`, `stop`, `gate`):** Fires concise warnings **at most once per compaction epoch**, always fails open on errors (exit `0`), and never blocks `/compact`, `/btw`, `/rewind`, `/new`, or `/clear`.
- **Interactive MCP App Dashboard:** Live side-pane (`aux_pane`) and multi-conversation (`global`) dashboard with SVG turn-by-turn sparklines, Bloat Inspector, Light/Dark host theme integration, and 5-second auto-refresh.
- **One-Click Smart Handoff:** Extracts the original goal, last 3 user prompts, up to 15 recent files, and up to 10 recent commands into a clean continuation brief sent directly to a fresh conversation (with clipboard copy fallback).

## Zero-Runtime-Dependency Architecture

End users need **zero** `npm install` or `pip install` steps at runtime:

- **Pure Python 3.9+ Stdlib Backend:** `scripts/context_core.py`, `scripts/hook.py`, and `server/mcp_server.py` depend exclusively on the Python standard library — reliable even when Node.js lives inside `nvm` outside GUI `PATH`.
- **Pre-Bundled Single-File MCP App UI:** `ui/app.html` (`text/html;profile=mcp-app`, under 512 KiB) is pre-compiled from TypeScript (`ui-src/`) with inline CSS and JavaScript and zero external `<script src>` or `<link>` tags, complying with Antigravity's strict default CSP (`default-src 'none'`).

### Repository Layout

```text
agy-context-guard/
├── plugin.json                 # Plugin manifest (name: agy-context-guard)
├── mcp_config.json             # Launches python3 ${PLUGIN_ROOT}/server/mcp_server.py
├── hooks.json                  # SessionStart, PreInvocation, PostInvocation, Stop hooks
├── README.md                   # Documentation
├── assets/
│   └── logo.svg                # 128x128 plugin icon
├── scripts/
│   ├── context_core.py         # Transcript analyzer, config store, bloat & handoff engine
│   └── hook.py                 # Fail-open lifecycle hook entrypoint
├── server/
│   └── mcp_server.py           # Zero-dep stdio JSON-RPC 2.0 MCP server & MCP App host
├── skills/
│   └── context-guard/
│       └── SKILL.md            # /context-guard skill for chat usage
├── ui/
│   └── app.html                # Pre-bundled single-file HTML dashboard
├── ui-src/                     # Dev-only TypeScript source & esbuild bundler
│   ├── build.mjs
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/main.ts
│   └── tests/ui.test.mjs
└── tests/                      # Python unittest suite
    ├── test_context_core.py
    ├── test_hook.py
    └── test_mcp_server.py
```

## Installation

### Option 1: Antigravity CLI (`agy`)

Clone the repository and install the local plugin directory:

```bash
git clone https://github.com/tamzidpeace/agy-context-guard.git
agy plugin install ./agy-context-guard
```

*(On Antigravity CLI versions that support direct GitHub URL or Marketplace installs, you can also use `agy plugin install https://github.com/tamzidpeace/agy-context-guard`.)*

### Option 2: Manual Plugin Directory Copy

Clone or copy the plugin directly into Antigravity's plugins directory:

```bash
git clone https://github.com/tamzidpeace/agy-context-guard.git ~/.gemini/config/plugins/agy-context-guard
```

Verify the plugin is recognized and passes strict validation:

```bash
agy plugin list
agy plugin validate --strict ~/.gemini/config/plugins/agy-context-guard
```

## Lifecycle Hooks & Enforcement Modes

`hooks.json` registers `python3 ./scripts/hook.py` (5-second timeout) on `SessionStart`, `PreInvocation`, `PostInvocation`, and `Stop`.

| Event / State | `warn` | `stop` *(default)* | `gate` |
|---|---|---|---|
| **Approaching (`>= warnPercent`, default `80%`)** | Injects 1 concise `systemMessage` per epoch | Injects 1 concise `systemMessage` per epoch | Injects 1 concise `systemMessage` per epoch |
| **At/Above Threshold on `PostInvocation`** | Injects 1 concise alert per epoch; continues | Injects 1 concise alert per epoch and returns `"terminationBehavior": "terminate"` to pause cleanly at turn end | Injects 1 concise alert per epoch and returns `"terminationBehavior": "terminate"` |
| **At/Above Threshold on `PreInvocation`** | Injects 1 concise alert per epoch if not yet alerted | Injects 1 concise alert per epoch if not yet alerted | Hard-blocks turn (`errorMessage` + `"terminationBehavior": "terminate"`) until `/compact`, `/new`, threshold increase, or snooze |

### Safe Slash-Command Bypass

When `lastUserInput` begins with any of the following commands, hooks **never** block or inject warning messages — even in `gate` mode:

- `/compact`
- `/btw`
- `/rewind`
- `/new`
- `/clear`

### Deduplication & Epoch Reset

Each conversation tracks `warnedAtEpoch` and `alertedAtEpoch` against `compactionCount`. Warnings fire at most once per compaction epoch rather than repeating on every turn, and automatically re-arm whenever compaction happens or you raise the threshold / snooze limit.

## Configuration

Settings and per-conversation state are persisted atomically in `~/.gemini/config/context-guard.json` (overridable via `CONTEXT_GUARD_CONFIG_PATH`).

### Default Config Format

```json
{
  "enabled": true,
  "thresholdTokens": 300000,
  "warnPercent": 80,
  "mode": "stop",
  "modelWindows": {
    "gemini-4": 1048576,
    "gemini-3": 1048576,
    "claude": 200000,
    "gpt-4o": 128000,
    "default": 1048576
  },
  "lastActiveConversationId": null,
  "conversations": {}
}
```

### Fields Reference

- `enabled` (`boolean`): Master toggle for lifecycle hook enforcement (default `true`).
- `thresholdTokens` (`integer`, min `1000`): Global token alert threshold (default `300000`).
- `warnPercent` (`integer`, `1`–`99`): Percentage of the effective threshold that triggers early warning (default `80`).
- `mode` (`"warn" | "stop" | "gate"`): Enforcement behavior when reaching the threshold (default `"stop"`).
- `modelWindows` (`object`): Model substring/key to context window size mapping used for the secondary `Model Window` gauge.
- `conversations.<conversationId>`: Per-session state (`transcriptPath`, `lastCompactionIndex`, `warnedAtEpoch`, `alertedAtEpoch`, `snoozedUntilTokens`, `customThresholdTokens`, `lastTokens`, `lastModel`, `updatedAt`). Automatically pruned above `200` stored entries while retaining the active, custom-threshold, and snoozed sessions.

### Transcript Discovery & Environment Overrides

By default, Context Guard searches `ANTIGRAVITY_APP_DATA_DIR` (if set), `~/.gemini/antigravity`, `~/.gemini/antigravity-cli`, and `~/.gemini/antigravity-ide` for `brain/<conversationId>/.system_generated/logs/transcript.jsonl`.

| Environment Variable | Purpose |
|---|---|
| `CONTEXT_GUARD_CONFIG_PATH` | Override config file path (default `~/.gemini/config/context-guard.json`) |
| `ANTIGRAVITY_APP_DATA_DIR` | Primary Antigravity data directory to search before defaults |
| `CONTEXT_GUARD_BRAIN_ROOTS` | `os.pathsep`-delimited override list of brain root directories |
| `ANTIGRAVITY_CONVERSATION_ID` | Fallback active conversation ID when omitted from tool args/`_meta` |
| `CONTEXT_GUARD_APP_HTML` | Override path for the dashboard HTML bundle (default `ui/app.html`) |
| `CONTEXT_GUARD_DATA_DIR` | Populated by `mcp_config.json` from `${PLUGIN_DATA}` |

## MCP Tools & Dashboard Guide

The `monitor` MCP server exposes the resource `ui://context-guard/dashboard.html` and the following tools:

### Model & App Tools

| Tool | UI Entrypoint | Parameters | Description |
|---|---|---|---|
| `context_status` | `aux_pane` (*Context Guard*) | `conversationId?` | Opens the side-pane dashboard for the active (or specified) conversation and returns live token, turn, and bloat telemetry. |
| `context_overview` | `global` (*Context Guard*) | `limit?` (`1`–`100`, default `20`) | Lists recent conversations across Antigravity App, CLI, and IDE with live usage bars and health pills. |
| `context_configure` | — | `thresholdTokens?`, `warnPercent?`, `mode?`, `enabled?`, `conversationId?`, `customThresholdTokens?` (`0` clears), `snoozeTokens?`, `resetSnooze?` | Persists global or per-conversation settings and returns updated status. |
| `context_handoff` | — | `conversationId?`, `note?` | Generates a structured continuation brief for starting a fresh chat cleanly. |

Internal UI-only tools (`visibility: ["app"]`): `app_refresh`, `app_save_settings`, `app_snooze`, and `app_build_handoff`.

### Dashboard Panels & Controls

- **Header:** Status pill (`OK`, `WARN`, `LIMIT`, `DISABLED`, `NO DATA`), active mode badge, `Overview` / `Detail` toggle, `Refresh`, and `Auto 5s` live polling toggle.
- **Gauges & Metrics:** `Guard Threshold` progress bar (with early-warn tick and estimated turns remaining), `Model Window` bar, and 4 breakdown cards (`Input`, `Cache Read`, `Output`, `Cache Hit %`).
- **Turn-by-Turn Growth Sparkline:** SVG trajectory across completed `PLANNER_RESPONSE` turns with dashed warn and threshold lines.
- **Bloat Inspector:** Category distribution (`User`, `Model`, `Tool Outputs`, `System`) plus the top 8 heaviest steps since the latest compaction with tool attribution.
- **Action Bar (capability-gated via `app.getHostCapabilities()`):**
  - **Try /compact:** Sends `/compact` to the active conversation via `app.sendMessage`.
  - **Snooze +50k / Clear Snooze:** Adds `+50,000` tokens above `max(effectiveTokens, currentThreshold)` or clears active snooze.
  - **Smart Handoff:** Builds a continuation brief (with optional custom note) and sends it directly to a new conversation (`target: "new"`), with one-click **Copy Brief** fallback.
  - **Attach Summary:** Attaches a compact telemetry snapshot via `app.updateModelContext` when supported by the host.
  - **Settings:** Inline drawer with threshold presets (`150k`, `200k`, `300k`, `500k`), custom token input, warn %, mode switcher (`WARN` / `STOP` / `GATE`), and hook toggle.

### Example Chat Prompts

- *"Show my current context window usage and what is eating tokens"*
- *"Set my Context Guard alert threshold to 300k tokens"*
- *"Switch Context Guard to gate mode"*
- *"Prepare a clean handoff brief to continue this task in a fresh chat"*

## Known Platform Limitation

Antigravity does not currently expose a silent programmatic RPC from lifecycle hooks or MCP servers to trigger host-side compaction without user or composer action.

Context Guard addresses this cleanly without trapping your workflow:

1. **Never blocks `/compact`:** Safe slash-command bypass lets you compact anytime, even when `gate` mode has paused the session.
2. **Clean Turn Stops (`stop` / `gate`):** Prevents runaway multi-turn tool loops from burning past your quality threshold.
3. **One-Click Recovery:** Use **Try /compact** (dispatched via `app.sendMessage` when supported) or **Smart Handoff** to seamlessly transition into a fresh chat with preserved context before automatic lossy compaction degrades reasoning.

## Development & Rebuilding `ui/app.html`

End users do not need Node.js. If you modify the dashboard UI under `ui-src/`:

```bash
cd ui-src
npm install
npm run typecheck
npm test
npm run build
```

`npm run build` runs `esbuild` via `ui-src/build.mjs`, bundles `ui-src/src/main.ts` into the single-file `ui/app.html`, and enforces single-file CSP compliance and the `< 512 KiB` size budget.

To run the complete Python test suite and strict plugin validation from the plugin root:

```bash
python3 -m unittest discover -s tests -v
agy plugin validate --strict --json .
```

## License

MIT
