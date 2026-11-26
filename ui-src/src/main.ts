import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostCapabilities,
  type McpUiHostContext,
  type McpUiStyles,
  type McpUiTheme,
} from "@modelcontextprotocol/ext-apps";

export type GuardStatus = "ok" | "warn" | "limit" | "disabled" | "no_data";
export type GuardMode = "warn" | "stop" | "gate";
export type BloatCategoryKey = "user" | "model" | "toolOutputs" | "system";
export type ViewMode = "status" | "search" | "footprint" | "overview";
export type SearchScope = "all" | "messages" | "artifacts" | "files";

export interface QuotaPrediction {
  state: "warming" | "idle" | "active";
  burnRatePerHour: number | null;
  hoursUntilExhausted: number | null;
  hoursUntilReset: number | null;
  bufferHours: number | null;
  riskLevel: "unknown" | "safe" | "tight" | "at_risk";
  sampleCount: number;
  windowObservedSeconds: number;
  usedDeltaPct: number;
}

export interface ActiveLoopState {
  signature: string;
  toolName: string;
  count: number;
  reason: string;
  lastErrorSnippet?: string;
}

export interface LoopGuardConfig {
  enabled: boolean;
  consecutiveThreshold: number;
  nudgeOncePerStreak?: boolean;
  autoPauseThreshold: number;
}

export interface AlertsConfig {
  soundEnabled: boolean;
  desktopNotificationEnabled: boolean;
  notifyOnStop: boolean;
  notifyOnThreshold: boolean;
  notifyOnLoop: boolean;
  notifyOnLowQuotaPct: number;
  cooldownSeconds?: number;
}

export interface SearchSnippet {
  source: string;
  label: string;
  text: string;
  spans: Array<[number, number]>;
}

export interface SearchResultItem {
  convId: string;
  title: string;
  updatedAt: string;
  sourceApp?: string;
  isSubagent?: boolean;
  matchCount: number;
  editedFiles: string[];
  snippets: SearchSnippet[];
}

export interface SearchPayload {
  view: "search";
  query: string;
  scope: SearchScope;
  includeSubagents: boolean;
  totalIndexed: number;
  matchedCount: number;
  results: SearchResultItem[];
}

export interface FootprintWarning {
  code: string;
  severity: "warning" | "info";
  title: string;
  detail: string;
  path?: string;
}

export interface FootprintRuleItem {
  name: string;
  scope: string;
  path: string;
  bytes: number;
  estTokens: number;
}

export interface FootprintSkillItem {
  name: string;
  description: string;
  scope: string;
  source: string;
  path: string;
  frontmatterTokens: number;
  bodyTokens: number;
  enabled: boolean;
}

export interface FootprintMcpItem {
  name: string;
  scope: string;
  source: string;
  enabled: boolean;
  commandPreview: string;
  envKeys: string[];
  envKeyCount: number;
  estTokens: number;
  path: string;
}

export interface FootprintHookItem {
  name: string;
  scope: string;
  enabled: boolean;
  events: string[];
  timeoutSeconds: number;
  path: string;
}

export interface FootprintPayload {
  view: "footprint";
  workspacePath: string;
  summary: {
    estimatedStartupTokens: number;
    onDemandSkillsTokens: number;
    observedStartupTokens: number | null;
    rulesCount: number;
    pluginsCount: number;
    skillsCount: number;
    builtinSkillsCount: number;
    mcpServersCount: number;
    hooksCount: number;
  };
  builtinSkillsSummary?: {
    count: number;
    frontmatterTokens: number;
    onDemandTokens: number;
  };
  warnings: FootprintWarning[];
  rules: FootprintRuleItem[];
  plugins: Array<{ id: string; name: string; version: string; enabled: boolean; scope: string; path: string }>;
  skills: FootprintSkillItem[];
  mcpServers: FootprintMcpItem[];
  hooks: FootprintHookItem[];
}

export interface HistoryPoint {
  stepIndex: number;
  inputTokens?: number;
  cacheReadTokens?: number;
  outputTokens?: number;
  totalTokens: number;
  deltaTokens: number;
  model?: string;
  createdAt?: string;
}

export interface BloatStep {
  stepIndex: number;
  source: string;
  type: string;
  category: string;
  tools: string[];
  estimatedTokens: number;
  snippet: string;
}

export interface BloatByCategory {
  user: number;
  model: number;
  toolOutputs: number;
  system: number;
}

export interface QuotaBucket {
  id: string;
  window: string;
  label: string;
  remainingFraction: number;
  remainingPercent: number;
  resetTime: string;
  prediction?: QuotaPrediction;
}

export interface QuotaGroup {
  name: string;
  modelsSummary?: string;
  buckets: QuotaBucket[];
}

export interface ModelQuotaPayload {
  available: boolean;
  status: "ok" | "unavailable" | "error" | "idle";
  source?: string;
  fetchedAt?: number;
  stale?: boolean;
  fetching?: boolean;
  ageSeconds?: number | null;
  description?: string;
  groups: QuotaGroup[];
  error?: string;
}

export interface StatusPayload {
  view: "status";
  conversationId: string | null;
  title?: string;
  agentName?: string;
  parentConversationId?: string;
  isSubagent?: boolean;
  status: GuardStatus;
  enabled: boolean;
  mode: GuardMode;
  warnPercent: number;
  autoRefreshSeconds?: number;
  model: string;
  effectiveTokens: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  pendingDeltaTokens: number;
  cacheHitPercent: number;
  effectiveThresholdTokens: number;
  warnThresholdTokens: number;
  baseThresholdTokens: number;
  snoozedUntilTokens: number;
  modelWindowTokens: number;
  thresholdPercent: number;
  windowPercent: number;
  remainingToThreshold: number;
  avgDeltaPerTurn: number;
  estimatedTurnsRemaining: number | null;
  compactionCount: number;
  activeEpochStep?: number;
  history: HistoryPoint[];
  bloatByCategory: BloatByCategory;
  topBloatSteps: BloatStep[];
  modelQuota?: ModelQuotaPayload;
  activeLoop?: ActiveLoopState | null;
  loopGuard?: LoopGuardConfig;
  alerts?: AlertsConfig;
}

export interface OverviewConversation {
  conversationId: string;
  title?: string;
  agentName?: string;
  parentConversationId?: string;
  isSubagent?: boolean;
  sourceApp: string;
  model: string;
  effectiveTokens: number;
  thresholdTokens: number;
  thresholdPercent: number;
  status: GuardStatus;
  compactionCount: number;
  mtime: number;
  transcriptPath: string;
}

export interface OverviewPayload {
  view: "overview";
  enabled: boolean;
  mode: GuardMode;
  thresholdTokens: number;
  warnPercent: number;
  autoRefreshSeconds?: number;
  conversations: OverviewConversation[];
  active: OverviewConversation | null;
  modelQuota?: ModelQuotaPayload;
}

export interface HandoffPayload {
  conversationId: string | null;
  effectiveTokens: number;
  thresholdTokens: number;
  brief: string;
}

export interface SparklinePoint {
  x: number;
  y: number;
  stepIndex: number;
  totalTokens: number;
  deltaTokens: number;
}

export interface SparklineModel {
  width: number;
  height: number;
  points: SparklinePoint[];
  linePath: string;
  areaPath: string;
  thresholdY: number;
  warnY: number | null;
  maxYValue: number;
  peakTokens: number;
}

export type ToolResultLike = {
  isError?: boolean;
  content?: Array<{ type?: string; text?: string } | Record<string, unknown>>;
  structuredContent?: unknown;
};

export type DashboardAppClient = Pick<
  App,
  | "ontoolinput"
  | "ontoolresult"
  | "ontoolcancelled"
  | "onhostcontextchanged"
  | "connect"
  | "getHostContext"
  | "getHostCapabilities"
  | "callServerTool"
  | "sendMessage"
  | "updateModelContext"
>;

export interface MountDependencies {
  app: DashboardAppClient;
  doc?: Document;
  applyTheme?: (theme: McpUiTheme) => void;
  applyStyles?: (styles: McpUiStyles, root?: HTMLElement) => void;
  applyFonts?: (fontCss: string) => void;
  autoFetchDelayMs?: number;
  autoRefresh?: boolean;
  autoRefreshIntervalMs?: number;
}

export interface DashboardController {
  dispose: () => void;
  refreshCurrentView: (skipIfBusy?: boolean) => Promise<void>;
  switchToOverview: () => Promise<void>;
  switchToSearch: () => Promise<void>;
  switchToFootprint: (workspacePath?: string) => Promise<void>;
  runSearch: (query?: string, scope?: SearchScope, includeSubagents?: boolean) => Promise<void>;
  refreshFootprint: (workspacePath?: string) => Promise<void>;
  clearActiveLoop: () => Promise<void>;
  triggerTestAlert: () => Promise<void>;
  selectConversation: (conversationId: string) => Promise<void>;
  triggerSnooze: (deltaTokens: number) => Promise<void>;
  triggerResetSnooze: () => Promise<void>;
  triggerCompact: () => Promise<void>;
  triggerHandoff: (note?: string, autoSend?: boolean) => Promise<void>;
  triggerAttachSummary: () => Promise<void>;
  refreshQuota: () => Promise<void>;
  saveSettings: (draft: SettingsDraft) => Promise<void>;
  showStatusView: () => void;
}

interface SettingsDraft {
  enabled: boolean;
  thresholdTokens: number;
  warnPercent: number;
  mode: GuardMode;
  autoRefreshSeconds?: number;
  loopGuard?: LoopGuardConfig;
  alerts?: AlertsConfig;
}

interface BannerState {
  kind: "error" | "info" | "success";
  text: string;
}

interface DashboardState {
  connected: boolean;
  loading: boolean;
  autoRefresh: boolean;
  autoRefreshSeconds: number;
  currentView: ViewMode;
  userSwitchedView: boolean;
  hasInitialToolResult: boolean;
  activeConversationId: string | null;
  overviewLimit: number;
  statusData: StatusPayload | null;
  overviewData: OverviewPayload | null;
  searchData: SearchPayload | null;
  footprintData: FootprintPayload | null;
  searchQuery: string;
  searchScope: SearchScope;
  searchIncludeSubagents: boolean;
  footprintWorkspaceInput: string;
  quotaData: ModelQuotaPayload | null;
  handoffData: HandoffPayload | null;
  handoffSentToNewChat: boolean;
  handoffNote: string;
  handoffOpen: boolean;
  settingsOpen: boolean;
  settingsDraft: SettingsDraft;
  banner: BannerState | null;
  hostCapabilities: McpUiHostCapabilities | undefined;
  hostContext: McpUiHostContext | undefined;
  overviewPrefetched: boolean;
  hideSubagents: boolean;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const DEFAULT_AUTO_REFRESH_SECONDS = 5;
const MIN_AUTO_REFRESH_SECONDS = 1;
const MAX_AUTO_REFRESH_SECONDS = 300;
const AUTO_REFRESH_PRESETS = [2, 5, 10, 30, 60] as const;
const SNOOZE_STEP_TOKENS = 50_000;
const MIN_THRESHOLD_TOKENS = 1_000;
const THRESHOLD_PRESETS = [150_000, 200_000, 300_000, 500_000] as const;

const DEFAULT_LOOP_GUARD_DRAFT: LoopGuardConfig = {
  enabled: true,
  consecutiveThreshold: 3,
  nudgeOncePerStreak: true,
  autoPauseThreshold: 5,
};

const DEFAULT_ALERTS_DRAFT: AlertsConfig = {
  soundEnabled: true,
  desktopNotificationEnabled: true,
  notifyOnStop: true,
  notifyOnThreshold: true,
  notifyOnLoop: true,
  notifyOnLowQuotaPct: 15,
  cooldownSeconds: 15,
};

const MODE_DESCRIPTIONS: Record<GuardMode, string> = {
  warn: "Warn once per compaction epoch without interrupting turns.",
  stop: "Pause cleanly at turn end (PostInvocation) once threshold is reached.",
  gate: "Block new model invocations (PreInvocation) until /compact or snooze.",
};

const CATEGORY_META: Array<{
  key: BloatCategoryKey;
  label: string;
  colorVar: string;
}> = [
  { key: "user", label: "User", colorVar: "var(--cg-cat-user)" },
  { key: "model", label: "Model", colorVar: "var(--cg-cat-model)" },
  { key: "toolOutputs", label: "Tool Outputs", colorVar: "var(--cg-cat-tools)" },
  { key: "system", label: "System", colorVar: "var(--cg-cat-system)" },
];

/** Escape special HTML characters strictly for defensive string rendering. */
export function escapeHtml(value: string): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/`/g, "&#96;");
}

/** Clamp an auto-refresh interval (in seconds) within safe bounds. */
export function normalizeAutoRefreshSeconds(
  value: unknown,
  fallback = DEFAULT_AUTO_REFRESH_SECONDS,
): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(
    MIN_AUTO_REFRESH_SECONDS,
    Math.min(MAX_AUTO_REFRESH_SECONDS, Math.round(value)),
  );
}

/** Check whether an unknown value is a non-null plain record. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Validate a StatusPayload structuredContent object from mcp_server.py. */
export function isStatusPayload(value: unknown): value is StatusPayload {
  return (
    isRecord(value) &&
    value.view === "status" &&
    typeof value.effectiveTokens === "number"
  );
}

/** Validate an OverviewPayload structuredContent object from mcp_server.py. */
export function isOverviewPayload(value: unknown): value is OverviewPayload {
  return (
    isRecord(value) &&
    value.view === "overview" &&
    Array.isArray(value.conversations)
  );
}

/** Validate a HandoffPayload structuredContent object from mcp_server.py. */
export function isHandoffPayload(value: unknown): value is HandoffPayload {
  return isRecord(value) && typeof value.brief === "string";
}

/** Validate a SearchPayload structuredContent object from mcp_server.py. */
export function isSearchPayload(value: unknown): value is SearchPayload {
  return (
    isRecord(value) &&
    value.view === "search" &&
    Array.isArray(value.results)
  );
}

/** Validate a FootprintPayload structuredContent object from mcp_server.py. */
export function isFootprintPayload(value: unknown): value is FootprintPayload {
  return (
    isRecord(value) &&
    value.view === "footprint" &&
    isRecord(value.summary)
  );
}

export function formatDurationHours(hours: number | null | undefined): string {
  if (typeof hours !== "number" || !Number.isFinite(hours) || hours < 0) {
    return "";
  }
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function formatQuotaPrediction(pred: QuotaPrediction | undefined): {
  text: string;
  toneClass: string;
} | null {
  if (!pred) return null;
  if (pred.state === "warming") {
    return { text: "Collecting burn-rate baseline...", toneClass: "cg-quota-pred" };
  }
  if (pred.state === "idle") {
    return { text: "Idle burn (0%/hr) · Safe", toneClass: "cg-quota-pred cg-quota-pred-safe" };
  }
  const rate = pred.burnRatePerHour;
  if (typeof rate !== "number" || !Number.isFinite(rate) || rate <= 0) {
    return null;
  }
  const rateStr = `${Number.isInteger(rate) ? rate : rate.toFixed(1)}%/hr`;
  if (pred.riskLevel === "at_risk" && pred.hoursUntilExhausted !== null) {
    return {
      text: `Burn ~${rateStr} · Runs out ~${formatDurationHours(pred.hoursUntilExhausted)} before reset`,
      toneClass: "cg-quota-pred cg-quota-pred-risk",
    };
  }
  if (pred.riskLevel === "tight" && pred.bufferHours !== null) {
    return {
      text: `Burn ~${rateStr} · Tight (~${formatDurationHours(Math.max(0, pred.bufferHours))} buffer)`,
      toneClass: "cg-quota-pred cg-quota-pred-tight",
    };
  }
  if (pred.riskLevel === "safe") {
    const buf =
      pred.bufferHours !== null && pred.bufferHours > 0
        ? ` (~${formatDurationHours(pred.bufferHours)} buffer)`
        : "";
    return {
      text: `Burn ~${rateStr} · Safe until reset${buf}`,
      toneClass: "cg-quota-pred cg-quota-pred-safe",
    };
  }
  return {
    text: `Burn ~${rateStr}${pred.hoursUntilExhausted !== null ? ` · Est. ~${formatDurationHours(pred.hoursUntilExhausted)} left` : ""}`,
    toneClass: "cg-quota-pred",
  };
}

/** Format ISO reset timestamp into a concise "Resets in ..." string when active. */
export function formatQuotaReset(
  resetTimeIso: string | undefined,
  remainingPercent: number,
  nowMs = Date.now(),
): string {
  if (!resetTimeIso || remainingPercent >= 99.95) return "";
  const targetMs = Date.parse(resetTimeIso);
  if (!Number.isFinite(targetMs)) return "";
  const diffSec = Math.max(0, Math.floor((targetMs - nowMs) / 1000));
  if (diffSec < 60) return "Resetting soon";
  const totalMins = Math.floor(diffSec / 60);
  const days = Math.floor(totalMins / (24 * 60));
  const hours = Math.floor((totalMins % (24 * 60)) / 60);
  const mins = totalMins % 60;
  if (days > 0) return `Resets in ${days}d ${hours}h`;
  if (hours > 0) return `Resets in ${hours}h ${mins}m`;
  return `Resets in ${mins}m`;
}

/** Format cached quota age concisely ("just now", "2m ago", "1h ago"). */
export function formatQuotaAge(ageSeconds: number | null | undefined): string {
  if (typeof ageSeconds !== "number" || !Number.isFinite(ageSeconds) || ageSeconds < 0) {
    return "";
  }
  if (ageSeconds < 60) return "just now";
  const mins = Math.floor(ageSeconds / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ago`;
}

/** Extract concise error text from an MCP CallToolResult or Error. */
export function extractToolErrorText(
  resultOrError: unknown,
  fallback = "Request failed",
): string {
  if (resultOrError instanceof Error) {
    return resultOrError.message || fallback;
  }
  if (isRecord(resultOrError) && Array.isArray(resultOrError.content)) {
    const texts: string[] = [];
    for (const block of resultOrError.content) {
      if (isRecord(block) && typeof block.text === "string" && block.text.trim()) {
        texts.push(block.text.trim());
      }
    }
    if (texts.length > 0) {
      return texts.join(" ");
    }
  }
  return fallback;
}

/** Format token counts compactly (e.g. 850, 265k, 265.4k, 1.05M). */
export function formatTokens(tokens: number): string {
  const safe = Number.isFinite(tokens) ? Math.max(0, tokens) : 0;
  if (safe < 1_000) {
    return String(Math.round(safe));
  }
  if (safe < 1_000_000) {
    return `${(safe / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return `${(safe / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
}

/** Format exact integer token counts with locale grouping. */
export function formatExactTokens(tokens: number): string {
  const safe = Number.isFinite(tokens) ? Math.max(0, Math.round(tokens)) : 0;
  return safe.toLocaleString("en-US");
}

/** Clamp percentage values safely into [0, 100] for visual progress bars. */
export function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/** Map GuardStatus to user-facing uppercase status pill text. */
export function statusDisplayLabel(status: GuardStatus): string {
  switch (status) {
    case "ok":
      return "OK";
    case "warn":
      return "WARN";
    case "limit":
      return "LIMIT";
    case "disabled":
      return "DISABLED";
    case "no_data":
      return "NO DATA";
  }
}

function statusPillClass(status: GuardStatus): string {
  switch (status) {
    case "ok":
      return "cg-pill cg-pill-ok";
    case "warn":
      return "cg-pill cg-pill-warn";
    case "limit":
      return "cg-pill cg-pill-limit";
    case "disabled":
    case "no_data":
      return "cg-pill cg-pill-muted";
  }
}

function statusFillClass(status: GuardStatus): string {
  switch (status) {
    case "ok":
      return "cg-fill cg-fill-ok";
    case "warn":
      return "cg-fill cg-fill-warn";
    case "limit":
      return "cg-fill cg-fill-limit";
    case "disabled":
    case "no_data":
      return "cg-fill cg-fill-muted";
  }
}

function statusStrokeColor(status: GuardStatus): string {
  switch (status) {
    case "ok":
      return "var(--color-text-success, var(--cg-text-success))";
    case "warn":
      return "var(--color-text-warning, var(--cg-text-warning))";
    case "limit":
      return "var(--color-text-danger, var(--cg-text-danger))";
    case "disabled":
    case "no_data":
      return "var(--color-text-tertiary, var(--cg-text-tertiary))";
  }
}

/**
 * Serialize async server tool calls with a strict FIFO mutex (max 1 in-flight)
 * so the host's 4-concurrent-call ceiling is never approached.
 */
export function createToolMutex<A extends unknown[], R>(
  executor: (...args: A) => Promise<R>,
): {
  run: (getArgs: () => A) => Promise<R>;
  isBusy: () => boolean;
  activeCount: () => number;
} {
  let tail: Promise<void> = Promise.resolve();
  let pending = 0;

  return {
    run(getArgs: () => A): Promise<R> {
      pending += 1;
      const task = tail.then(async () => executor(...getArgs()));
      const settled = task.finally(() => {
        pending -= 1;
      });
      tail = settled.then(
        () => undefined,
        () => undefined,
      );
      return settled;
    },
    isBusy: () => pending > 0,
    activeCount: () => pending,
  };
}

/** Compute SVG coordinate geometry for turn-by-turn token growth sparklines. */
export function buildSparklineModel(
  history: HistoryPoint[],
  thresholdTokens: number,
  width = 320,
  height = 96,
  warnThresholdTokens?: number,
): SparklineModel {
  const padX = 6;
  const padTop = 8;
  const padBottom = 8;
  const plotW = Math.max(1, width - padX * 2);
  const plotH = Math.max(1, height - padTop - padBottom);

  const safeThreshold = Math.max(1_000, Number.isFinite(thresholdTokens) ? thresholdTokens : 300_000);
  const peakHistory = history.reduce(
    (maxVal, item) => Math.max(maxVal, Number.isFinite(item.totalTokens) ? item.totalTokens : 0),
    0,
  );
  const maxYValue = Math.max(safeThreshold * 1.12, peakHistory * 1.06, 1_000);

  const toY = (val: number): number => {
    const ratio = Math.max(0, Math.min(1, val / maxYValue));
    return Number((padTop + plotH * (1 - ratio)).toFixed(2));
  };

  const thresholdY = toY(safeThreshold);
  const warnY =
    warnThresholdTokens && warnThresholdTokens > 0 && warnThresholdTokens < maxYValue
      ? toY(warnThresholdTokens)
      : null;

  if (history.length === 0) {
    return {
      width,
      height,
      points: [],
      linePath: "",
      areaPath: "",
      thresholdY,
      warnY,
      maxYValue,
      peakTokens: 0,
    };
  }

  const points: SparklinePoint[] = history.map((item, idx) => {
    const x =
      history.length === 1
        ? padX + plotW / 2
        : padX + (idx / (history.length - 1)) * plotW;
    const total = Number.isFinite(item.totalTokens) ? Math.max(0, item.totalTokens) : 0;
    return {
      x: Number(x.toFixed(2)),
      y: toY(total),
      stepIndex: item.stepIndex,
      totalTokens: total,
      deltaTokens: Number.isFinite(item.deltaTokens) ? item.deltaTokens : 0,
    };
  });

  const lineCmds = points.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`);
  const linePath = lineCmds.join(" ");
  const baselineY = Number((padTop + plotH).toFixed(2));
  const areaPath =
    points.length > 1
      ? `${linePath} L ${points[points.length - 1].x} ${baselineY} L ${points[0].x} ${baselineY} Z`
      : "";

  return {
    width,
    height,
    points,
    linePath,
    areaPath,
    thresholdY,
    warnY,
    maxYValue,
    peakTokens: peakHistory,
  };
}

/** Build a compact markdown summary for App.updateModelContext. */
export function buildAttachSummaryText(status: StatusPayload): string {
  const lines = [
    `Context Guard Summary (${status.conversationId || "active"})`,
    `- Status: ${statusDisplayLabel(status.status)} | Mode: ${status.mode}`,
    `- Usage: ${formatExactTokens(status.effectiveTokens)} / ${formatExactTokens(status.effectiveThresholdTokens)} tokens (${status.thresholdPercent.toFixed(1)}%)`,
    `- Model Window: ${status.windowPercent.toFixed(1)}% of ${formatTokens(status.modelWindowTokens)} (${status.model || "unknown"})`,
    `- Breakdown: input=${formatTokens(status.inputTokens)}, cacheRead=${formatTokens(status.cacheReadTokens)} (${status.cacheHitPercent.toFixed(1)}% hit), output=${formatTokens(status.outputTokens)}`,
  ];
  if (status.topBloatSteps.length > 0) {
    const top = status.topBloatSteps[0];
    const tools = top.tools.length > 0 ? top.tools.join(", ") : top.type || top.source;
    lines.push(
      `- Top Bloat Step #${top.stepIndex} (${tools}): ~${formatTokens(top.estimatedTokens)} tokens`,
    );
  }
  return lines.join("\n");
}

function el<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(
  doc: Document,
  tag: string,
  attrs: Record<string, string | number>,
): SVGElement {
  const node = doc.createElementNS(SVG_NS, tag);
  for (const [key, val] of Object.entries(attrs)) {
    node.setAttribute(key, String(val));
  }
  return node;
}

function createBrandIcon(doc: Document): SVGElement {
  const svg = svgEl(doc, "svg", {
    viewBox: "0 0 24 24",
    class: "cg-logo",
    "aria-hidden": "true",
    fill: "none",
  });
  svg.appendChild(
    svgEl(doc, "path", {
      d: "M12 2L20 5V11.5C20 16.6 16.6 20.6 12 22C7.4 20.6 4 16.6 4 11.5V5L12 2Z",
      stroke: "var(--cg-accent)",
      "stroke-width": "2",
      "stroke-linejoin": "round",
    }),
  );
  svg.appendChild(
    svgEl(doc, "path", {
      d: "M8 13.5A4.2 4.2 0 1 1 16 13.5",
      stroke: "var(--color-text-primary, var(--cg-text-primary))",
      "stroke-width": "1.8",
      "stroke-linecap": "round",
    }),
  );
  svg.appendChild(
    svgEl(doc, "path", {
      d: "M12 13.5L14.8 10.7",
      stroke: "var(--cg-accent)",
      "stroke-width": "1.8",
      "stroke-linecap": "round",
    }),
  );
  return svg;
}

function createButton(
  doc: Document,
  label: string,
  options: {
    className?: string;
    disabled?: boolean;
    title?: string;
    onClick: () => void;
  },
): HTMLButtonElement {
  const btn = el(doc, "button", options.className || "cg-btn", label);
  btn.type = "button";
  btn.disabled = Boolean(options.disabled);
  if (options.title) btn.title = options.title;
  btn.addEventListener("click", options.onClick);
  return btn;
}

function createProgressRow(
  doc: Document,
  options: {
    label: string;
    valueText: string;
    percent: number;
    fillClass: string;
    warnMarkPercent?: number;
    footnote?: string;
  },
): HTMLElement {
  const block = el(doc, "div", "cg-bar-block");
  const labels = el(doc, "div", "cg-bar-labels");
  labels.appendChild(el(doc, "span", "cg-label", options.label));
  labels.appendChild(el(doc, "span", "cg-nums cg-muted", options.valueText));
  block.appendChild(labels);

  const track = el(doc, "div", "cg-track");
  const fill = el(doc, "div", options.fillClass);
  fill.style.width = `${clampPercent(options.percent).toFixed(1)}%`;
  track.appendChild(fill);

  if (
    options.warnMarkPercent !== undefined &&
    options.warnMarkPercent > 0 &&
    options.warnMarkPercent < 100
  ) {
    const tick = el(doc, "div", "cg-warn-tick");
    tick.style.left = `${options.warnMarkPercent}%`;
    track.appendChild(tick);
  }
  block.appendChild(track);

  if (options.footnote) {
    block.appendChild(el(doc, "div", "cg-muted", options.footnote));
  }
  return block;
}

function renderHeader(
  doc: Document,
  state: DashboardState,
  controller: DashboardController,
  onToggleAuto: () => void,
  onToggleSettings: () => void,
): HTMLElement {
  const wrapper = el(doc, "div");
  const header = el(doc, "header", "cg-header");

  const brand = el(doc, "div", "cg-brand");
  brand.appendChild(createBrandIcon(doc));
  brand.appendChild(el(doc, "span", "cg-title", "Agy Lens"));

  if (state.currentView === "status" && state.statusData) {
    brand.appendChild(
      el(
        doc,
        "span",
        statusPillClass(state.statusData.status),
        statusDisplayLabel(state.statusData.status),
      ),
    );
    brand.appendChild(
      el(doc, "span", "cg-pill cg-pill-muted", state.statusData.mode),
    );
  } else if (state.currentView === "overview" && state.overviewData) {
    brand.appendChild(
      el(
        doc,
        "span",
        state.overviewData.enabled ? "cg-pill cg-pill-ok" : "cg-pill cg-pill-muted",
        state.overviewData.enabled ? "ACTIVE" : "DISABLED",
      ),
    );
    brand.appendChild(
      el(doc, "span", "cg-pill cg-pill-muted", state.overviewData.mode),
    );
  }

  const controls = el(doc, "div", "cg-header-controls");
  const canServerTools = Boolean(state.hostCapabilities?.serverTools);

  controls.appendChild(
    createButton(doc, state.loading ? "Syncing..." : "Refresh", {
      className: "cg-btn cg-btn-sm",
      disabled: !canServerTools || state.loading,
      title: canServerTools
        ? "Refresh active view now"
        : "Host does not support server tool calls",
      onClick: () => {
        void controller.refreshCurrentView(false);
      },
    }),
  );

  const autoLabel = `Auto ${state.autoRefreshSeconds}s`;
  const autoBtn = createButton(doc, state.autoRefresh ? "" : autoLabel, {
    className: state.autoRefresh
      ? "cg-btn cg-btn-sm cg-btn-active"
      : "cg-btn cg-btn-sm",
    disabled: !canServerTools,
    title: state.autoRefresh
      ? `Auto-refreshing every ${state.autoRefreshSeconds}s (click to pause; interval in Settings)`
      : `${state.autoRefreshSeconds}-second auto-refresh paused (click to enable; interval in Settings)`,
    onClick: onToggleAuto,
  });
  if (state.autoRefresh) {
    autoBtn.append(el(doc, "span", "cg-dot"), autoLabel);
  }
  controls.appendChild(autoBtn);

  controls.appendChild(
    createButton(doc, state.settingsOpen ? "Hide Settings" : "Settings", {
      className: state.settingsOpen ? "cg-btn cg-btn-sm cg-btn-active" : "cg-btn cg-btn-sm",
      onClick: onToggleSettings,
    }),
  );

  header.append(brand, controls);
  wrapper.appendChild(header);

  const tabs = el(doc, "nav", "cg-tabs");
  const tabDefs: Array<{ id: ViewMode; label: string; disabled?: boolean; onClick: () => void }> = [
    {
      id: "status",
      label: "Monitor",
      onClick: () => {
        const targetId = state.activeConversationId || state.overviewData?.active?.conversationId || "";
        if (!state.statusData && targetId && canServerTools) {
          void controller.selectConversation(targetId);
        } else {
          controller.showStatusView();
        }
      },
    },
    {
      id: "search",
      label: "Search",
      disabled: !canServerTools,
      onClick: () => {
        void controller.switchToSearch();
      },
    },
    {
      id: "footprint",
      label: "Setup",
      disabled: !canServerTools,
      onClick: () => {
        void controller.switchToFootprint();
      },
    },
    {
      id: "overview",
      label: "Chats",
      disabled: !canServerTools,
      onClick: () => {
        void controller.switchToOverview();
      },
    },
  ];
  for (const tab of tabDefs) {
    tabs.appendChild(
      createButton(doc, tab.label, {
        className: state.currentView === tab.id ? "cg-tab cg-tab-active" : "cg-tab",
        disabled: Boolean(tab.disabled),
        onClick: tab.onClick,
      }),
    );
  }
  wrapper.appendChild(tabs);
  return wrapper;
}

function renderLoopBanner(
  doc: Document,
  activeLoop: ActiveLoopState | null | undefined,
  loading: boolean,
  onDismiss: () => void,
): HTMLElement | null {
  if (!activeLoop) return null;
  const card = el(doc, "div", "cg-loop-banner");
  const reasonLabel =
    activeLoop.reason === "identical_repeats" ? "identical repeats" : "consecutive failures";
  const detail = activeLoop.lastErrorSnippet ? ` — ${activeLoop.lastErrorSnippet}` : "";
  const msg = `Loop Guard: ${activeLoop.toolName} (${activeLoop.count}x ${reasonLabel})${detail}`;
  card.appendChild(el(doc, "span", undefined, msg));
  card.appendChild(
    createButton(doc, "Dismiss Loop", {
      className: "cg-btn cg-btn-sm",
      disabled: loading,
      onClick: onDismiss,
    }),
  );
  return card;
}

function renderBanner(
  doc: Document,
  state: DashboardState,
  onDismiss: () => void,
): HTMLElement | null {
  if (!state.banner) return null;
  const cls =
    state.banner.kind === "error"
      ? "cg-banner cg-banner-error"
      : state.banner.kind === "success"
        ? "cg-banner cg-banner-success"
        : "cg-banner cg-banner-info";
  const banner = el(doc, "div", cls);
  banner.appendChild(el(doc, "span", undefined, state.banner.text));
  banner.appendChild(
    createButton(doc, "Dismiss", {
      className: "cg-btn cg-btn-sm",
      onClick: onDismiss,
    }),
  );
  return banner;
}

function shortConvId(cid: string | null | undefined): string {
  if (!cid) return "";
  return cid.length > 8 ? cid.slice(0, 8) : cid;
}

function formatConversationOptionLabel(
  title: string | undefined,
  cid: string,
  extra?: string,
): string {
  const cleanTitle = (title || "").trim();
  const shortId = shortConvId(cid);
  if (cleanTitle && cleanTitle !== cid) {
    const compact =
      cleanTitle.length > 46 ? `${cleanTitle.slice(0, 43).trimEnd()}...` : cleanTitle;
    return extra ? `${compact} · ${shortId} (${extra})` : `${compact} · ${shortId}`;
  }
  return extra ? `${cid} (${extra})` : cid;
}

function renderDetailSubheader(
  doc: Document,
  status: StatusPayload,
  state: DashboardState,
  controller: DashboardController,
): HTMLElement {
  const row = el(doc, "div", "cg-subheader");
  const selectWrap = el(doc, "div", "cg-conv-select-wrap");

  const select = el(doc, "select", "cg-select");
  select.setAttribute("aria-label", "Conversation selector");

  const knownIds = new Set<string>();
  const currentId = status.conversationId || state.activeConversationId || "";
  if (currentId) {
    knownIds.add(currentId);
  }
  const overviewList = state.overviewData?.conversations || [];
  for (const item of overviewList) {
    if (item.conversationId) knownIds.add(item.conversationId);
  }

  if (knownIds.size === 0) {
    const opt = el(doc, "option", undefined, "Active session");
    opt.value = "";
    select.appendChild(opt);
    select.disabled = true;
  } else {
    for (const cid of knownIds) {
      const overviewMatch = overviewList.find((c) => c.conversationId === cid);
      const title =
        cid === currentId
          ? status.title || overviewMatch?.title
          : overviewMatch?.title;
      const label = formatConversationOptionLabel(
        title,
        cid,
        overviewMatch?.sourceApp,
      );
      const opt = el(doc, "option", undefined, label);
      opt.value = cid;
      opt.selected = cid === currentId;
      select.appendChild(opt);
    }
    select.disabled = !state.hostCapabilities?.serverTools;
  }

  select.addEventListener("change", () => {
    if (select.value && select.value !== currentId) {
      void controller.selectConversation(select.value);
    }
  });
  selectWrap.appendChild(select);
  row.appendChild(selectWrap);

  const badges = el(doc, "div", "cg-badge-row");
  if (status.isSubagent) {
    badges.appendChild(
      el(
        doc,
        "span",
        "cg-pill cg-pill-muted",
        status.agentName || "Subagent",
      ),
    );
  }
  const modelLabel = status.model || "No model telemetry";
  const modelBadge = el(doc, "span", "cg-model-badge", modelLabel);
  modelBadge.title = modelLabel;
  badges.appendChild(modelBadge);

  if (status.snoozedUntilTokens > status.baseThresholdTokens) {
    badges.appendChild(
      el(
        doc,
        "span",
        "cg-pill cg-pill-info",
        `Snoozed ${formatTokens(status.snoozedUntilTokens)}`,
      ),
    );
  }
  row.appendChild(badges);
  return row;
}

function renderGaugeCard(doc: Document, status: StatusPayload): HTMLElement {
  const card = el(doc, "section", "cg-card");

  const heroRow = el(doc, "div", "cg-hero-row");
  const heroVal = el(
    doc,
    "div",
    "cg-hero-value cg-nums",
    `${formatTokens(status.effectiveTokens)} tokens`,
  );
  heroVal.title = `${formatExactTokens(status.effectiveTokens)} effective tokens`;

  const heroMeta = el(doc, "div", "cg-hero-meta cg-nums");
  heroMeta.appendChild(
    el(
      doc,
      "span",
      undefined,
      `${status.thresholdPercent.toFixed(1)}% of ${formatTokens(status.effectiveThresholdTokens)}`,
    ),
  );
  if (status.pendingDeltaTokens > 0) {
    heroMeta.appendChild(
      el(
        doc,
        "span",
        "cg-pill cg-pill-info",
        `+${formatTokens(status.pendingDeltaTokens)} pending`,
      ),
    );
  }
  if (status.compactionCount > 0) {
    heroMeta.appendChild(
      el(
        doc,
        "span",
        "cg-pill cg-pill-muted",
        `Compactions: ${status.compactionCount}`,
      ),
    );
  }
  heroRow.append(heroVal, heroMeta);
  card.appendChild(heroRow);

  const remainingText =
    status.remainingToThreshold > 0
      ? `${formatTokens(status.remainingToThreshold)} remaining`
      : "Threshold reached";
  const turnsText =
    status.estimatedTurnsRemaining === null
      ? "~— turns left"
      : `~${status.estimatedTurnsRemaining} turn${status.estimatedTurnsRemaining === 1 ? "" : "s"} left`;

  const snoozeNote =
    status.snoozedUntilTokens > status.baseThresholdTokens
      ? ` · Snoozed from ${formatTokens(status.baseThresholdTokens)} to ${formatTokens(status.snoozedUntilTokens)}`
      : "";

  card.appendChild(
    createProgressRow(doc, {
      label: "Guard Threshold",
      valueText: `${formatTokens(status.effectiveTokens)} / ${formatTokens(status.effectiveThresholdTokens)} (${status.thresholdPercent.toFixed(1)}%)`,
      percent: status.thresholdPercent,
      fillClass: statusFillClass(status.status),
      warnMarkPercent: status.warnPercent,
      footnote: `${remainingText} · ${turnsText}${snoozeNote}`,
    }),
  );

  card.appendChild(
    createProgressRow(doc, {
      label: "Model Window",
      valueText: `${formatTokens(status.effectiveTokens)} / ${formatTokens(status.modelWindowTokens)} (${status.windowPercent.toFixed(1)}%)`,
      percent: status.windowPercent,
      fillClass: "cg-fill cg-fill-info",
    }),
  );

  return card;
}

function renderMetricGrid(doc: Document, status: StatusPayload): HTMLElement {
  const grid = el(doc, "section", "cg-metrics-grid");
  const promptTotal = status.inputTokens + status.cacheReadTokens;
  const items = [
    {
      label: "Input",
      value: formatTokens(status.inputTokens),
      sub: `${formatExactTokens(status.inputTokens)} uncached`,
    },
    {
      label: "Cache Read",
      value: formatTokens(status.cacheReadTokens),
      sub: `${formatExactTokens(status.cacheReadTokens)} cached`,
    },
    {
      label: "Output",
      value: formatTokens(status.outputTokens),
      sub: `${formatExactTokens(status.outputTokens)} generated`,
    },
    {
      label: "Cache Hit %",
      value: `${status.cacheHitPercent.toFixed(1)}%`,
      sub: `${formatTokens(promptTotal)} prompt base`,
    },
  ];

  for (const item of items) {
    const card = el(doc, "div", "cg-metric-card");
    card.appendChild(el(doc, "div", "cg-metric-label", item.label));
    card.appendChild(el(doc, "div", "cg-metric-val", item.value));
    card.appendChild(el(doc, "div", "cg-metric-sub", item.sub));
    grid.appendChild(card);
  }
  return grid;
}

function isQuotaPayload(value: unknown): value is ModelQuotaPayload {
  return isRecord(value) && Array.isArray(value.groups) && typeof value.status === "string";
}

function quotaRingStroke(remainingPercent: number): string {
  if (remainingPercent < 20) return "var(--color-text-danger, var(--cg-text-danger))";
  if (remainingPercent < 50) return "var(--color-text-warning, var(--cg-text-warning))";
  return "var(--color-text-success, var(--cg-text-success))";
}

function renderQuotaRing(doc: Document, remainingPercent: number): SVGElement {
  const clamped = Math.max(0, Math.min(100, remainingPercent));
  const r = 6.5;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - clamped / 100);
  const svg = svgEl(doc, "svg", {
    viewBox: "0 0 18 18",
    class: "cg-quota-ring",
    role: "img",
    "aria-label": `${Math.round(clamped)}% remaining`,
  });
  svg.appendChild(
    svgEl(doc, "circle", {
      cx: 9,
      cy: 9,
      r,
      fill: "none",
      stroke: "var(--cg-track)",
      "stroke-width": "2.2",
    }),
  );
  svg.appendChild(
    svgEl(doc, "circle", {
      cx: 9,
      cy: 9,
      r,
      fill: "none",
      stroke: quotaRingStroke(clamped),
      "stroke-width": "2.2",
      "stroke-linecap": "round",
      "stroke-dasharray": circ.toFixed(2),
      "stroke-dashoffset": offset.toFixed(2),
      transform: "rotate(-90 9 9)",
    }),
  );
  return svg;
}

function renderModelQuotaCard(
  doc: Document,
  quota: ModelQuotaPayload,
  state: DashboardState,
  controller: DashboardController,
): HTMLElement {
  const card = el(doc, "section", "cg-card");
  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Model Quotas"));

  const controls = el(doc, "div", "cg-header-controls");
  const ageLabel = quota.fetching
    ? "Syncing..."
    : quota.fetchedAt
      ? formatQuotaAge(quota.ageSeconds)
      : quota.status === "unavailable"
        ? "CLI missing"
        : "Not synced";
  if (ageLabel) {
    controls.appendChild(el(doc, "span", "cg-muted", ageLabel));
  }
  const canServerTools = Boolean(state.hostCapabilities?.serverTools);
  controls.appendChild(
    createButton(doc, quota.fetching ? "Checking..." : "Refresh Limits", {
      className: "cg-btn cg-btn-sm",
      disabled: !canServerTools || Boolean(quota.fetching),
      title: "Refresh Antigravity 5h and weekly model usage limits via agy CLI",
      onClick: () => {
        void controller.refreshQuota();
      },
    }),
  );
  head.appendChild(controls);
  card.appendChild(head);

  if (quota.groups.length === 0) {
    const msg = quota.fetching
      ? "Checking Antigravity 5h and weekly model usage limits..."
      : quota.error || "Click Refresh Limits to load Antigravity 5h and weekly model quotas.";
    card.appendChild(el(doc, "div", "cg-muted", msg));
    return card;
  }

  const grid = el(doc, "div", "cg-quota-grid");
  for (const group of quota.groups) {
    const box = el(doc, "div", "cg-quota-group");
    const gHead = el(doc, "div", "cg-quota-group-head");
    gHead.appendChild(el(doc, "div", "cg-quota-group-title", group.name));
    if (group.modelsSummary) {
      gHead.appendChild(el(doc, "div", "cg-quota-models", group.modelsSummary));
    }
    box.appendChild(gHead);

    for (const bucket of group.buckets) {
      const row = el(doc, "div", "cg-quota-row");
      const left = el(doc, "div", "cg-quota-left");
      left.appendChild(el(doc, "span", "cg-quota-label", bucket.label));
      const resetText = formatQuotaReset(bucket.resetTime, bucket.remainingPercent);
      if (resetText) {
        left.appendChild(el(doc, "span", "cg-quota-reset", resetText));
      }
      const pred = formatQuotaPrediction(bucket.prediction);
      if (pred) {
        left.appendChild(el(doc, "span", pred.toneClass, pred.text));
      }

      const right = el(doc, "div", "cg-quota-right cg-nums");
      right.appendChild(
        el(doc, "span", "cg-quota-pct", `${Math.round(bucket.remainingPercent)}% left`),
      );
      right.appendChild(renderQuotaRing(doc, bucket.remainingPercent));
      row.append(left, right);
      box.appendChild(row);
    }

    grid.appendChild(box);
  }
  card.appendChild(grid);

  if (quota.status === "error" && quota.error) {
    card.appendChild(el(doc, "div", "cg-muted", `Showing cached limits (${quota.error})`));
  }
  return card;
}

function renderSparklineCard(doc: Document, status: StatusPayload): HTMLElement {
  const card = el(doc, "section", "cg-card");
  const activeEpochStep =
    typeof status.activeEpochStep === "number" && Number.isFinite(status.activeEpochStep)
      ? status.activeEpochStep
      : 0;
  const epochHistory =
    activeEpochStep > 0
      ? status.history.filter((item) => item.stepIndex >= activeEpochStep)
      : status.history;

  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Turn-by-Turn Growth"));

  const epochPrefix =
    status.compactionCount > 0 && activeEpochStep > 0 ? `Since #${activeEpochStep} · ` : "";
  const meta = el(
    doc,
    "span",
    "cg-muted cg-nums",
    `${epochPrefix}${epochHistory.length} turns · avg +${formatTokens(status.avgDeltaPerTurn)}/turn`,
  );
  head.appendChild(meta);
  card.appendChild(head);

  const model = buildSparklineModel(
    epochHistory,
    status.effectiveThresholdTokens,
    320,
    96,
    status.warnThresholdTokens,
  );

  if (model.points.length === 0) {
    const emptyMsg =
      status.compactionCount > 0 && activeEpochStep > 0
        ? `Compacted at step #${activeEpochStep} · Waiting for next PLANNER_RESPONSE turn...`
        : "Waiting for completed PLANNER_RESPONSE turns...";
    card.appendChild(el(doc, "div", "cg-muted", emptyMsg));
    return card;
  }

  const wrap = el(doc, "div", "cg-spark-wrap");
  const svg = svgEl(doc, "svg", {
    viewBox: `0 0 ${model.width} ${model.height}`,
    preserveAspectRatio: "none",
    class: "cg-spark-svg",
    role: "img",
    "aria-label": "Turn-by-turn token growth sparkline",
  });

  if (model.warnY !== null) {
    svg.appendChild(
      svgEl(doc, "line", {
        x1: 0,
        y1: model.warnY,
        x2: model.width,
        y2: model.warnY,
        stroke: "var(--color-text-warning, var(--cg-text-warning))",
        "stroke-opacity": "0.35",
        "stroke-width": "1",
        "stroke-dasharray": "2 4",
      }),
    );
  }

  svg.appendChild(
    svgEl(doc, "line", {
      x1: 0,
      y1: model.thresholdY,
      x2: model.width,
      y2: model.thresholdY,
      stroke: "var(--color-text-danger, var(--cg-text-danger))",
      "stroke-opacity": "0.75",
      "stroke-width": "1.25",
      "stroke-dasharray": "4 3",
    }),
  );

  const strokeColor = statusStrokeColor(status.status);
  if (model.areaPath) {
    svg.appendChild(
      svgEl(doc, "path", {
        d: model.areaPath,
        fill: strokeColor,
        "fill-opacity": "0.14",
      }),
    );
  }
  if (model.points.length > 1) {
    svg.appendChild(
      svgEl(doc, "path", {
        d: model.linePath,
        fill: "none",
        stroke: strokeColor,
        "stroke-width": "2",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }),
    );
  }

  const last = model.points[model.points.length - 1];
  svg.appendChild(
    svgEl(doc, "circle", {
      cx: last.x,
      cy: last.y,
      r: 3.5,
      fill: strokeColor,
    }),
  );

  wrap.appendChild(svg);
  card.appendChild(wrap);

  const foot = el(doc, "div", "cg-bar-labels cg-muted cg-nums");
  foot.appendChild(
    el(doc, "span", undefined, `Peak: ${formatTokens(model.peakTokens)}`),
  );
  foot.appendChild(
    el(
      doc,
      "span",
      undefined,
      `Threshold line: ${formatTokens(status.effectiveThresholdTokens)}`,
    ),
  );
  card.appendChild(foot);
  return card;
}

function renderBloatCard(doc: Document, status: StatusPayload): HTMLElement {
  const card = el(doc, "section", "cg-card");
  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Bloat Inspector"));
  const epochLabel =
    status.activeEpochStep && status.activeEpochStep > 0
      ? `Since step #${status.activeEpochStep}`
      : "Current epoch";
  head.appendChild(el(doc, "span", "cg-muted", epochLabel));
  card.appendChild(head);

  const cats = status.bloatByCategory;
  const totalCat =
    cats.user + cats.model + cats.toolOutputs + cats.system;

  if (totalCat > 0) {
    const stacked = el(doc, "div", "cg-stacked-bar");
    for (const meta of CATEGORY_META) {
      const val = Math.max(0, cats[meta.key]);
      if (val <= 0) continue;
      const seg = el(doc, "div");
      seg.style.width = `${((val / totalCat) * 100).toFixed(1)}%`;
      seg.style.background = meta.colorVar;
      stacked.appendChild(seg);
    }
    card.appendChild(stacked);
  }

  const chipGrid = el(doc, "div", "cg-cat-grid");
  for (const meta of CATEGORY_META) {
    const val = Math.max(0, cats[meta.key]);
    const pct = totalCat > 0 ? Math.round((val / totalCat) * 100) : 0;
    const chip = el(doc, "div", "cg-cat-chip");
    const nameWrap = el(doc, "span", "cg-cat-name");
    const dot = el(doc, "span", "cg-cat-dot");
    dot.style.background = meta.colorVar;
    nameWrap.appendChild(dot);
    nameWrap.appendChild(el(doc, "span", undefined, meta.label));
    chip.appendChild(nameWrap);
    chip.appendChild(
      el(doc, "span", "cg-nums", `${formatTokens(val)} (${pct}%)`),
    );
    chipGrid.appendChild(chip);
  }
  card.appendChild(chipGrid);

  if (status.topBloatSteps.length === 0) {
    card.appendChild(
      el(doc, "div", "cg-muted", "No heavy steps recorded in the active epoch."),
    );
    return card;
  }

  const table = el(doc, "table", "cg-table");
  const thead = el(doc, "thead");
  const headRow = el(doc, "tr");
  const hStep = el(doc, "th", "cg-col-step", "Step");
  const hTool = el(doc, "th", "cg-col-tool", "Tool / Src");
  const hTok = el(doc, "th", "cg-col-tok", "Est.");
  const hSnip = el(doc, "th", undefined, "Snippet");
  headRow.append(hStep, hTool, hTok, hSnip);
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el(doc, "tbody");
  for (const step of status.topBloatSteps.slice(0, 8)) {
    const tr = el(doc, "tr");

    const tdStep = el(doc, "td", "cg-col-step cg-mono cg-nums", `#${step.stepIndex}`);
    const toolLabel =
      step.tools.length > 0 ? step.tools.join(", ") : step.type || step.source || "-";
    const tdTool = el(doc, "td", "cg-col-tool cg-mono", toolLabel);
    tdTool.title = toolLabel;

    const tdTok = el(
      doc,
      "td",
      "cg-col-tok cg-nums",
      formatTokens(step.estimatedTokens),
    );
    tdTok.title = `${formatExactTokens(step.estimatedTokens)} estimated tokens`;

    const snippetText = step.snippet || "(empty)";
    const tdSnip = el(doc, "td", undefined, snippetText);
    tdSnip.title = snippetText;

    tr.append(tdStep, tdTool, tdTok, tdSnip);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  card.appendChild(table);
  return card;
}

function renderActionBar(
  doc: Document,
  status: StatusPayload,
  state: DashboardState,
  controller: DashboardController,
): HTMLElement {
  const card = el(doc, "section", "cg-card");
  const bar = el(doc, "div", "cg-actions");
  const caps = state.hostCapabilities;
  const canMessage = Boolean(caps?.message);
  const canServerTools = Boolean(caps?.serverTools);
  const canUpdateContext = Boolean(caps?.updateModelContext);

  bar.appendChild(
    createButton(doc, "Copy /compact", {
      title: "Copy /compact command to paste into the chat input box",
      onClick: () => {
        void controller.triggerCompact();
      },
    }),
  );

  bar.appendChild(
    createButton(doc, "Snooze +50k", {
      disabled: !canServerTools || state.loading,
      title: canServerTools
        ? "Snooze threshold alerts by +50k tokens"
        : "Host does not support server tool calls",
      onClick: () => {
        void controller.triggerSnooze(SNOOZE_STEP_TOKENS);
      },
    }),
  );

  if (status.snoozedUntilTokens > 0) {
    bar.appendChild(
      createButton(doc, "Clear Snooze", {
        disabled: !canServerTools || state.loading,
        onClick: () => {
          void controller.triggerResetSnooze();
        },
      }),
    );
  }

  bar.appendChild(
    createButton(doc, "Smart Handoff", {
      className: "cg-btn cg-btn-primary",
      disabled: !canServerTools || state.loading,
      title: canMessage
        ? "Generate brief and start a fresh conversation"
        : "Generate copyable continuation brief",
      onClick: () => {
        void controller.triggerHandoff(state.handoffNote);
      },
    }),
  );

  if (canUpdateContext) {
    bar.appendChild(
      createButton(doc, "Attach Summary", {
        disabled: state.loading,
        title: "Attach current context stats to next model turn",
        onClick: () => {
          void controller.triggerAttachSummary();
        },
      }),
    );
  }

  card.appendChild(bar);
  return card;
}

function renderHandoffPanel(
  doc: Document,
  state: DashboardState,
  controller: DashboardController,
  onCopy: (textarea: HTMLTextAreaElement) => void,
  onClose: () => void,
): HTMLElement | null {
  if (!state.handoffOpen || !state.handoffData) return null;
  const handoff = state.handoffData;
  const card = el(doc, "section", "cg-card");

  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Smart Handoff Brief"));
  head.appendChild(
    createButton(doc, "Close", {
      className: "cg-btn cg-btn-sm",
      onClick: onClose,
    }),
  );
  card.appendChild(head);

  const statusNote = state.handoffSentToNewChat
    ? "Sent to a new conversation. You can also copy or tweak the brief below."
    : state.hostCapabilities?.message
      ? "Brief generated. Copy below or send to a new chat."
      : "Copy the continuation brief below and paste it into a fresh chat.";
  card.appendChild(el(doc, "div", "cg-muted", statusNote));

  const noteRow = el(doc, "div", "cg-field-row");
  const noteInput = el(doc, "input", "cg-input");
  noteInput.type = "text";
  noteInput.placeholder = "Optional handoff note...";
  noteInput.value = state.handoffNote;
  noteInput.style.flex = "1";
  noteInput.addEventListener("input", () => {
    state.handoffNote = noteInput.value;
  });

  const rebuildBtn = createButton(doc, "Rebuild", {
    className: "cg-btn cg-btn-sm",
    disabled: !state.hostCapabilities?.serverTools || state.loading,
    onClick: () => {
      void controller.triggerHandoff(state.handoffNote, false);
    },
  });
  noteRow.append(noteInput, rebuildBtn);
  card.appendChild(noteRow);

  const textarea = el(doc, "textarea", "cg-textarea");
  textarea.readOnly = true;
  textarea.value = handoff.brief;
  textarea.setAttribute("aria-label", "Smart handoff brief");
  card.appendChild(textarea);

  const actions = el(doc, "div", "cg-actions");
  actions.appendChild(
    createButton(doc, "Copy Brief", {
      className: "cg-btn cg-btn-primary cg-btn-sm",
      onClick: () => onCopy(textarea),
    }),
  );
  if (state.hostCapabilities?.message) {
    actions.appendChild(
      createButton(doc, "Send to New Chat", {
        className: "cg-btn cg-btn-sm",
        onClick: () => {
          void controller.triggerHandoff(state.handoffNote);
        },
      }),
    );
  }
  card.appendChild(actions);
  return card;
}

function renderSettingsDrawer(
  doc: Document,
  state: DashboardState,
  controller: DashboardController,
  onClose: () => void,
  onDraftChanged: () => void,
): HTMLElement | null {
  if (!state.settingsOpen) return null;
  const draft = state.settingsDraft;
  const card = el(doc, "section", "cg-card");

  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Agy Lens Settings"));
  head.appendChild(
    createButton(doc, "Close", {
      className: "cg-btn cg-btn-sm",
      onClick: onClose,
    }),
  );
  card.appendChild(head);

  const enabledRow = el(doc, "label", "cg-field-row");
  enabledRow.appendChild(el(doc, "span", "cg-label", "Enable Guard Hooks"));
  const enabledCheckbox = el(doc, "input");
  enabledCheckbox.type = "checkbox";
  enabledCheckbox.checked = draft.enabled;
  enabledCheckbox.addEventListener("change", () => {
    draft.enabled = enabledCheckbox.checked;
  });
  enabledRow.appendChild(enabledCheckbox);
  card.appendChild(enabledRow);

  const presetWrap = el(doc, "div", "cg-field");
  presetWrap.appendChild(el(doc, "span", "cg-label", "Threshold Presets"));
  const presetRow = el(doc, "div", "cg-preset-row");
  for (const preset of THRESHOLD_PRESETS) {
    const active = draft.thresholdTokens === preset;
    presetRow.appendChild(
      createButton(doc, formatTokens(preset), {
        className: active ? "cg-btn cg-btn-sm cg-btn-active" : "cg-btn cg-btn-sm",
        onClick: () => {
          draft.thresholdTokens = preset;
          onDraftChanged();
        },
      }),
    );
  }
  presetWrap.appendChild(presetRow);
  card.appendChild(presetWrap);

  const grid = el(doc, "div", "cg-form-grid");

  const threshField = el(doc, "div", "cg-field");
  threshField.appendChild(
    el(doc, "label", "cg-label", "Custom Threshold (tokens)"),
  );
  const threshInput = el(doc, "input", "cg-input cg-nums");
  threshInput.type = "number";
  threshInput.min = String(MIN_THRESHOLD_TOKENS);
  threshInput.step = "10000";
  threshInput.value = String(draft.thresholdTokens);
  threshInput.addEventListener("input", () => {
    const parsed = Number.parseInt(threshInput.value, 10);
    if (Number.isFinite(parsed)) {
      draft.thresholdTokens = parsed;
    }
  });
  threshField.appendChild(threshInput);

  const warnField = el(doc, "div", "cg-field");
  warnField.appendChild(el(doc, "label", "cg-label", "Warn Percent (1-99%)"));
  const warnInput = el(doc, "input", "cg-input cg-nums");
  warnInput.type = "number";
  warnInput.min = "1";
  warnInput.max = "99";
  warnInput.value = String(draft.warnPercent);
  warnInput.addEventListener("input", () => {
    const parsed = Number.parseInt(warnInput.value, 10);
    if (Number.isFinite(parsed)) {
      draft.warnPercent = parsed;
    }
  });
  warnField.appendChild(warnInput);

  grid.append(threshField, warnField);
  card.appendChild(grid);

  const modeField = el(doc, "div", "cg-field");
  modeField.appendChild(el(doc, "span", "cg-label", "Enforcement Mode"));
  const segmented = el(doc, "div", "cg-segmented");
  for (const mode of ["warn", "stop", "gate"] as const) {
    segmented.appendChild(
      createButton(doc, mode.toUpperCase(), {
        className:
          draft.mode === mode
            ? "cg-btn cg-btn-sm cg-btn-active"
            : "cg-btn cg-btn-sm",
        onClick: () => {
          draft.mode = mode;
          onDraftChanged();
        },
      }),
    );
  }
  modeField.appendChild(segmented);
  modeField.appendChild(el(doc, "div", "cg-muted", MODE_DESCRIPTIONS[draft.mode]));
  card.appendChild(modeField);

  const refreshSeconds = draft.autoRefreshSeconds ?? state.autoRefreshSeconds;
  const refreshField = el(doc, "div", "cg-field");
  refreshField.appendChild(el(doc, "span", "cg-label", "Auto-Refresh Interval (seconds)"));
  const refreshPresetRow = el(doc, "div", "cg-preset-row");
  for (const secs of AUTO_REFRESH_PRESETS) {
    const active = refreshSeconds === secs;
    refreshPresetRow.appendChild(
      createButton(doc, `${secs}s`, {
        className: active ? "cg-btn cg-btn-sm cg-btn-active" : "cg-btn cg-btn-sm",
        onClick: () => {
          draft.autoRefreshSeconds = secs;
          onDraftChanged();
        },
      }),
    );
  }
  refreshField.appendChild(refreshPresetRow);

  const refreshInputRow = el(doc, "div", "cg-field-row");
  refreshInputRow.appendChild(el(doc, "label", "cg-muted", "Custom (1-300s)"));
  const refreshInput = el(doc, "input", "cg-input cg-nums");
  refreshInput.type = "number";
  refreshInput.min = String(MIN_AUTO_REFRESH_SECONDS);
  refreshInput.max = String(MAX_AUTO_REFRESH_SECONDS);
  refreshInput.step = "1";
  refreshInput.value = String(refreshSeconds);
  refreshInput.setAttribute("aria-label", "Auto-Refresh Interval (seconds)");
  refreshInput.addEventListener("input", () => {
    const parsed = Number.parseInt(refreshInput.value, 10);
    if (Number.isFinite(parsed)) {
      draft.autoRefreshSeconds = parsed;
    }
  });
  refreshInputRow.appendChild(refreshInput);
  refreshField.appendChild(refreshInputRow);
  card.appendChild(refreshField);

  const lgDraft = draft.loopGuard ?? { ...DEFAULT_LOOP_GUARD_DRAFT };
  draft.loopGuard = lgDraft;
  const lgSection = el(doc, "div", "cg-field");
  lgSection.appendChild(el(doc, "span", "cg-label", "Loop Guard (Stuck-Loop Detector)"));
  const lgEnabledRow = el(doc, "label", "cg-field-row");
  lgEnabledRow.appendChild(el(doc, "span", "cg-muted", "Enable Loop Guard"));
  const lgCb = el(doc, "input");
  lgCb.type = "checkbox";
  lgCb.checked = lgDraft.enabled;
  lgCb.addEventListener("change", () => {
    lgDraft.enabled = lgCb.checked;
  });
  lgEnabledRow.appendChild(lgCb);
  lgSection.appendChild(lgEnabledRow);

  const lgGrid = el(doc, "div", "cg-form-grid");
  const lgThreshWrap = el(doc, "div", "cg-field");
  lgThreshWrap.appendChild(el(doc, "label", "cg-muted", "Failure/Repeat Nudge (2-6)"));
  const lgThreshIn = el(doc, "input", "cg-input cg-nums");
  lgThreshIn.type = "number";
  lgThreshIn.min = "2";
  lgThreshIn.max = "6";
  lgThreshIn.value = String(lgDraft.consecutiveThreshold);
  lgThreshIn.addEventListener("input", () => {
    const p = Number.parseInt(lgThreshIn.value, 10);
    if (Number.isFinite(p)) lgDraft.consecutiveThreshold = p;
  });
  lgThreshWrap.appendChild(lgThreshIn);

  const lgPauseWrap = el(doc, "div", "cg-field");
  lgPauseWrap.appendChild(el(doc, "label", "cg-muted", "Auto-Pause Threshold (0=off, 4-10)"));
  const lgPauseIn = el(doc, "input", "cg-input cg-nums");
  lgPauseIn.type = "number";
  lgPauseIn.min = "0";
  lgPauseIn.max = "10";
  lgPauseIn.value = String(lgDraft.autoPauseThreshold);
  lgPauseIn.addEventListener("input", () => {
    const p = Number.parseInt(lgPauseIn.value, 10);
    if (Number.isFinite(p)) lgDraft.autoPauseThreshold = p;
  });
  lgPauseWrap.appendChild(lgPauseIn);
  lgGrid.append(lgThreshWrap, lgPauseWrap);
  lgSection.appendChild(lgGrid);
  card.appendChild(lgSection);

  const alertsDraft = draft.alerts ?? { ...DEFAULT_ALERTS_DRAFT };
  draft.alerts = alertsDraft;
  const alertsSection = el(doc, "div", "cg-field");
  alertsSection.appendChild(el(doc, "span", "cg-label", "Smart Alerts (Sound & Notifications)"));
  const alertToggleDefs: Array<{ label: string; get: () => boolean; set: (v: boolean) => void }> = [
    { label: "Sound Alerts", get: () => alertsDraft.soundEnabled, set: (v) => { alertsDraft.soundEnabled = v; } },
    { label: "Desktop Notifications", get: () => alertsDraft.desktopNotificationEnabled, set: (v) => { alertsDraft.desktopNotificationEnabled = v; } },
    { label: "Notify on Task Done (Stop)", get: () => alertsDraft.notifyOnStop, set: (v) => { alertsDraft.notifyOnStop = v; } },
    { label: "Notify on Context Limit", get: () => alertsDraft.notifyOnThreshold, set: (v) => { alertsDraft.notifyOnThreshold = v; } },
    { label: "Notify on Loop Detected", get: () => alertsDraft.notifyOnLoop, set: (v) => { alertsDraft.notifyOnLoop = v; } },
  ];
  for (const item of alertToggleDefs) {
    const row = el(doc, "label", "cg-field-row");
    row.appendChild(el(doc, "span", "cg-muted", item.label));
    const cb = el(doc, "input");
    cb.type = "checkbox";
    cb.checked = item.get();
    cb.addEventListener("change", () => item.set(cb.checked));
    row.appendChild(cb);
    alertsSection.appendChild(row);
  }
  const lowQuotaRow = el(doc, "div", "cg-field-row");
  lowQuotaRow.appendChild(el(doc, "label", "cg-muted", "Low 5h Quota Alert % (0=off)"));
  const lowQuotaIn = el(doc, "input", "cg-input cg-nums");
  lowQuotaIn.type = "number";
  lowQuotaIn.min = "0";
  lowQuotaIn.max = "50";
  lowQuotaIn.value = String(alertsDraft.notifyOnLowQuotaPct);
  lowQuotaIn.addEventListener("input", () => {
    const p = Number.parseInt(lowQuotaIn.value, 10);
    if (Number.isFinite(p)) alertsDraft.notifyOnLowQuotaPct = p;
  });
  lowQuotaRow.appendChild(lowQuotaIn);
  alertsSection.appendChild(lowQuotaRow);
  card.appendChild(alertsSection);

  const footer = el(doc, "div", "cg-actions");
  const canServerTools = Boolean(state.hostCapabilities?.serverTools);
  footer.appendChild(
    createButton(doc, "Save Settings", {
      className: "cg-btn cg-btn-primary",
      disabled: !canServerTools || state.loading,
      onClick: () => {
        void controller.saveSettings({ ...draft });
      },
    }),
  );
  footer.appendChild(
    createButton(doc, "Test Sound & Alert", {
      className: "cg-btn",
      disabled: !canServerTools || state.loading,
      onClick: () => {
        void controller.triggerTestAlert();
      },
    }),
  );
  footer.appendChild(
    createButton(doc, "Cancel", {
      onClick: onClose,
    }),
  );
  card.appendChild(footer);
  return card;
}

function renderOverviewView(
  doc: Document,
  overview: OverviewPayload,
  state: DashboardState,
  controller: DashboardController,
  onToggleSubagents: () => void,
): HTMLElement[] {
  const nodes: HTMLElement[] = [];

  const mainConversations = overview.conversations.filter((c) => !c.isSubagent);
  const subagentCount = overview.conversations.length - mainConversations.length;
  const visibleConversations =
    state.hideSubagents && subagentCount > 0 && mainConversations.length > 0
      ? mainConversations
      : overview.conversations;

  const summaryCard = el(doc, "section", "cg-card");
  const head = el(doc, "div", "cg-card-head");
  const countLabel =
    subagentCount > 0 && state.hideSubagents && mainConversations.length > 0
      ? `Recent Conversations (${visibleConversations.length} main · ${subagentCount} subagents hidden)`
      : `Recent Conversations (${visibleConversations.length})`;
  head.appendChild(el(doc, "span", "cg-card-title", countLabel));

  const headActions = el(doc, "div", "cg-header-controls");
  if (subagentCount > 0 && mainConversations.length > 0) {
    headActions.appendChild(
      createButton(
        doc,
        state.hideSubagents ? `Show Subagents (${subagentCount})` : "Hide Subagents",
        {
          className: state.hideSubagents ? "cg-btn cg-btn-sm" : "cg-btn cg-btn-sm cg-btn-active",
          onClick: onToggleSubagents,
        },
      ),
    );
  }
  head.appendChild(headActions);
  summaryCard.appendChild(head);

  summaryCard.appendChild(
    el(
      doc,
      "div",
      "cg-muted cg-nums",
      `Global threshold: ${formatTokens(overview.thresholdTokens)} · Warn at ${overview.warnPercent}% · Mode: ${overview.mode}`,
    ),
  );
  nodes.push(summaryCard);

  if (visibleConversations.length === 0) {
    const emptyCard = el(doc, "section", "cg-card cg-empty");
    emptyCard.appendChild(
      el(
        doc,
        "div",
        undefined,
        "No recent Antigravity transcripts discovered yet. Start or continue a conversation to track token usage.",
      ),
    );
    nodes.push(emptyCard);
    return nodes;
  }

  const list = el(doc, "div", "cg-conv-list");
  const activeId = overview.active?.conversationId || null;
  const canServerTools = Boolean(state.hostCapabilities?.serverTools);

  for (const conv of visibleConversations) {
    const card = el(doc, "button", "cg-conv-card");
    card.type = "button";
    card.disabled = !canServerTools;
    card.addEventListener("click", () => {
      void controller.selectConversation(conv.conversationId);
    });

    const top = el(doc, "div", "cg-conv-top");
    const idWrap = el(doc, "div", "cg-conv-left");
    idWrap.appendChild(
      el(doc, "span", statusPillClass(conv.status), statusDisplayLabel(conv.status)),
    );
    const displayTitle = (conv.title || "").trim() || conv.conversationId;
    const titleEl = el(doc, "span", "cg-conv-title", displayTitle);
    titleEl.title = `${displayTitle} (${conv.conversationId})`;
    idWrap.appendChild(titleEl);
    if (displayTitle !== conv.conversationId) {
      const subIdEl = el(doc, "span", "cg-conv-subid", shortConvId(conv.conversationId));
      subIdEl.title = conv.conversationId;
      idWrap.appendChild(subIdEl);
    }

    const badges = el(doc, "div", "cg-badge-row");
    if (activeId && conv.conversationId === activeId) {
      badges.appendChild(el(doc, "span", "cg-pill cg-pill-info", "Active"));
    }
    if (conv.isSubagent) {
      badges.appendChild(
        el(doc, "span", "cg-pill cg-pill-muted", conv.agentName || "Subagent"),
      );
    }
    badges.appendChild(
      el(doc, "span", "cg-pill cg-pill-muted", conv.sourceApp || "Antigravity"),
    );
    top.append(idWrap, badges);
    card.appendChild(top);

    const track = el(doc, "div", "cg-track cg-track-sm");
    const fill = el(doc, "div", statusFillClass(conv.status));
    fill.style.width = `${clampPercent(conv.thresholdPercent).toFixed(1)}%`;
    track.appendChild(fill);
    card.appendChild(track);

    const foot = el(doc, "div", "cg-conv-foot cg-nums");
    foot.appendChild(
      el(
        doc,
        "span",
        undefined,
        `${formatTokens(conv.effectiveTokens)} / ${formatTokens(conv.thresholdTokens)} (${conv.thresholdPercent.toFixed(1)}%)`,
      ),
    );

    const rightMeta = el(doc, "span", "cg-mono", conv.model || "No model");
    if (conv.compactionCount > 0) {
      rightMeta.textContent = `${conv.model || "No model"} · ${conv.compactionCount} compact`;
    }
    foot.appendChild(rightMeta);
    card.appendChild(foot);

    list.appendChild(card);
  }
  nodes.push(list);
  return nodes;
}

function renderHighlightedText(
  doc: Document,
  text: string,
  spans: Array<[number, number]>,
): HTMLElement {
  const container = el(doc, "div", "cg-snippet");
  const sorted = [...spans]
    .filter(([s, e]) => Number.isFinite(s) && Number.isFinite(e) && s >= 0 && e > s && e <= text.length)
    .sort((a, b) => a[0] - b[0]);
  let cursor = 0;
  for (const [start, end] of sorted) {
    if (start > cursor) {
      container.appendChild(doc.createTextNode(text.slice(cursor, start)));
    }
    if (end > start && start >= cursor) {
      container.appendChild(el(doc, "mark", "cg-mark", text.slice(start, end)));
      cursor = end;
    }
  }
  if (cursor < text.length) {
    container.appendChild(doc.createTextNode(text.slice(cursor)));
  }
  return container;
}

function renderSearchView(
  doc: Document,
  state: DashboardState,
  controller: DashboardController,
  onSearchInput: (query: string) => void,
  onCopyResult: (item: SearchResultItem) => void,
): HTMLElement[] {
  const nodes: HTMLElement[] = [];
  const controlsCard = el(doc, "section", "cg-card");
  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Cross-Chat Search"));
  if (state.searchData) {
    head.appendChild(
      el(
        doc,
        "span",
        "cg-muted cg-nums",
        `${state.searchData.matchedCount} match(es) / ${state.searchData.totalIndexed} chats`,
      ),
    );
  }
  controlsCard.appendChild(head);

  const searchBar = el(doc, "div", "cg-search-bar");
  const input = el(doc, "input", "cg-input");
  input.type = "text";
  input.placeholder = "Search transcripts, errors, artifacts, files...";
  input.value = state.searchQuery;
  input.addEventListener("input", () => {
    onSearchInput(input.value);
  });
  input.addEventListener("keydown", (evt) => {
    if ((evt as KeyboardEvent).key === "Enter") {
      void controller.runSearch(input.value, state.searchScope, state.searchIncludeSubagents);
    }
  });
  searchBar.appendChild(input);
  searchBar.appendChild(
    createButton(doc, "Search", {
      className: "cg-btn cg-btn-primary cg-btn-sm",
      disabled: state.loading,
      onClick: () => {
        void controller.runSearch(input.value, state.searchScope, state.searchIncludeSubagents);
      },
    }),
  );
  controlsCard.appendChild(searchBar);

  const filterRow = el(doc, "div", "cg-subcard-row");
  const scopesWrap = el(doc, "div", "cg-segmented");
  const scopeDefs: Array<{ id: SearchScope; label: string }> = [
    { id: "all", label: "All" },
    { id: "messages", label: "Messages" },
    { id: "artifacts", label: "Artifacts" },
    { id: "files", label: "Files" },
  ];
  for (const sc of scopeDefs) {
    scopesWrap.appendChild(
      createButton(doc, sc.label, {
        className: state.searchScope === sc.id ? "cg-btn cg-btn-sm cg-btn-active" : "cg-btn cg-btn-sm",
        onClick: () => {
          void controller.runSearch(state.searchQuery, sc.id, state.searchIncludeSubagents);
        },
      }),
    );
  }
  filterRow.appendChild(scopesWrap);

  const subLabel = el(doc, "label", "cg-field-row");
  subLabel.appendChild(el(doc, "span", "cg-muted", "Subagents"));
  const subCb = el(doc, "input");
  subCb.type = "checkbox";
  subCb.checked = state.searchIncludeSubagents;
  subCb.addEventListener("change", () => {
    void controller.runSearch(state.searchQuery, state.searchScope, subCb.checked);
  });
  subLabel.appendChild(subCb);
  filterRow.appendChild(subLabel);
  controlsCard.appendChild(filterRow);
  nodes.push(controlsCard);

  if (!state.searchData) {
    const empty = el(doc, "section", "cg-card cg-empty");
    empty.appendChild(el(doc, "div", undefined, "Indexing recent conversations..."));
    nodes.push(empty);
    return nodes;
  }

  if (state.searchData.results.length === 0) {
    const empty = el(doc, "section", "cg-card cg-empty");
    empty.appendChild(
      el(doc, "div", undefined, "No matching chats found. Try fewer terms or toggle Subagents."),
    );
    nodes.push(empty);
    return nodes;
  }

  for (const item of state.searchData.results) {
    const card = el(doc, "section", "cg-card");
    const rHead = el(doc, "div", "cg-card-head");
    const titleWrap = el(doc, "div");
    titleWrap.appendChild(el(doc, "div", "cg-card-title", item.title || shortConvId(item.convId)));
    const metaParts = [shortConvId(item.convId)];
    if (item.sourceApp) metaParts.push(item.sourceApp);
    if (item.updatedAt) metaParts.push(item.updatedAt.slice(0, 16).replace("T", " "));
    titleWrap.appendChild(el(doc, "div", "cg-muted", metaParts.join(" · ")));
    rHead.appendChild(titleWrap);
    rHead.appendChild(el(doc, "span", "cg-pill cg-pill-muted cg-nums", `${item.matchCount} hits`));
    card.appendChild(rHead);

    if (item.editedFiles && item.editedFiles.length > 0) {
      const chips = el(doc, "div", "cg-chip-row");
      for (const f of item.editedFiles.slice(0, 4)) {
        chips.appendChild(el(doc, "span", "cg-chip", f));
      }
      card.appendChild(chips);
    }

    for (const snip of item.snippets || []) {
      const snipWrap = el(doc, "div");
      if (snip.label) {
        snipWrap.appendChild(el(doc, "div", "cg-muted", `${snip.source} · ${snip.label}`));
      }
      snipWrap.appendChild(renderHighlightedText(doc, snip.text, snip.spans || []));
      card.appendChild(snipWrap);
    }

    const actions = el(doc, "div", "cg-actions");
    actions.appendChild(
      createButton(doc, "Open in Monitor", {
        className: "cg-btn cg-btn-primary cg-btn-sm",
        onClick: () => {
          void controller.selectConversation(item.convId);
        },
      }),
    );
    actions.appendChild(
      createButton(doc, "Copy Context", {
        className: "cg-btn cg-btn-sm",
        onClick: () => {
          onCopyResult(item);
        },
      }),
    );
    card.appendChild(actions);
    nodes.push(card);
  }

  return nodes;
}

function renderSetupView(
  doc: Document,
  state: DashboardState,
  controller: DashboardController,
): HTMLElement[] {
  const nodes: HTMLElement[] = [];
  const headerCard = el(doc, "section", "cg-card");
  const head = el(doc, "div", "cg-card-head");
  head.appendChild(el(doc, "span", "cg-card-title", "Startup Context & Customizations Footprint"));
  headerCard.appendChild(head);

  const bar = el(doc, "div", "cg-setup-bar");
  const wsIn = el(doc, "input", "cg-input");
  wsIn.type = "text";
  wsIn.placeholder = "Workspace path (auto-inferred if blank)";
  wsIn.value = state.footprintWorkspaceInput || state.footprintData?.workspacePath || "";
  wsIn.addEventListener("input", () => {
    state.footprintWorkspaceInput = wsIn.value;
  });
  bar.appendChild(wsIn);
  bar.appendChild(
    createButton(doc, "Rescan", {
      className: "cg-btn cg-btn-primary cg-btn-sm",
      disabled: state.loading,
      onClick: () => {
        void controller.refreshFootprint(wsIn.value);
      },
    }),
  );
  headerCard.appendChild(bar);
  nodes.push(headerCard);

  const fp = state.footprintData;
  if (!fp) {
    const empty = el(doc, "section", "cg-card cg-empty");
    empty.appendChild(el(doc, "div", undefined, "Scanning Antigravity rules, plugins, skills, MCPs, and hooks..."));
    nodes.push(empty);
    return nodes;
  }

  const kpiGrid = el(doc, "div", "cg-metrics-grid");
  const kpis = [
    { label: "Est. Startup", value: formatTokens(fp.summary.estimatedStartupTokens), sub: "always-loaded baseline" },
    {
      label: "Observed",
      value: fp.summary.observedStartupTokens !== null ? formatTokens(fp.summary.observedStartupTokens) : "—",
      sub: "first turn prompt",
    },
    { label: "On-Demand Skills", value: formatTokens(fp.summary.onDemandSkillsTokens), sub: `${fp.summary.skillsCount + fp.summary.builtinSkillsCount} total skills` },
    {
      label: "Customizations",
      value: `${fp.summary.rulesCount}R · ${fp.summary.skillsCount}S`,
      sub: `${fp.summary.mcpServersCount} MCP · ${fp.summary.hooksCount} hooks`,
    },
  ];
  for (const kpi of kpis) {
    const tile = el(doc, "div", "cg-metric");
    tile.appendChild(el(doc, "div", "cg-metric-label", kpi.label));
    tile.appendChild(el(doc, "div", "cg-metric-value cg-nums", kpi.value));
    tile.appendChild(el(doc, "div", "cg-muted", kpi.sub));
    kpiGrid.appendChild(tile);
  }
  const kpiCard = el(doc, "section", "cg-card");
  kpiCard.appendChild(kpiGrid);
  nodes.push(kpiCard);

  if (fp.warnings.length > 0) {
    const warnSection = el(doc, "section", "cg-card");
    warnSection.appendChild(el(doc, "div", "cg-card-title", `Diagnostics (${fp.warnings.length})`));
    for (const w of fp.warnings) {
      const box = el(doc, "div", w.severity === "info" ? "cg-warn-card cg-warn-card-info" : "cg-warn-card");
      box.appendChild(el(doc, "strong", undefined, w.title));
      box.appendChild(el(doc, "div", undefined, w.detail));
      if (w.path) box.appendChild(el(doc, "div", "cg-path", w.path));
      warnSection.appendChild(box);
    }
    nodes.push(warnSection);
  }

  const rulesCard = el(doc, "section", "cg-card");
  rulesCard.appendChild(el(doc, "div", "cg-card-title", `Always-Loaded Rules (${fp.rules.length})`));
  if (fp.rules.length === 0) {
    rulesCard.appendChild(el(doc, "div", "cg-muted", "No GEMINI.md / AGENTS.md / rules found."));
  } else {
    for (const r of fp.rules) {
      const row = el(doc, "div", "cg-subcard");
      const top = el(doc, "div", "cg-subcard-row");
      top.appendChild(el(doc, "span", "cg-label", `${r.name} (${r.scope})`));
      top.appendChild(el(doc, "span", "cg-muted cg-nums", `~${formatTokens(r.estTokens)} tok`));
      row.appendChild(top);
      row.appendChild(el(doc, "div", "cg-path", r.path));
      rulesCard.appendChild(row);
    }
  }
  nodes.push(rulesCard);

  const skillsCard = el(doc, "section", "cg-card");
  skillsCard.appendChild(
    el(doc, "div", "cg-card-title", `Plugins (${fp.plugins.length}) & Skills (${fp.skills.length} custom)`),
  );
  if (fp.builtinSkillsSummary && fp.builtinSkillsSummary.count > 0) {
    skillsCard.appendChild(
      el(
        doc,
        "div",
        "cg-muted cg-nums",
        `Built-in skills: ${fp.builtinSkillsSummary.count} (~${formatTokens(fp.builtinSkillsSummary.frontmatterTokens)} baseline · ~${formatTokens(fp.builtinSkillsSummary.onDemandTokens)} on-demand)`,
      ),
    );
  }
  for (const s of fp.skills.slice(0, 20)) {
    const row = el(doc, "div", "cg-subcard");
    const top = el(doc, "div", "cg-subcard-row");
    top.appendChild(el(doc, "span", "cg-label", `${s.name} · ${s.source} (${s.scope})`));
    top.appendChild(
      el(doc, "span", "cg-muted cg-nums", `${s.frontmatterTokens} base / ${formatTokens(s.bodyTokens)} body`),
    );
    row.appendChild(top);
    row.appendChild(el(doc, "div", "cg-path", s.path));
    skillsCard.appendChild(row);
  }
  nodes.push(skillsCard);

  const infraCard = el(doc, "section", "cg-card");
  infraCard.appendChild(
    el(doc, "div", "cg-card-title", `MCP Servers (${fp.mcpServers.length}) & Hooks (${fp.hooks.length})`),
  );
  for (const m of fp.mcpServers) {
    const row = el(doc, "div", "cg-subcard");
    const top = el(doc, "div", "cg-subcard-row");
    top.appendChild(el(doc, "span", "cg-label", `${m.name} (${m.scope})`));
    top.appendChild(
      el(doc, "span", "cg-muted cg-nums", m.enabled ? `~${m.estTokens} est. tok · ${m.envKeyCount} env keys` : "disabled"),
    );
    row.appendChild(top);
    if (m.commandPreview) row.appendChild(el(doc, "div", "cg-path", m.commandPreview));
    infraCard.appendChild(row);
  }
  for (const h of fp.hooks) {
    const row = el(doc, "div", "cg-subcard");
    const top = el(doc, "div", "cg-subcard-row");
    top.appendChild(el(doc, "span", "cg-label", `hook: ${h.name} (${h.scope})`));
    top.appendChild(el(doc, "span", "cg-muted", h.events.join(", ")));
    row.appendChild(top);
    infraCard.appendChild(row);
  }
  nodes.push(infraCard);

  return nodes;
}

function syncDraftFromStatus(state: DashboardState, status: StatusPayload): void {
  if (state.settingsOpen) return;
  state.settingsDraft = {
    enabled: status.enabled,
    thresholdTokens: status.baseThresholdTokens || status.effectiveThresholdTokens,
    warnPercent: status.warnPercent,
    mode: status.mode,
    autoRefreshSeconds: state.autoRefreshSeconds,
    loopGuard: status.loopGuard ? { ...status.loopGuard } : { ...DEFAULT_LOOP_GUARD_DRAFT },
    alerts: status.alerts ? { ...status.alerts } : { ...DEFAULT_ALERTS_DRAFT },
  };
}

function syncDraftFromOverview(
  state: DashboardState,
  overview: OverviewPayload,
): void {
  if (state.settingsOpen) return;
  state.settingsDraft = {
    enabled: overview.enabled,
    thresholdTokens: overview.thresholdTokens,
    warnPercent: overview.warnPercent,
    mode: overview.mode,
    autoRefreshSeconds: state.autoRefreshSeconds,
  };
}

/**
 * Mount the Context Guard dashboard onto #app and connect to the MCP App host.
 * Registers all four required MCP App handlers strictly before app.connect().
 */
export async function mountDashboard(
  deps: MountDependencies,
): Promise<DashboardController> {
  const doc = deps.doc ?? document;
  const applyTheme = deps.applyTheme ?? applyDocumentTheme;
  const applyStyles = deps.applyStyles ?? applyHostStyleVariables;
  const applyFonts = deps.applyFonts ?? applyHostFonts;
  const app = deps.app;

  const state: DashboardState = {
    connected: false,
    loading: false,
    autoRefresh: deps.autoRefresh ?? true,
    autoRefreshSeconds: DEFAULT_AUTO_REFRESH_SECONDS,
    currentView: "status",
    userSwitchedView: false,
    hasInitialToolResult: false,
    activeConversationId: null,
    overviewLimit: 20,
    statusData: null,
    overviewData: null,
    searchData: null,
    footprintData: null,
    searchQuery: "",
    searchScope: "all",
    searchIncludeSubagents: false,
    footprintWorkspaceInput: "",
    quotaData: null,
    handoffData: null,
    handoffSentToNewChat: false,
    handoffNote: "",
    handoffOpen: false,
    settingsOpen: false,
    settingsDraft: {
      enabled: true,
      thresholdTokens: 300_000,
      warnPercent: 80,
      mode: "stop",
      autoRefreshSeconds: DEFAULT_AUTO_REFRESH_SECONDS,
      loopGuard: { ...DEFAULT_LOOP_GUARD_DRAFT },
      alerts: { ...DEFAULT_ALERTS_DRAFT },
    },
    banner: null,
    hostCapabilities: undefined,
    hostContext: undefined,
    overviewPrefetched: false,
    hideSubagents: true,
  };

  let initialFallbackTimer: ReturnType<typeof setTimeout> | null = null;
  let autoRefreshTimer: ReturnType<typeof setInterval> | null = null;
  let quotaPollTimer: ReturnType<typeof setTimeout> | null = null;
  let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  let quotaPollAttempts = 0;
  let disposed = false;

  const clearInitialFallbackTimer = (): void => {
    if (initialFallbackTimer !== null) {
      clearTimeout(initialFallbackTimer);
      initialFallbackTimer = null;
    }
  };

  const stopQuotaPollTimer = (): void => {
    if (quotaPollTimer !== null) {
      clearTimeout(quotaPollTimer);
      quotaPollTimer = null;
    }
  };

  const scheduleQuotaPollIfFetching = (): void => {
    stopQuotaPollTimer();
    if (
      disposed ||
      !state.quotaData?.fetching ||
      !state.hostCapabilities?.serverTools ||
      quotaPollAttempts >= 12
    ) {
      return;
    }
    const delayMs = Math.max(50, deps.autoRefreshIntervalMs ? deps.autoRefreshIntervalMs : 2500);
    quotaPollTimer = setTimeout(() => {
      quotaPollAttempts += 1;
      void invokeServerTool("app_quota", { refresh: false }, { skipIfBusy: true, showLoading: false }).then(
        (res) => {
          if (res?.structuredContent) {
            applyStructuredResult(res.structuredContent, { switchView: false });
            render();
          }
        },
      );
    }, delayMs);
  };

  const stopAutoRefreshTimer = (): void => {
    if (autoRefreshTimer !== null) {
      clearInterval(autoRefreshTimer);
      autoRefreshTimer = null;
    }
  };

  const startAutoRefreshTimer = (): void => {
    stopAutoRefreshTimer();
    if (!state.autoRefresh || disposed || !state.hostCapabilities?.serverTools) {
      return;
    }
    const intervalMs = Math.max(
      10,
      deps.autoRefreshIntervalMs ?? state.autoRefreshSeconds * 1000,
    );
    autoRefreshTimer = setInterval(() => {
      if (disposed || doc.hidden || state.settingsOpen || state.handoffOpen) {
        return;
      }
      void controller.refreshCurrentView(true);
    }, intervalMs);
  };

  const onVisibilityChange = (): void => {
    if (!disposed && !doc.hidden && state.autoRefresh && state.hostCapabilities?.serverTools) {
      if (!state.settingsOpen && !state.handoffOpen) {
        void controller.refreshCurrentView(true);
      }
    }
  };
  doc.addEventListener?.("visibilitychange", onVisibilityChange);

  const applyHostContext = (ctx: McpUiHostContext | undefined): void => {
    if (!ctx) return;
    state.hostContext = ctx;
    if (ctx.theme === "light" || ctx.theme === "dark") {
      applyTheme(ctx.theme);
    }
    if (ctx.styles?.variables) {
      applyStyles(ctx.styles.variables);
    }
    if (ctx.styles?.css?.fonts) {
      applyFonts(ctx.styles.css.fonts);
    }
    const toolName = ctx.toolInfo?.tool?.name;
    if (
      toolName === "context_overview" &&
      !state.hasInitialToolResult &&
      !state.userSwitchedView
    ) {
      state.currentView = "overview";
    }
  };

  const toolMutex = createToolMutex(
    async (name: string, args: Record<string, unknown>) =>
      app.callServerTool({ name, arguments: args }),
  );

  const invokeServerTool = async (
    name: string,
    args: Record<string, unknown>,
    options: { skipIfBusy?: boolean; showLoading?: boolean } = {},
  ): Promise<ToolResultLike | null> => {
    if (!state.hostCapabilities?.serverTools) {
      state.banner = {
        kind: "error",
        text: "Host does not advertise serverTools capability.",
      };
      render();
      return null;
    }
    if (options.skipIfBusy && toolMutex.isBusy()) {
      return null;
    }

    const showLoading = options.showLoading ?? true;
    if (showLoading) {
      state.loading = true;
      render();
    }

    try {
      const result = await toolMutex.run(() => [name, args]);
      if (result.isError) {
        state.banner = {
          kind: "error",
          text: extractToolErrorText(result, `Tool ${name} returned an error`),
        };
        return null;
      }
      return result;
    } catch (err) {
      state.banner = {
        kind: "error",
        text: extractToolErrorText(err, `Failed calling ${name}`),
      };
      return null;
    } finally {
      if (showLoading) {
        state.loading = false;
      }
      render();
    }
  };

  const applyStructuredResult = (
    sc: unknown,
    options: { switchView?: boolean } = {},
  ): void => {
    if (isStatusPayload(sc)) {
      const prevSecs = state.autoRefreshSeconds;
      if (typeof sc.autoRefreshSeconds === "number") {
        state.autoRefreshSeconds = normalizeAutoRefreshSeconds(
          sc.autoRefreshSeconds,
          state.autoRefreshSeconds,
        );
      }
      if (isQuotaPayload(sc.modelQuota)) {
        state.quotaData = sc.modelQuota;
        if (!sc.modelQuota.fetching) quotaPollAttempts = 0;
        scheduleQuotaPollIfFetching();
      }
      state.statusData = sc;
      if (sc.conversationId) {
        state.activeConversationId = sc.conversationId;
      }
      syncDraftFromStatus(state, sc);
      if (options.switchView !== false) {
        state.currentView = "status";
      }
      if (
        state.autoRefreshSeconds !== prevSecs &&
        state.autoRefresh &&
        state.hostCapabilities?.serverTools &&
        !disposed
      ) {
        startAutoRefreshTimer();
      }
      return;
    }
    if (isOverviewPayload(sc)) {
      const prevSecs = state.autoRefreshSeconds;
      if (typeof sc.autoRefreshSeconds === "number") {
        state.autoRefreshSeconds = normalizeAutoRefreshSeconds(
          sc.autoRefreshSeconds,
          state.autoRefreshSeconds,
        );
      }
      if (isQuotaPayload(sc.modelQuota)) {
        state.quotaData = sc.modelQuota;
        if (!sc.modelQuota.fetching) quotaPollAttempts = 0;
        scheduleQuotaPollIfFetching();
      }
      state.overviewData = sc;
      if (!state.activeConversationId && sc.active?.conversationId) {
        state.activeConversationId = sc.active.conversationId;
      }
      if (state.currentView === "overview") {
        syncDraftFromOverview(state, sc);
      }
      if (options.switchView !== false) {
        state.currentView = "overview";
      }
      if (
        state.autoRefreshSeconds !== prevSecs &&
        state.autoRefresh &&
        state.hostCapabilities?.serverTools &&
        !disposed
      ) {
        startAutoRefreshTimer();
      }
      return;
    }
    if (isRecord(sc) && sc.view === "quota" && isQuotaPayload(sc.modelQuota)) {
      state.quotaData = sc.modelQuota;
      if (!sc.modelQuota.fetching) quotaPollAttempts = 0;
      scheduleQuotaPollIfFetching();
      return;
    }
    if (isSearchPayload(sc)) {
      state.searchData = sc;
      if (options.switchView !== false) {
        state.currentView = "search";
      }
      return;
    }
    if (isFootprintPayload(sc)) {
      state.footprintData = sc;
      if (!state.footprintWorkspaceInput && sc.workspacePath) {
        state.footprintWorkspaceInput = sc.workspacePath;
      }
      if (options.switchView !== false) {
        state.currentView = "footprint";
      }
      return;
    }
    if (isHandoffPayload(sc)) {
      state.handoffData = sc;
      state.handoffOpen = true;
      state.handoffSentToNewChat = false;
    }
  };

  const prefetchOverviewOnce = (): void => {
    if (
      state.overviewPrefetched ||
      state.overviewData !== null ||
      !state.hostCapabilities?.serverTools ||
      toolMutex.isBusy()
    ) {
      return;
    }
    state.overviewPrefetched = true;
    void invokeServerTool(
      "app_refresh",
      { view: "overview", limit: state.overviewLimit },
      { skipIfBusy: true, showLoading: false },
    ).then((res) => {
      if (res && isOverviewPayload(res.structuredContent)) {
        state.overviewData = res.structuredContent;
        render();
      }
    });
  };

  const controller: DashboardController = {
    dispose(): void {
      disposed = true;
      clearInitialFallbackTimer();
      stopAutoRefreshTimer();
      stopQuotaPollTimer();
      if (searchDebounceTimer !== null) {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = null;
      }
      doc.removeEventListener?.("visibilitychange", onVisibilityChange);
    },

    async refreshCurrentView(skipIfBusy = false): Promise<void> {
      const requestedView = state.currentView;
      if (requestedView === "search") {
        await controller.runSearch(state.searchQuery, state.searchScope, state.searchIncludeSubagents);
        return;
      }
      if (requestedView === "footprint") {
        await controller.refreshFootprint(state.footprintWorkspaceInput);
        return;
      }
      const args: Record<string, unknown> =
        requestedView === "overview"
          ? { view: "overview", limit: state.overviewLimit }
          : {
              view: "status",
              ...(state.activeConversationId
                ? { conversationId: state.activeConversationId }
                : {}),
            };
      const res = await invokeServerTool("app_refresh", args, {
        skipIfBusy,
        showLoading: !skipIfBusy,
      });
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, {
          switchView: !skipIfBusy || state.currentView === requestedView,
        });
        render();
      }
    },

    async switchToOverview(): Promise<void> {
      state.userSwitchedView = true;
      state.currentView = "overview";
      render();
      const res = await invokeServerTool("app_refresh", {
        view: "overview",
        limit: state.overviewLimit,
      });
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: true });
        render();
      }
    },

    async switchToSearch(): Promise<void> {
      state.userSwitchedView = true;
      state.currentView = "search";
      render();
      await controller.runSearch(state.searchQuery, state.searchScope, state.searchIncludeSubagents);
    },

    async switchToFootprint(workspacePath?: string): Promise<void> {
      state.userSwitchedView = true;
      state.currentView = "footprint";
      render();
      await controller.refreshFootprint(workspacePath ?? state.footprintWorkspaceInput);
    },

    async runSearch(
      query = state.searchQuery,
      scope = state.searchScope,
      includeSubagents = state.searchIncludeSubagents,
    ): Promise<void> {
      state.searchQuery = query;
      state.searchScope = scope;
      state.searchIncludeSubagents = includeSubagents;
      const res = await invokeServerTool("app_search", {
        query,
        scope,
        includeSubagents,
        limit: 25,
      });
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: false });
        render();
      }
    },

    async refreshFootprint(workspacePath = state.footprintWorkspaceInput): Promise<void> {
      const args: Record<string, unknown> = {};
      if (workspacePath && workspacePath.trim()) {
        args.workspacePath = workspacePath.trim();
      }
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }
      const res = await invokeServerTool("app_footprint", args);
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: false });
        render();
      }
    },

    async clearActiveLoop(): Promise<void> {
      const args: Record<string, unknown> = {};
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }
      const res = await invokeServerTool("app_clear_loop", args);
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: false });
        state.banner = { kind: "info", text: "Loop Guard alert dismissed." };
        render();
      }
    },

    async triggerTestAlert(): Promise<void> {
      const res = await invokeServerTool("app_test_alert", {});
      if (res?.structuredContent) {
        const sc = res.structuredContent as Record<string, unknown>;
        state.banner = sc.sent
          ? { kind: "success", text: "Test sound & notification triggered." }
          : { kind: "info", text: `Test alert skipped (${String(sc.reason || "disabled")}).` };
        render();
      }
    },

    async selectConversation(conversationId: string): Promise<void> {
      state.userSwitchedView = true;
      state.activeConversationId = conversationId;
      state.currentView = "status";
      render();
      const res = await invokeServerTool("app_refresh", {
        view: "status",
        conversationId,
      });
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: true });
        render();
      }
    },

    async triggerSnooze(deltaTokens: number): Promise<void> {
      const args: Record<string, unknown> = { deltaTokens };
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }
      const res = await invokeServerTool("app_snooze", args);
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: true });
        state.banner = {
          kind: "success",
          text: `Snoozed alerts by +${formatTokens(deltaTokens)} tokens.`,
        };
        render();
      }
    },

    async triggerResetSnooze(): Promise<void> {
      const args: Record<string, unknown> = { resetSnooze: true };
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }
      const res = await invokeServerTool("app_snooze", args);
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: true });
        state.banner = {
          kind: "info",
          text: "Snooze cleared for active conversation.",
        };
        render();
      }
    },

    async triggerCompact(): Promise<void> {
      const copied = await copyPlainText(doc, "/compact");
      state.banner = copied
        ? {
            kind: "success",
            text: 'Copied "/compact" — paste & send it in the chat box to compact (MCP Apps cannot run slash commands directly).',
          }
        : {
            kind: "info",
            text: "Type /compact in the chat box and press Enter to compact (MCP Apps cannot run slash commands directly).",
          };
      render();
    },

    showStatusView(): void {
      state.userSwitchedView = true;
      state.currentView = "status";
      render();
    },

    async triggerHandoff(note = "", autoSend = true): Promise<void> {
      const args: Record<string, unknown> = {};
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }
      if (note.trim()) {
        args.note = note.trim();
      }
      const res = await invokeServerTool("app_build_handoff", args);
      if (!res || !isHandoffPayload(res.structuredContent)) {
        return;
      }

      const handoff = res.structuredContent;
      state.handoffData = handoff;
      state.handoffOpen = true;
      state.handoffSentToNewChat = false;

      if (autoSend && state.hostCapabilities?.message) {
        try {
          const msgPayload = {
            role: "user",
            content: [{ type: "text", text: handoff.brief }],
            _meta: { "com.google.antigravity/message": { target: "new" } },
          } as unknown as Parameters<App["sendMessage"]>[0];
          const msgRes = await app.sendMessage(msgPayload);
          if (!msgRes?.isError) {
            state.handoffSentToNewChat = true;
            state.banner = {
              kind: "success",
              text: "Smart Handoff brief sent to a new conversation.",
            };
          } else {
            state.banner = {
              kind: "info",
              text: "Host declined opening a new chat; copy the brief below.",
            };
          }
        } catch {
          state.banner = {
            kind: "info",
            text: "Could not send directly to a new chat; copy the brief below.",
          };
        }
      } else {
        state.banner = {
          kind: "info",
          text: "Smart Handoff brief ready. Use Copy Brief below.",
        };
      }
      render();
    },

    async triggerAttachSummary(): Promise<void> {
      if (!state.hostCapabilities?.updateModelContext || !state.statusData) {
        return;
      }
      try {
        await app.updateModelContext({
          content: [
            { type: "text", text: buildAttachSummaryText(state.statusData) },
          ],
          structuredContent: {
            conversationId: state.statusData.conversationId,
            status: state.statusData.status,
            effectiveTokens: state.statusData.effectiveTokens,
            effectiveThresholdTokens: state.statusData.effectiveThresholdTokens,
            thresholdPercent: state.statusData.thresholdPercent,
          },
        });
        state.banner = {
          kind: "success",
          text: "Context summary attached for the next model turn.",
        };
      } catch (err) {
        state.banner = {
          kind: "error",
          text: extractToolErrorText(err, "Failed to update model context"),
        };
      }
      render();
    },

    async refreshQuota(): Promise<void> {
      quotaPollAttempts = 0;
      const res = await invokeServerTool(
        "app_quota",
        { refresh: true },
        { showLoading: false },
      );
      if (res?.structuredContent) {
        applyStructuredResult(res.structuredContent, { switchView: false });
        render();
      }
    },

    async saveSettings(draft: SettingsDraft): Promise<void> {
      const thresh = Math.round(draft.thresholdTokens);
      const warnPct = Math.round(draft.warnPercent);
      const hasRefreshSeconds = draft.autoRefreshSeconds !== undefined;
      const refreshSecs = hasRefreshSeconds
        ? Math.round(draft.autoRefreshSeconds as number)
        : state.autoRefreshSeconds;
      if (!Number.isFinite(thresh) || thresh < MIN_THRESHOLD_TOKENS) {
        state.banner = {
          kind: "error",
          text: `Threshold must be at least ${MIN_THRESHOLD_TOKENS.toLocaleString("en-US")} tokens.`,
        };
        render();
        return;
      }
      if (!Number.isFinite(warnPct) || warnPct < 1 || warnPct > 99) {
        state.banner = {
          kind: "error",
          text: "Warn percent must be between 1 and 99.",
        };
        render();
        return;
      }
      if (
        hasRefreshSeconds &&
        (!Number.isFinite(refreshSecs) ||
          refreshSecs < MIN_AUTO_REFRESH_SECONDS ||
          refreshSecs > MAX_AUTO_REFRESH_SECONDS)
      ) {
        state.banner = {
          kind: "error",
          text: `Auto-refresh interval must be between ${MIN_AUTO_REFRESH_SECONDS} and ${MAX_AUTO_REFRESH_SECONDS} seconds.`,
        };
        render();
        return;
      }

      const args: Record<string, unknown> = {
        enabled: draft.enabled,
        thresholdTokens: thresh,
        warnPercent: warnPct,
        mode: draft.mode,
      };
      if (hasRefreshSeconds) {
        args.autoRefreshSeconds = refreshSecs;
      }
      if (draft.loopGuard) {
        args.loopGuard = draft.loopGuard;
      }
      if (draft.alerts) {
        args.alerts = draft.alerts;
      }
      if (state.activeConversationId) {
        args.conversationId = state.activeConversationId;
      }

      const res = await invokeServerTool("app_save_settings", args);
      if (!res?.structuredContent) return;

      if (hasRefreshSeconds) {
        state.autoRefreshSeconds = normalizeAutoRefreshSeconds(
          refreshSecs,
          state.autoRefreshSeconds,
        );
        if (state.autoRefresh && state.hostCapabilities?.serverTools && !disposed) {
          startAutoRefreshTimer();
        }
      }
      applyStructuredResult(res.structuredContent, {
        switchView: state.currentView === "status",
      });
      state.settingsOpen = false;
      state.banner = {
        kind: "success",
        text: "Settings saved.",
      };

      if (state.currentView === "overview") {
        const ovRes = await invokeServerTool("app_refresh", {
          view: "overview",
          limit: state.overviewLimit,
        });
        if (ovRes?.structuredContent) {
          applyStructuredResult(ovRes.structuredContent, { switchView: true });
        }
      }
      render();
    },
  };

  const copyPlainText = async (
    targetDoc: Document,
    text: string,
    selectSource?: HTMLTextAreaElement,
  ): Promise<boolean> => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      // Fall through to execCommand fallback below.
    }

    if (selectSource) {
      selectSource.focus?.();
      selectSource.select();
      try {
        return targetDoc.execCommand("copy");
      } catch {
        return false;
      }
    }

    const body = targetDoc.body;
    if (!body) return false;
    try {
      const temp = targetDoc.createElement("textarea") as HTMLTextAreaElement;
      temp.value = text;
      temp.setAttribute("readonly", "");
      if (temp.style) {
        temp.style.position = "fixed";
        temp.style.opacity = "0";
      }
      body.appendChild(temp);
      temp.focus?.();
      temp.select?.();
      const ok = Boolean(targetDoc.execCommand?.("copy"));
      body.removeChild?.(temp);
      return ok;
    } catch {
      return false;
    }
  };

  const copyBrief = async (textarea: HTMLTextAreaElement): Promise<void> => {
    const copied = await copyPlainText(doc, textarea.value, textarea);
    state.banner = copied
      ? { kind: "success", text: "Handoff brief copied to clipboard." }
      : { kind: "info", text: "Brief selected — press Cmd/Ctrl+C to copy." };
    render();
  };

  const toggleAutoRefresh = (): void => {
    state.autoRefresh = !state.autoRefresh;
    if (state.autoRefresh && !disposed) {
      startAutoRefreshTimer();
      if (!doc.hidden && !state.settingsOpen && !state.handoffOpen) {
        void controller.refreshCurrentView(true);
      }
    } else {
      stopAutoRefreshTimer();
    }
    render();
  };

  const scheduleSearchQuery = (query: string): void => {
    state.searchQuery = query;
    if (searchDebounceTimer !== null) {
      clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
      searchDebounceTimer = null;
      void controller.runSearch(state.searchQuery, state.searchScope, state.searchIncludeSubagents);
    }, 250);
  };

  const copySearchResultContext = async (item: SearchResultItem): Promise<void> => {
    const lines = [
      `## ${item.title || item.convId} (${item.convId})`,
      item.editedFiles?.length ? `Edited files: ${item.editedFiles.join(", ")}` : "",
      ...(item.snippets || []).map((s) => `- [${s.source}] ${s.text}`),
    ].filter(Boolean);
    const copied = await copyPlainText(doc, lines.join("\n"));
    state.banner = copied
      ? { kind: "success", text: "Matched chat context copied to clipboard." }
      : { kind: "info", text: "Unable to access clipboard." };
    render();
  };

  const render = (): void => {
    const root = doc.getElementById("app");
    if (!root) return;

    const toggleSettings = (): void => {
      state.settingsOpen = !state.settingsOpen;
      if (state.settingsOpen) {
        if (state.statusData) {
          state.settingsDraft = {
            enabled: state.statusData.enabled,
            thresholdTokens:
              state.statusData.baseThresholdTokens ||
              state.statusData.effectiveThresholdTokens,
            warnPercent: state.statusData.warnPercent,
            mode: state.statusData.mode,
            autoRefreshSeconds: state.autoRefreshSeconds,
            loopGuard: state.statusData.loopGuard
              ? { ...state.statusData.loopGuard }
              : { ...DEFAULT_LOOP_GUARD_DRAFT },
            alerts: state.statusData.alerts
              ? { ...state.statusData.alerts }
              : { ...DEFAULT_ALERTS_DRAFT },
          };
        } else if (state.overviewData) {
          state.settingsDraft = {
            enabled: state.overviewData.enabled,
            thresholdTokens: state.overviewData.thresholdTokens,
            warnPercent: state.overviewData.warnPercent,
            mode: state.overviewData.mode,
            autoRefreshSeconds: state.autoRefreshSeconds,
            loopGuard: { ...DEFAULT_LOOP_GUARD_DRAFT },
            alerts: { ...DEFAULT_ALERTS_DRAFT },
          };
        }
      }
      render();
    };

    const shell = el(doc, "main", "cg-shell");
    shell.appendChild(renderHeader(doc, state, controller, toggleAutoRefresh, toggleSettings));

    const banner = renderBanner(doc, state, () => {
      state.banner = null;
      render();
    });
    if (banner) shell.appendChild(banner);

    const settingsNode = renderSettingsDrawer(
      doc,
      state,
      controller,
      () => {
        state.settingsOpen = false;
        render();
      },
      render,
    );

    const handoffNode = renderHandoffPanel(
      doc,
      state,
      controller,
      (ta) => {
        void copyBrief(ta);
      },
      () => {
        state.handoffOpen = false;
        render();
      },
    );

    if (state.currentView === "search") {
      if (settingsNode) shell.appendChild(settingsNode);
      for (const node of renderSearchView(
        doc,
        state,
        controller,
        scheduleSearchQuery,
        (item) => {
          void copySearchResultContext(item);
        },
      )) {
        shell.appendChild(node);
      }
    } else if (state.currentView === "footprint") {
      if (settingsNode) shell.appendChild(settingsNode);
      for (const node of renderSetupView(doc, state, controller)) {
        shell.appendChild(node);
      }
    } else if (state.currentView === "overview" && state.overviewData) {
      if (settingsNode) shell.appendChild(settingsNode);
      if (handoffNode) shell.appendChild(handoffNode);
      const overviewNodes = renderOverviewView(
        doc,
        state.overviewData,
        state,
        controller,
        () => {
          state.hideSubagents = !state.hideSubagents;
          render();
        },
      );
      if (overviewNodes.length > 0) {
        shell.appendChild(overviewNodes[0]);
      }
      if (state.quotaData) {
        shell.appendChild(renderModelQuotaCard(doc, state.quotaData, state, controller));
      }
      for (const node of overviewNodes.slice(1)) {
        shell.appendChild(node);
      }
    } else if (state.statusData) {
      const loopBanner = renderLoopBanner(
        doc,
        state.statusData.activeLoop,
        state.loading,
        () => {
          void controller.clearActiveLoop();
        },
      );
      if (loopBanner) shell.appendChild(loopBanner);
      shell.appendChild(
        renderDetailSubheader(
          doc,
          state.statusData,
          state,
          controller,
        ),
      );
      shell.appendChild(renderGaugeCard(doc, state.statusData));
      shell.appendChild(renderMetricGrid(doc, state.statusData));
      if (state.quotaData) {
        shell.appendChild(renderModelQuotaCard(doc, state.quotaData, state, controller));
      }
      shell.appendChild(renderSparklineCard(doc, state.statusData));
      shell.appendChild(renderBloatCard(doc, state.statusData));
      shell.appendChild(
        renderActionBar(
          doc,
          state.statusData,
          state,
          controller,
        ),
      );
      if (handoffNode) shell.appendChild(handoffNode);
      if (settingsNode) shell.appendChild(settingsNode);
    } else {
      const empty = el(doc, "section", "cg-card cg-empty");
      empty.appendChild(
        el(
          doc,
          "div",
          undefined,
          state.connected
            ? "Loading Agy Lens telemetry..."
            : "Waiting for MCP App host connection...",
        ),
      );
      shell.appendChild(empty);
      if (handoffNode) shell.appendChild(handoffNode);
      if (settingsNode) shell.appendChild(settingsNode);
    }

    root.replaceChildren(shell);
  };

  // Register ALL 4 MCP App lifecycle handlers BEFORE await app.connect().
  app.ontoolinput = (params) => {
    const args = params?.arguments;
    if (isRecord(args)) {
      if (typeof args.conversationId === "string" && args.conversationId.trim()) {
        state.activeConversationId = args.conversationId.trim();
      }
      if (typeof args.limit === "number" && args.limit > 0) {
        state.overviewLimit = Math.floor(args.limit);
      }
    }
    render();
  };

  app.ontoolresult = (result) => {
    state.hasInitialToolResult = true;
    clearInitialFallbackTimer();
    state.loading = false;

    if (result?.isError) {
      state.banner = {
        kind: "error",
        text: extractToolErrorText(result, "Tool execution failed"),
      };
      render();
      return;
    }

    if (result?.structuredContent !== undefined) {
      applyStructuredResult(result.structuredContent, { switchView: true });
    }
    render();
    if (state.currentView === "status") {
      prefetchOverviewOnce();
    }
  };

  app.ontoolcancelled = (params) => {
    state.loading = false;
    state.banner = {
      kind: "info",
      text: params?.reason
        ? `Tool cancelled: ${params.reason}`
        : "Tool execution cancelled.",
    };
    render();
  };

  app.onhostcontextchanged = (ctx) => {
    applyHostContext(ctx);
    render();
  };

  render();

  try {
    await app.connect();
    state.connected = true;
    state.hostCapabilities = app.getHostCapabilities();
    applyHostContext(app.getHostContext());
    if (state.autoRefresh && state.hostCapabilities?.serverTools && !disposed) {
      startAutoRefreshTimer();
    }
    render();
    if (state.hasInitialToolResult && state.currentView === "status") {
      prefetchOverviewOnce();
    }

    const fallbackDelay = deps.autoFetchDelayMs ?? 250;
    if (
      fallbackDelay > 0 &&
      !state.hasInitialToolResult &&
      state.hostCapabilities?.serverTools &&
      !disposed
    ) {
      initialFallbackTimer = setTimeout(() => {
        if (!disposed && !state.hasInitialToolResult) {
          void controller.refreshCurrentView(true);
        }
      }, fallbackDelay);
    }
  } catch (err) {
    state.banner = {
      kind: "error",
      text: extractToolErrorText(err, "Unable to connect to MCP App host"),
    };
    render();
  }

  return controller;
}

function bootstrapBrowserApp(): void {
  const app = new App(
    { name: "Context Guard", version: "1.0.0" },
    {},
    { autoResize: true },
  );
  void mountDashboard({ app });
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  bootstrapBrowserApp();
}
