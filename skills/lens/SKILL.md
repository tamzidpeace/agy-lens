---
name: lens
description: >-
  Use when the user invokes /lens (or legacy /context-guard) or asks about
  context window usage, token bloat, quota burn-rate, cross-chat search,
  setup footprint (skills, rules, MCPs, plugins), alert thresholds, loop
  guardrails, notifications, or preparing a session handoff.
---

# Agy Lens

Unified Antigravity observability & guardrails side pane (`monitor` MCP server). Compatible with both `/lens` and legacy `/context-guard` invocations without bloating the active chat.

## Five Observability & Guardrail Pillars

1. **Monitor**: Real-time context window usage (`effectiveTokens / effectiveThresholdTokens`), cache hit rate, bloat attribution by category and step, configurable threshold enforcement (`warn` / `stop` / `gate`), temporary snoozing, and one-click **Smart Handoff**.
2. **Quotas & Burn-Rate**: Tracks Antigravity model quota pools (`5h`, `Weekly`), burn-rate projections, and low-quota warnings (`notifyOnLowQuotaPct`).
3. **Cross-Chat Search**: Searches across Antigravity transcripts, titles, and tool activity without loading full transcripts into the active conversation.
4. **Setup Footprint**: Inspects installed Antigravity customizations (skills, rules, workflows, MCP servers, hooks, and plugins) and their baseline context footprint.
5. **Loop Guard & Alerts**: Detects repeated tool-call loops (`loopGuard`: `consecutiveThreshold`, `nudgeOncePerStreak`, `autoPauseThreshold`) and triggers configurable sound/desktop alerts with per-kind cooldowns (`alerts` in `~/.gemini/config/agy-lens.json`, transparently migrating from legacy `~/.gemini/config/context-guard.json`).

## Tool Mapping

Call the model-visible tools from the `monitor` server based on user intent:

| User Intent | Tool | Key Arguments | Behavior |
|---|---|---|---|
| Check current usage, remaining turns, or what is eating tokens | `context_status` | `conversationId?` | Opens the **Agy Lens** side pane (`aux_pane`) and returns telemetry (`effectiveTokens`, `effectiveThresholdTokens`, `thresholdPercent`, `status`, `bloatByCategory`, `topBloatSteps`). |
| Compare sessions across App, CLI, and IDE | `context_overview` | `limit?` (`1`–`100`, default `20`) | Opens the global multi-conversation view and lists recent sessions with live token counts and statuses. |
| Change threshold, warn %, mode, hooks, auto-refresh, or snooze | `context_configure` | `thresholdTokens?` (`>= 1000`), `warnPercent?` (`1`–`99`), `mode?` (`"warn"` \| `"stop"` \| `"gate"`), `enabled?`, `autoRefreshSeconds?` (`1`–`300`), `conversationId?`, `customThresholdTokens?` (`0` clears), `snoozeTokens?`, `resetSnooze?` | Atomically updates `~/.gemini/config/agy-lens.json` (with file locking) and returns refreshed status. |
| Prepare a fresh-chat continuation prompt | `context_handoff` | `conversationId?`, `note?` | Generates a structured `# Session Continuation Handoff` brief from the original goal, recent prompts, files touched, commands run, and current metrics. |

> **Note:** `app_refresh`, `app_save_settings`, `app_snooze`, `app_build_handoff`, and `app_quota` are internal UI-only tools (`visibility: ["app"]`). Use only the model-visible tools above.

## Enforcement Modes

- `warn`: Injects a single concise warning once per compaction epoch without stopping turns.
- `stop` *(default)*: Injects a concise alert once per compaction epoch and terminates cleanly at turn end (`PostInvocation`) once `effectiveTokens >= effectiveThresholdTokens`.
- `gate`: Hard-blocks new turns (`PreInvocation`) at the threshold until the user compacts, snoozes, raises the threshold, or issues a safe bypass command (`/compact`, `/btw`, `/rewind`, `/new`, `/clear`).

## Required Context Hygiene

1. **Never dump large raw transcripts or full JSONL step logs into chat.** Doing so directly inflates the active context window.
2. **Summarize concisely:** Report `effectiveTokens / effectiveThresholdTokens` (`thresholdPercent%`), `cacheHitPercent%`, `estimatedTurnsRemaining`, and the top contributors from `bloatByCategory` / `topBloatSteps`. Let the **Agy Lens** side pane display the full sparkline and step table.
3. **When near or at the limit (`status` is `"warn"` or `"limit"`):**
   - Recommend running `/compact` in the chat composer (never blocked by hooks), **or**
   - Recommend clicking **Smart Handoff** in the **Agy Lens** side pane (which sends a continuation brief to a fresh conversation, with clipboard copy fallback) — or invoke `context_handoff` if the user asks for a copyable brief in chat.
   - If the user wants to keep working past the current threshold temporarily, offer to snooze via `context_configure` (`snoozeTokens`, e.g. `50000`) or the dashboard's **Snooze +50k** button.
