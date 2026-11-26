import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_SRC_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(UI_SRC_DIR, "..");
const APP_HTML_PATH = path.join(REPO_ROOT, "ui", "app.html");
const MAIN_TS_PATH = path.join(UI_SRC_DIR, "src", "main.ts");

async function loadMainModule() {
  const result = await esbuild.build({
    entryPoints: [MAIN_TS_PATH],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: ["es2022"],
    write: false,
  });
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "cg-ui-test-"));
  const modPath = path.join(tmpDir, "main.mjs");
  fs.writeFileSync(modPath, result.outputFiles[0].text, "utf8");
  const mod = await import(pathToFileURL(modPath).href);
  fs.rmSync(tmpDir, { recursive: true, force: true });
  return mod;
}

function createFakeElement(tagName) {
  const listeners = new Map();
  const children = [];
  const attrs = new Map();
  const classSet = new Set();
  const el = {
    tagName: tagName.toUpperCase(),
    children,
    style: {},
    dataset: {},
    value: "",
    checked: false,
    disabled: false,
    title: "",
    id: "",
    type: "",
    readOnly: false,
    classList: {
      add: (...cls) => cls.forEach((c) => classSet.add(c)),
      remove: (...cls) => cls.forEach((c) => classSet.delete(c)),
      toggle: (c, force) => {
        if (force === undefined) {
          if (classSet.has(c)) classSet.delete(c);
          else classSet.add(c);
        } else if (force) {
          classSet.add(c);
        } else {
          classSet.delete(c);
        }
      },
      contains: (c) => classSet.has(c),
    },
    set className(val) {
      classSet.clear();
      String(val)
        .split(/\s+/)
        .filter(Boolean)
        .forEach((c) => classSet.add(c));
    },
    get className() {
      return Array.from(classSet).join(" ");
    },
    _text: "",
    set textContent(val) {
      el._text = String(val ?? "");
      children.length = 0;
    },
    get textContent() {
      if (children.length === 0) return el._text;
      return el._text + children.map((c) => c.textContent ?? "").join("");
    },
    set innerHTML(_val) {
      throw new Error("XSS guard violation: innerHTML must not be assigned");
    },
    get innerHTML() {
      return "";
    },
    setAttribute(k, v) {
      attrs.set(k, String(v));
      if (k === "class") el.className = String(v);
    },
    getAttribute(k) {
      return attrs.get(k) ?? null;
    },
    appendChild(child) {
      children.push(child);
      return child;
    },
    insertBefore(newNode, refNode) {
      const idx = refNode ? children.indexOf(refNode) : -1;
      if (idx === -1) children.push(newNode);
      else children.splice(idx, 0, newNode);
      return newNode;
    },
    append(...nodes) {
      for (const n of nodes) {
        if (typeof n === "string") {
          children.push(createFakeTextNode(n));
        } else {
          children.push(n);
        }
      }
    },
    replaceChildren(...nodes) {
      children.length = 0;
      el._text = "";
      el.append(...nodes);
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent(evt) {
      const list = listeners.get(evt.type) ?? [];
      for (const fn of list) fn(evt);
    },
    getBoundingClientRect() {
      return { width: 360, height: 240 };
    },
    select() {},
  };
  return el;
}

function createFakeTextNode(text) {
  return {
    nodeType: 3,
    textContent: String(text),
  };
}

function createFakeDocument() {
  const rootEl = createFakeElement("html");
  const headEl = createFakeElement("head");
  const bodyEl = createFakeElement("body");
  const appEl = createFakeElement("div");
  appEl.id = "app";
  bodyEl.appendChild(appEl);

  return {
    documentElement: rootEl,
    head: headEl,
    body: bodyEl,
    hidden: false,
    createElement: (tag) => createFakeElement(tag),
    createElementNS: (_ns, tag) => createFakeElement(tag),
    createTextNode: (text) => createFakeTextNode(text),
    getElementById: (id) => (id === "app" ? appEl : null),
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    execCommand: () => false,
    _appEl: appEl,
  };
}

function collectText(node) {
  if (!node) return "";
  if (node.nodeType === 3) return node.textContent || "";
  const own = node._text || "";
  const nested = (node.children || []).map(collectText).join(" ");
  return [own, nested].filter(Boolean).join(" ");
}

function collectTags(node, out = []) {
  if (!node || !node.tagName) return out;
  out.push(node.tagName);
  for (const child of node.children || []) {
    collectTags(child, out);
  }
  return out;
}

test("built ui/app.html meets single-file CSP and size constraints", () => {
  assert.equal(fs.existsSync(APP_HTML_PATH), true, "ui/app.html must exist");
  const html = fs.readFileSync(APP_HTML_PATH, "utf8");
  const byteSize = Buffer.byteLength(html, "utf8");
  assert.match(html, /^<!doctype html>/i, "must start with <!doctype html>");
  assert.ok(byteSize < 512 * 1024, `expected < 512 KiB, got ${byteSize} bytes`);
  assert.equal(/<script[^>]*\bsrc\s*=/i.test(html), false, "must not contain <script src=...>");
  assert.equal(/<link\b/i.test(html), false, "must not contain <link ...>");
});

test("formatters, sparkline geometry, and payload guards handle edge cases", async () => {
  const mod = await loadMainModule();
  assert.equal(mod.formatTokens(0), "0");
  assert.equal(mod.formatTokens(850), "850");
  assert.equal(mod.formatTokens(300000), "300k");
  assert.equal(mod.formatTokens(265400), "265.4k");
  assert.equal(mod.formatTokens(1048576), "1.05M");

  assert.equal(mod.clampPercent(-10), 0);
  assert.equal(mod.clampPercent(42.5), 42.5);
  assert.equal(mod.clampPercent(180), 100);
  assert.equal(mod.clampPercent(Number.NaN), 0);

  const spark = mod.buildSparklineModel(
    [
      { stepIndex: 1, totalTokens: 50000, deltaTokens: 50000 },
      { stepIndex: 3, totalTokens: 150000, deltaTokens: 100000 },
      { stepIndex: 5, totalTokens: 280000, deltaTokens: 130000 },
    ],
    300000,
    320,
    96,
  );
  assert.equal(spark.points.length, 3);
  assert.ok(spark.thresholdY >= 0 && spark.thresholdY <= 96);
  assert.ok(spark.linePath.startsWith("M "));
  assert.ok(spark.areaPath.includes("Z"));

  const emptySpark = mod.buildSparklineModel([], 300000, 320, 96);
  assert.equal(emptySpark.points.length, 0);

  assert.equal(mod.isStatusPayload({ view: "status", effectiveTokens: 10 }), true);
  assert.equal(mod.isStatusPayload({ view: "overview" }), false);
  assert.equal(mod.isOverviewPayload({ view: "overview", conversations: [] }), true);
  assert.equal(mod.isHandoffPayload({ brief: "# Handoff" }), true);
});

test("server tool mutex serializes calls and caps concurrent in-flight calls at 1", async () => {
  const mod = await loadMainModule();
  let active = 0;
  let maxSeen = 0;
  const order = [];

  const runner = mod.createToolMutex(async (label, shouldFail = false) => {
    active += 1;
    maxSeen = Math.max(maxSeen, active);
    await new Promise((r) => setTimeout(r, 12));
    order.push(label);
    active -= 1;
    if (shouldFail) throw new Error("boom");
    return label;
  });

  const p1 = runner.run(() => ["first", false]);
  const p2 = runner.run(() => ["second", true]);
  const p3 = runner.run(() => ["third", false]);

  const results = await Promise.allSettled([p1, p2, p3]);
  assert.equal(maxSeen, 1, "mutex must keep concurrent calls <= 1 (well under host cap of 4)");
  assert.deepEqual(order, ["first", "second", "third"]);
  assert.equal(results[0].status, "fulfilled");
  assert.equal(results[1].status, "rejected");
  assert.equal(results[2].status, "fulfilled");
  assert.equal(runner.isBusy(), false);
});

test("controller registers all 4 handlers before connect() and escapes untrusted transcript XSS", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const callOrder = [];
  const serverToolCalls = [];
  const sentMessages = [];
  const modelContextUpdates = [];

  const xssModel = '<img src=x onerror="alert(1)">gemini';
  const xssConv = 'conv-<script>alert("xss")</script>';
  const xssSnippet = '<svg onload=alert(2)>heavy output & "quotes"';
  const xssTool = '<b onclick=x()>run_command</b>';
  const xssSource = '<iframe src=evil>Antigravity</iframe>';

  const sampleStatus = {
    view: "status",
    conversationId: xssConv,
    status: "warn",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    model: xssModel,
    effectiveTokens: 250000,
    inputTokens: 100000,
    cacheReadTokens: 140000,
    outputTokens: 10000,
    pendingDeltaTokens: 0,
    cacheHitPercent: 58.3,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 1048576,
    thresholdPercent: 83.3,
    windowPercent: 23.8,
    remainingToThreshold: 50000,
    avgDeltaPerTurn: 25000,
    estimatedTurnsRemaining: 2,
    compactionCount: 1,
    history: [
      { stepIndex: 1, totalTokens: 120000, deltaTokens: 120000 },
      { stepIndex: 3, totalTokens: 250000, deltaTokens: 130000 },
    ],
    bloatByCategory: {
      user: 5000,
      model: 40000,
      toolOutputs: 195000,
      system: 10000,
    },
    topBloatSteps: [
      {
        stepIndex: 2,
        source: "SYSTEM",
        type: "GENERIC",
        category: "toolOutputs",
        tools: [xssTool],
        estimatedTokens: 120000,
        snippet: xssSnippet,
      },
    ],
  };

  const fakeApp = {
    _handlers: {},
    set ontoolinput(fn) {
      callOrder.push("ontoolinput");
      this._handlers.ontoolinput = fn;
    },
    get ontoolinput() {
      return this._handlers.ontoolinput;
    },
    set ontoolresult(fn) {
      callOrder.push("ontoolresult");
      this._handlers.ontoolresult = fn;
    },
    get ontoolresult() {
      return this._handlers.ontoolresult;
    },
    set ontoolcancelled(fn) {
      callOrder.push("ontoolcancelled");
      this._handlers.ontoolcancelled = fn;
    },
    get ontoolcancelled() {
      return this._handlers.ontoolcancelled;
    },
    set onhostcontextchanged(fn) {
      callOrder.push("onhostcontextchanged");
      this._handlers.onhostcontextchanged = fn;
    },
    get onhostcontextchanged() {
      return this._handlers.onhostcontextchanged;
    },
    async connect() {
      callOrder.push("connect");
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    getHostCapabilities() {
      return {
        serverTools: { listChanged: false },
        message: { text: {} },
        updateModelContext: { text: {} },
      };
    },
    async callServerTool(params) {
      serverToolCalls.push(params);
      if (params.name === "app_build_handoff") {
        return {
          content: [{ type: "text", text: "# Handoff" }],
          structuredContent: {
            conversationId: xssConv,
            effectiveTokens: 250000,
            thresholdTokens: 300000,
            brief: "# Session Continuation Handoff\nTest brief",
          },
        };
      }
      if (params.name === "app_refresh" && params.arguments?.view === "overview") {
        return {
          content: [{ type: "text", text: "Overview" }],
          structuredContent: {
            view: "overview",
            enabled: true,
            mode: "stop",
            thresholdTokens: 300000,
            warnPercent: 80,
            conversations: [
              {
                conversationId: xssConv,
                sourceApp: xssSource,
                model: xssModel,
                effectiveTokens: 250000,
                thresholdTokens: 300000,
                thresholdPercent: 83.3,
                status: "warn",
                compactionCount: 1,
                mtime: 1700000000,
                transcriptPath: "/tmp/t.jsonl",
              },
            ],
            active: null,
          },
        };
      }
      return {
        content: [{ type: "text", text: "Status" }],
        structuredContent: sampleStatus,
      };
    },
    async sendMessage(params) {
      sentMessages.push(params);
      return {};
    },
    async updateModelContext(params) {
      modelContextUpdates.push(params);
      return {};
    },
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
    autoFetchDelayMs: 0,
  });

  // Handlers must all be registered BEFORE connect()
  const connectIdx = callOrder.indexOf("connect");
  assert.ok(connectIdx >= 4, `expected connect after 4 handlers, got ${callOrder.join(",")}`);
  for (const h of ["ontoolinput", "ontoolresult", "ontoolcancelled", "onhostcontextchanged"]) {
    const idx = callOrder.indexOf(h);
    assert.ok(idx !== -1 && idx < connectIdx, `${h} must be registered before connect()`);
  }

  // Deliver tool result and verify rendered text has escaped literal strings & zero injected HTML tags
  fakeApp._handlers.ontoolresult({
    content: [{ type: "text", text: "ok" }],
    structuredContent: sampleStatus,
  });

  const tags = collectTags(fakeDoc._appEl);
  assert.equal(tags.includes("IMG"), false, "XSS <img> must never be created in DOM");
  assert.equal(tags.includes("SCRIPT"), false, "XSS <script> must never be created in DOM");
  assert.equal(tags.includes("IFRAME"), false, "XSS <iframe> must never be created in DOM");

  const renderedText = collectText(fakeDoc._appEl);
  assert.ok(renderedText.includes(xssModel));
  assert.ok(renderedText.includes(xssSnippet));
  assert.ok(renderedText.includes(xssTool));

  // Verify Smart Handoff calls app_build_handoff and sendMessage with Antigravity target: "new"
  await controller.triggerHandoff();
  assert.equal(serverToolCalls.some((c) => c.name === "app_build_handoff"), true);
  assert.equal(sentMessages.length, 1);
  assert.deepEqual(sentMessages[0]._meta, {
    "com.google.antigravity/message": { target: "new" },
  });

  // Verify Attach Summary calls updateModelContext
  await controller.triggerAttachSummary();
  assert.equal(modelContextUpdates.length, 1);

  // Verify Overview renders sourceApp safely without tag injection
  await controller.switchToOverview();
  const overviewTags = collectTags(fakeDoc._appEl);
  assert.equal(overviewTags.includes("IFRAME"), false);
  assert.ok(collectText(fakeDoc._appEl).includes(xssSource));

  controller.dispose();
});

function collectButtons(node, out = []) {
  if (!node) return out;
  if (node.tagName === "BUTTON") out.push(node);
  for (const child of node.children || []) {
    collectButtons(child, out);
  }
  return out;
}

test("capability gating, snooze, settings validation, toolcancelled, and hostcontextchanged", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const serverToolCalls = [];
  const appliedThemes = [];
  const appliedStyles = [];
  const appliedFonts = [];
  let sentCount = 0;

  const baseStatus = {
    view: "status",
    conversationId: "conv-cap",
    status: "limit",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    model: "claude-sonnet",
    effectiveTokens: 310000,
    inputTokens: 120000,
    cacheReadTokens: 180000,
    outputTokens: 10000,
    pendingDeltaTokens: 0,
    cacheHitPercent: 60.0,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 200000,
    thresholdPercent: 103.3,
    windowPercent: 155.0,
    remainingToThreshold: 0,
    avgDeltaPerTurn: 20000,
    estimatedTurnsRemaining: 0,
    compactionCount: 0,
    history: [{ stepIndex: 1, totalTokens: 310000, deltaTokens: 310000 }],
    bloatByCategory: { user: 2000, model: 8000, toolOutputs: 300000, system: 0 },
    topBloatSteps: [],
  };

  const fakeApp = {
    _handlers: {},
    set ontoolinput(fn) { this._handlers.ontoolinput = fn; },
    get ontoolinput() { return this._handlers.ontoolinput; },
    set ontoolresult(fn) { this._handlers.ontoolresult = fn; },
    get ontoolresult() { return this._handlers.ontoolresult; },
    set ontoolcancelled(fn) { this._handlers.ontoolcancelled = fn; },
    get ontoolcancelled() { return this._handlers.ontoolcancelled; },
    set onhostcontextchanged(fn) { this._handlers.onhostcontextchanged = fn; },
    get onhostcontextchanged() { return this._handlers.onhostcontextchanged; },
    async connect() {},
    getHostContext() {
      return { theme: "light", styles: { variables: { "--color-background-primary": "#ffffff" } } };
    },
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    async callServerTool(params) {
      serverToolCalls.push(params);
      if (params.name === "app_build_handoff") {
        return {
          content: [{ type: "text", text: "# Handoff" }],
          structuredContent: {
            conversationId: "conv-cap",
            effectiveTokens: 310000,
            thresholdTokens: 300000,
            brief: "# Handoff Brief",
          },
        };
      }
      return {
        content: [{ type: "text", text: "ok" }],
        structuredContent: {
          ...baseStatus,
          snoozedUntilTokens: params.name === "app_snooze" && !params.arguments?.resetSnooze ? 360000 : 0,
        },
      };
    },
    async sendMessage() {
      sentCount += 1;
      return {};
    },
    async updateModelContext() {
      throw new Error("should not be called when updateModelContext capability is absent");
    },
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    applyTheme: (t) => appliedThemes.push(t),
    applyStyles: (s) => appliedStyles.push(s),
    applyFonts: (f) => appliedFonts.push(f),
    autoFetchDelayMs: 0,
  });

  fakeApp._handlers.ontoolresult({ content: [], structuredContent: baseStatus });

  const buttons = collectButtons(fakeDoc._appEl);
  const compactBtn = buttons.find((b) => b.textContent === "Copy /compact");
  assert.ok(compactBtn, "Copy /compact button should render");
  assert.equal(compactBtn.disabled, false, "Copy /compact stays enabled without caps.message");
  await controller.triggerCompact();
  assert.equal(sentCount, 0, "Copy /compact must not send /compact via sendMessage");
  assert.ok(collectText(fakeDoc._appEl).includes("chat box"));

  const attachBtn = buttons.find((b) => b.textContent === "Attach Summary");
  assert.equal(attachBtn, undefined, "Attach Summary must not render without caps.updateModelContext");

  // Smart Handoff without caps.message builds brief without calling sendMessage
  await controller.triggerHandoff("Need clean context");
  assert.equal(sentCount, 0);
  const handoffCall = serverToolCalls.find((c) => c.name === "app_build_handoff");
  assert.deepEqual(handoffCall?.arguments, {
    conversationId: "conv-cap",
    note: "Need clean context",
  });

  // Snooze +50k and reset snooze
  await controller.triggerSnooze(50_000);
  assert.deepEqual(serverToolCalls.at(-1), {
    name: "app_snooze",
    arguments: { conversationId: "conv-cap", deltaTokens: 50_000 },
  });
  await controller.triggerResetSnooze();
  assert.deepEqual(serverToolCalls.at(-1), {
    name: "app_snooze",
    arguments: { conversationId: "conv-cap", resetSnooze: true },
  });

  // Invalid settings must NOT invoke app_save_settings
  const callsBeforeInvalid = serverToolCalls.length;
  await controller.saveSettings({
    enabled: true,
    thresholdTokens: 500,
    warnPercent: 80,
    mode: "stop",
  });
  assert.equal(serverToolCalls.length, callsBeforeInvalid);
  assert.ok(collectText(fakeDoc._appEl).includes("at least 1,000"));

  // Valid settings call app_save_settings
  await controller.saveSettings({
    enabled: false,
    thresholdTokens: 250_000,
    warnPercent: 75,
    mode: "gate",
  });
  assert.deepEqual(serverToolCalls.at(-1), {
    name: "app_save_settings",
    arguments: {
      enabled: false,
      thresholdTokens: 250_000,
      warnPercent: 75,
      mode: "gate",
      conversationId: "conv-cap",
    },
  });

  // onhostcontextchanged updates theme, styles, and fonts
  fakeApp._handlers.onhostcontextchanged({
    theme: "dark",
    styles: {
      variables: { "--color-background-primary": "#111111" },
      css: { fonts: "body { font-family: sans-serif; }" },
    },
  });
  assert.deepEqual(appliedThemes, ["light", "dark"]);
  assert.equal(appliedStyles.length, 2);
  assert.equal(appliedFonts.length, 1);

  // ontoolcancelled renders reason cleanly
  fakeApp._handlers.ontoolcancelled({ reason: "user aborted" });
  assert.ok(collectText(fakeDoc._appEl).includes("Tool cancelled: user aborted"));

  controller.dispose();
});

test("compiled ui/app.html boots bundled ext-apps App and completes MCP ui/initialize handshake", async () => {
  const html = fs.readFileSync(APP_HTML_PATH, "utf8");
  const scriptMatch = html.match(/<script>([\s\S]*)<\/script>/i);
  assert.ok(scriptMatch, "ui/app.html must contain inline <script>");

  const fakeDoc = createFakeDocument();
  const windowListeners = new Map();
  const postedToHost = [];
  let handshakeResolved = false;

  const parentTarget = {
    postMessage(msg) {
      postedToHost.push(msg);
      if (msg && msg.method === "ui/initialize") {
        queueMicrotask(() => {
          const reply = {
            jsonrpc: "2.0",
            id: msg.id,
            result: {
              protocolVersion: "2026-01-26",
              hostInfo: { name: "test-host", version: "1.0.0" },
              hostCapabilities: { serverTools: { listChanged: false }, message: { text: {} } },
              hostContext: {
                theme: "dark",
                toolInfo: {
                  tool: {
                    name: "context_status",
                    inputSchema: { type: "object" },
                  },
                },
              },
            },
          };
          for (const fn of windowListeners.get("message") || []) {
            fn({ data: reply, source: parentTarget });
          }
        });
      } else if (msg && msg.method === "ui/notifications/initialized") {
        handshakeResolved = true;
        queueMicrotask(() => {
          const notif = {
            jsonrpc: "2.0",
            method: "ui/notifications/tool-result",
            params: {
              content: [{ type: "text", text: "ok" }],
              structuredContent: {
                view: "status",
                conversationId: "conv-live",
                status: "ok",
                enabled: true,
                mode: "stop",
                warnPercent: 80,
                model: "gemini-4-hydrogen-high",
                effectiveTokens: 42000,
                inputTokens: 12000,
                cacheReadTokens: 28000,
                outputTokens: 2000,
                pendingDeltaTokens: 0,
                cacheHitPercent: 70.0,
                effectiveThresholdTokens: 300000,
                warnThresholdTokens: 240000,
                baseThresholdTokens: 300000,
                snoozedUntilTokens: 0,
                modelWindowTokens: 1048576,
                thresholdPercent: 14.0,
                windowPercent: 4.0,
                remainingToThreshold: 258000,
                avgDeltaPerTurn: 10000,
                estimatedTurnsRemaining: 25,
                compactionCount: 0,
                history: [{ stepIndex: 1, totalTokens: 42000, deltaTokens: 42000 }],
                bloatByCategory: { user: 1000, model: 5000, toolOutputs: 36000, system: 0 },
                topBloatSteps: [],
              },
            },
          };
          for (const fn of windowListeners.get("message") || []) {
            fn({ data: notif, source: parentTarget });
          }
        });
      }
    },
  };

  const sandboxWindow = {
    parent: parentTarget,
    innerWidth: 360,
    addEventListener(type, fn) {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = windowListeners.get(type) || [];
      const idx = arr.indexOf(fn);
      if (idx !== -1) arr.splice(idx, 1);
    },
  };

  const vmTimers = new Set();
  const vmIntervals = new Set();
  const context = vm.createContext({
    window: sandboxWindow,
    document: fakeDoc,
    console: { log: () => {}, warn: () => {}, debug: () => {}, error: console.error },
    setTimeout: (cb, ms, ...args) => {
      const id = setTimeout(cb, ms, ...args);
      vmTimers.add(id);
      return id;
    },
    clearTimeout: (id) => {
      vmTimers.delete(id);
      clearTimeout(id);
    },
    setInterval: (cb, ms, ...args) => {
      const id = setInterval(cb, ms, ...args);
      vmIntervals.add(id);
      return id;
    },
    clearInterval: (id) => {
      vmIntervals.delete(id);
      clearInterval(id);
    },
    requestAnimationFrame: (cb) => {
      const id = setTimeout(cb, 0);
      vmTimers.add(id);
      return id;
    },
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    TextEncoder,
    TextDecoder,
    URL,
    AbortController,
    AbortSignal,
  });

  try {
    vm.runInContext(scriptMatch[1], context, { filename: "ui/app.html" });
    await new Promise((r) => setTimeout(r, 80));

    assert.equal(handshakeResolved, true, "bundled App must complete ui/initialize handshake");
    assert.equal(fakeDoc.documentElement.getAttribute("data-theme"), "dark");
    const rendered = collectText(fakeDoc._appEl);
    assert.ok(rendered.includes("42k tokens"), `expected rendered tokens, got: ${rendered}`);
    assert.ok(rendered.includes("gemini-4-hydrogen-high"));
  } finally {
    for (const id of vmTimers) clearTimeout(id);
    for (const id of vmIntervals) clearInterval(id);
  }
});

test("overview renders conversation titles and toggles subagent filter", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const mockApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: {} };
    },
    getHostContext() {
      return { theme: "dark" };
    },
    async callServerTool() {
      return { content: [], structuredContent: {} };
    },
    async sendMessage() {
      return { isError: false };
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: mockApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  mockApp.ontoolresult({
    structuredContent: {
      view: "overview",
      enabled: true,
      mode: "stop",
      thresholdTokens: 300000,
      warnPercent: 80,
      conversations: [
        {
          conversationId: "fee5bb10-fea1-4289-9552-dda76eb615c6",
          title: "Context Guard Custom Plugin Development",
          agentName: "",
          parentConversationId: "",
          isSubagent: false,
          sourceApp: "antigravity",
          model: "gemini-4-hydrogen-high",
          effectiveTokens: 276100,
          thresholdTokens: 300000,
          thresholdPercent: 92.0,
          status: "warn",
          compactionCount: 5,
          mtime: 100,
          transcriptPath: "/tmp/t1.jsonl",
        },
        {
          conversationId: "1db71075-5c2a-466b-b56b-201285d1bfc6",
          title: "Perform Final Whole-Plugin Review",
          agentName: "context-guard-reviewer",
          parentConversationId: "fee5bb10-fea1-4289-9552-dda76eb615c6",
          isSubagent: true,
          sourceApp: "antigravity",
          model: "gemini-4-hydrogen-tiered-xhigh",
          effectiveTokens: 183900,
          thresholdTokens: 300000,
          thresholdPercent: 61.3,
          status: "ok",
          compactionCount: 2,
          mtime: 99,
          transcriptPath: "/tmp/t2.jsonl",
        },
      ],
      active: null,
    },
  });

  let text = collectText(fakeDoc._appEl);
  assert.ok(text.includes("Context Guard Custom Plugin Development"), text);
  assert.ok(text.includes("Show Subagents (1)"), text);
  assert.equal(text.includes("Perform Final Whole-Plugin Review"), false);

  const findBtn = (node, label) => {
    if (!node) return null;
    if (node.tagName === "BUTTON" && (node._text || "").includes(label)) return node;
    for (const c of node.children || []) {
      const hit = findBtn(c, label);
      if (hit) return hit;
    }
    return null;
  };

  const toggleBtn = findBtn(fakeDoc._appEl, "Show Subagents (1)");
  assert.ok(toggleBtn);
  toggleBtn.dispatchEvent({ type: "click" });

  text = collectText(fakeDoc._appEl);
  assert.ok(text.includes("Perform Final Whole-Plugin Review"), text);
  assert.ok(text.includes("context-guard-reviewer"), text);
  assert.ok(text.includes("Hide Subagents"), text);

  controller.dispose();
});

test("auto-refresh starts active by default, polls on interval, toggles off/on, and disposes", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  let refreshCount = 0;

  const sampleStatus = {
    view: "status",
    conversationId: "conv-auto",
    status: "ok",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    model: "gemini-4-hydrogen-high",
    effectiveTokens: 42000,
    inputTokens: 12000,
    cacheReadTokens: 28000,
    outputTokens: 2000,
    pendingDeltaTokens: 0,
    cacheHitPercent: 70.0,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 1048576,
    thresholdPercent: 14.0,
    windowPercent: 4.0,
    remainingToThreshold: 258000,
    avgDeltaPerTurn: 10000,
    estimatedTurnsRemaining: 25,
    compactionCount: 0,
    history: [{ stepIndex: 1, totalTokens: 42000, deltaTokens: 42000 }],
    bloatByCategory: { user: 1000, model: 5000, toolOutputs: 36000, system: 0 },
    topBloatSteps: [],
  };

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      if (params.name === "app_refresh" && params.arguments?.view === "status") {
        refreshCount += 1;
        return {
          content: [{ type: "text", text: "Status" }],
          structuredContent: {
            ...sampleStatus,
            effectiveTokens: 42000 + refreshCount * 1000,
          },
        };
      }
      return { content: [], structuredContent: {} };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefreshIntervalMs: 20,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  fakeApp.ontoolresult({ content: [], structuredContent: sampleStatus });
  const initialAutoBtn = collectButtons(fakeDoc._appEl).find((b) =>
    (b.textContent || "").includes("Auto 5s"),
  );
  assert.ok(initialAutoBtn);
  assert.ok(
    initialAutoBtn.className.includes("cg-btn-active"),
    "Auto 5s must be active by default",
  );

  await new Promise((r) => setTimeout(r, 75));
  assert.ok(refreshCount >= 2, `expected interval auto-refreshes, got ${refreshCount}`);

  // Pause auto-refresh
  const activeBtn = collectButtons(fakeDoc._appEl).find((b) =>
    (b.textContent || "").includes("Auto 5s"),
  );
  activeBtn.dispatchEvent({ type: "click" });
  const pausedCount = refreshCount;
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(refreshCount, pausedCount, "paused Auto 5s must not poll");

  // Resume triggers immediate refresh + resumes polling
  const pausedBtn = collectButtons(fakeDoc._appEl).find((b) =>
    (b.textContent || "").includes("Auto 5s"),
  );
  pausedBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(refreshCount > pausedCount, "enabling Auto 5s triggers immediate refresh");

  controller.dispose();
});

test("auto-refresh interval setting validates bounds, persists via app_save_settings, and updates header label", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const savedCalls = [];

  const statusPayload = {
    view: "status",
    conversationId: "conv-interval",
    status: "ok",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    autoRefreshSeconds: 5,
    model: "gemini-4-hydrogen-high",
    effectiveTokens: 42000,
    inputTokens: 12000,
    cacheReadTokens: 28000,
    outputTokens: 2000,
    pendingDeltaTokens: 0,
    cacheHitPercent: 70.0,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 1048576,
    thresholdPercent: 14.0,
    windowPercent: 4.0,
    remainingToThreshold: 258000,
    avgDeltaPerTurn: 10000,
    estimatedTurnsRemaining: 25,
    compactionCount: 0,
    history: [{ stepIndex: 1, totalTokens: 42000, deltaTokens: 42000 }],
    bloatByCategory: { user: 1000, model: 5000, toolOutputs: 36000, system: 0 },
    topBloatSteps: [],
  };

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      if (params.name === "app_save_settings") {
        savedCalls.push(params.arguments);
        return {
          content: [{ type: "text", text: "Saved" }],
          structuredContent: {
            ...statusPayload,
            autoRefreshSeconds: params.arguments.autoRefreshSeconds ?? 5,
          },
        };
      }
      return { content: [], structuredContent: statusPayload };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  fakeApp.ontoolresult({ content: [], structuredContent: statusPayload });

  // Open Settings drawer and inspect Auto-Refresh Interval presets + custom input
  const settingsBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Settings");
  assert.ok(settingsBtn);
  settingsBtn.dispatchEvent({ type: "click" });

  const preset10s = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "10s");
  const preset2s = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "2s");
  assert.ok(preset2s && preset10s, "Auto-refresh preset buttons (2s, 10s, ...) must render in Settings");

  // Click 2s preset then Save Settings
  preset2s.dispatchEvent({ type: "click" });
  const saveBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Save Settings");
  assert.ok(saveBtn);
  saveBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(savedCalls.length, 1);
  assert.equal(savedCalls[0].autoRefreshSeconds, 2);
  const updatedAutoBtn = collectButtons(fakeDoc._appEl).find((b) =>
    (b.textContent || "").includes("Auto 2s"),
  );
  assert.ok(updatedAutoBtn, "Header auto-refresh button must update to Auto 2s");

  // Invalid autoRefreshSeconds is rejected before calling server tool
  await controller.saveSettings({
    enabled: true,
    thresholdTokens: 300000,
    warnPercent: 80,
    mode: "stop",
    autoRefreshSeconds: 0,
  });
  assert.equal(savedCalls.length, 1);
  assert.ok(collectText(fakeDoc._appEl).includes("between 1 and 300 seconds"));

  controller.dispose();
});

test("model quotas card renders 5h and weekly limits, countdowns, and triggers non-blocking refresh", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const calls = [];

  const nowMs = Date.now();
  const reset5h = new Date(nowMs + 4 * 3600 * 1000 + 11 * 60 * 1000).toISOString();
  const resetWeekly = new Date(nowMs + 6 * 86400 * 1000 + 23 * 3600 * 1000).toISOString();
  assert.equal(mod.formatQuotaReset(reset5h, 62, nowMs), "Resets in 4h 11m");
  assert.equal(mod.formatQuotaReset(resetWeekly, 89, nowMs), "Resets in 6d 23h");
  assert.equal(mod.formatQuotaReset(reset5h, 100, nowMs), "");

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      calls.push(params);
      if (params.name === "app_quota") {
        return {
          content: [{ type: "text", text: "Quota updated" }],
          structuredContent: {
            view: "quota",
            modelQuota: {
              available: true,
              status: "ok",
              fetchedAt: Math.floor(nowMs / 1000),
              ageSeconds: 0,
              fetching: false,
              groups: [
                {
                  name: "Gemini Models",
                  modelsSummary: "Gemini 4 Hydrogen, Gemini 3.8 Flash",
                  buckets: [
                    {
                      id: "gemini-5h",
                      window: "5h",
                      label: "5h limit",
                      remainingFraction: 0.61,
                      remainingPercent: 61,
                      resetTime: reset5h,
                    },
                  ],
                },
              ],
            },
          },
        };
      }
      return { content: [], structuredContent: {} };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefresh: false,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  fakeApp.ontoolresult({
    content: [],
    structuredContent: {
      view: "status",
      conversationId: "conv-quota",
      status: "ok",
      enabled: true,
      mode: "stop",
      warnPercent: 80,
      model: "gemini-4-hydrogen-high",
      effectiveTokens: 42000,
      inputTokens: 12000,
      cacheReadTokens: 28000,
      outputTokens: 2000,
      pendingDeltaTokens: 0,
      cacheHitPercent: 70.0,
      effectiveThresholdTokens: 300000,
      warnThresholdTokens: 240000,
      baseThresholdTokens: 300000,
      snoozedUntilTokens: 0,
      modelWindowTokens: 1048576,
      thresholdPercent: 14.0,
      windowPercent: 4.0,
      remainingToThreshold: 258000,
      avgDeltaPerTurn: 10000,
      estimatedTurnsRemaining: 25,
      compactionCount: 0,
      history: [{ stepIndex: 1, totalTokens: 42000, deltaTokens: 42000 }],
      bloatByCategory: { user: 1000, model: 5000, toolOutputs: 36000, system: 0 },
      topBloatSteps: [],
      modelQuota: {
        available: true,
        status: "ok",
        fetchedAt: Math.floor(nowMs / 1000) - 90,
        ageSeconds: 90,
        fetching: false,
        groups: [
          {
            name: "Gemini Models",
            modelsSummary: "Gemini 4 Hydrogen, Gemini 3.8 Flash",
            buckets: [
              {
                id: "gemini-5h",
                window: "5h",
                label: "5h limit",
                remainingFraction: 0.62,
                remainingPercent: 62,
                resetTime: reset5h,
              },
              {
                id: "gemini-weekly",
                window: "weekly",
                label: "Weekly limit",
                remainingFraction: 0.89,
                remainingPercent: 89,
                resetTime: resetWeekly,
              },
            ],
          },
          {
            name: "Claude and GPT models",
            modelsSummary: "Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS 120B",
            buckets: [
              {
                id: "3p-5h",
                window: "5h",
                label: "5h limit",
                remainingFraction: 1,
                remainingPercent: 100,
                resetTime: reset5h,
              },
            ],
          },
        ],
      },
    },
  });

  const text = collectText(fakeDoc._appEl);
  assert.ok(text.includes("Model Quotas"));
  assert.ok(text.includes("Gemini Models"));
  assert.ok(text.includes("62% left"));
  assert.ok(text.includes("89% left"));
  assert.ok(text.includes("100% left"));
  assert.ok(text.includes("Claude and GPT models"));

  const refreshQuotaBtn = collectButtons(fakeDoc._appEl).find(
    (b) => b.textContent === "Refresh Limits",
  );
  assert.ok(refreshQuotaBtn);
  refreshQuotaBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));

  const quotaCall = calls.find((c) => c.name === "app_quota");
  assert.deepEqual(quotaCall, { name: "app_quota", arguments: { refresh: true } });
  assert.ok(collectText(fakeDoc._appEl).includes("61% left"));

  controller.dispose();
});

function collectElementsByTag(node, tagUpper, out = []) {
  if (!node || !node.tagName) return out;
  if (node.tagName === tagUpper) out.push(node);
  for (const child of node.children || []) {
    collectElementsByTag(child, tagUpper, out);
  }
  return out;
}

test("Monitor tab displays quota burn-rate prediction and dismissible Loop Guard banner", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const calls = [];
  const nowMs = Date.now();
  const reset5h = new Date(nowMs + 4 * 3600 * 1000).toISOString();

  let loopCleared = false;
  const baseStatus = {
    view: "status",
    conversationId: "conv-loop-ui",
    title: "Debug Test Loop",
    status: "ok",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    model: "gemini-4-hydrogen-high",
    effectiveTokens: 42000,
    inputTokens: 12000,
    cacheReadTokens: 28000,
    outputTokens: 2000,
    pendingDeltaTokens: 0,
    cacheHitPercent: 70.0,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 1048576,
    thresholdPercent: 14.0,
    windowPercent: 4.0,
    remainingToThreshold: 258000,
    avgDeltaPerTurn: 10000,
    estimatedTurnsRemaining: 25,
    compactionCount: 0,
    history: [{ stepIndex: 1, totalTokens: 42000, deltaTokens: 42000 }],
    bloatByCategory: { user: 1000, model: 5000, toolOutputs: 36000, system: 0 },
    topBloatSteps: [],
    activeLoop: {
      signature: "cmd:npm test",
      toolName: "run_command",
      count: 3,
      reason: "consecutive_failures",
      lastErrorSnippet: "exit status 1",
    },
    modelQuota: {
      available: true,
      status: "ok",
      fetchedAt: Math.floor(nowMs / 1000),
      ageSeconds: 5,
      fetching: false,
      groups: [
        {
          name: "Gemini Models",
          modelsSummary: "Gemini 4 Hydrogen",
          buckets: [
            {
              id: "gemini-5h",
              window: "5h",
              label: "5h limit",
              remainingFraction: 0.4,
              remainingPercent: 40,
              resetTime: reset5h,
              prediction: {
                state: "active",
                burnRatePerHour: 20.0,
                hoursUntilExhausted: 2.0,
                hoursUntilReset: 4.0,
                bufferHours: -2.0,
                riskLevel: "at_risk",
                sampleCount: 3,
                windowObservedSeconds: 3600,
                usedDeltaPct: 20.0,
              },
            },
          ],
        },
      ],
    },
  };

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      calls.push(params);
      if (params.name === "app_clear_loop") {
        loopCleared = true;
        return {
          content: [{ type: "text", text: "Cleared" }],
          structuredContent: { ...baseStatus, activeLoop: null },
        };
      }
      return { content: [], structuredContent: { ...baseStatus, activeLoop: loopCleared ? null : baseStatus.activeLoop } };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefresh: false,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  fakeApp.ontoolresult({ content: [], structuredContent: baseStatus });

  const text = collectText(fakeDoc._appEl);
  assert.ok(text.includes("Agy Lens"), "Header brand should display Agy Lens");
  assert.ok(text.includes("Loop Guard"), "Active loop banner should render");
  assert.ok(text.includes("run_command"), text);
  assert.ok(text.includes("20%/hr") || text.includes("20.0%/hr"), `Expected burn rate in: ${text}`);
  assert.ok(text.includes("before reset"), `Expected exhaustion warning in: ${text}`);

  const dismissBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Dismiss Loop");
  assert.ok(dismissBtn, "Dismiss Loop button must be rendered on Loop Guard banner");
  dismissBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));

  assert.ok(calls.some((c) => c.name === "app_clear_loop"));
  assert.equal(collectText(fakeDoc._appEl).includes("Dismiss Loop"), false);

  controller.dispose();
});

test("Search tab searches across chats, renders mark spans safely, and jumps to Monitor", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const calls = [];

  const searchPayload = {
    view: "search",
    query: "pkce",
    scope: "all",
    includeSubagents: false,
    totalIndexed: 4,
    matchedCount: 1,
    results: [
      {
        convId: "conv-oauth",
        title: "Implement OAuth2 PKCE Flow",
        updatedAt: "2026-11-26T06:00:00Z",
        sourceApp: "antigravity",
        isSubagent: false,
        matchCount: 2,
        editedFiles: ["src/auth/pkce.ts"],
        snippets: [
          {
            source: "user",
            label: "Prompt",
            text: "Add PKCE verifier challenge",
            spans: [[4, 8]],
          },
        ],
      },
    ],
  };

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      calls.push(params);
      if (params.name === "app_search") {
        return { content: [], structuredContent: { ...searchPayload, query: params.arguments?.query || "" } };
      }
      return {
        content: [],
        structuredContent: {
          view: "status",
          conversationId: params.arguments?.conversationId || "conv-oauth",
          status: "ok",
          enabled: true,
          mode: "stop",
          warnPercent: 80,
          model: "gemini-4-hydrogen-high",
          effectiveTokens: 15000,
          inputTokens: 14000,
          cacheReadTokens: 0,
          outputTokens: 1000,
          pendingDeltaTokens: 0,
          cacheHitPercent: 0,
          effectiveThresholdTokens: 300000,
          warnThresholdTokens: 240000,
          baseThresholdTokens: 300000,
          snoozedUntilTokens: 0,
          modelWindowTokens: 1048576,
          thresholdPercent: 5,
          windowPercent: 1.4,
          remainingToThreshold: 285000,
          avgDeltaPerTurn: 0,
          estimatedTurnsRemaining: null,
          compactionCount: 0,
          history: [],
          bloatByCategory: { user: 0, model: 0, toolOutputs: 0, system: 0 },
          topBloatSteps: [],
        },
      };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefresh: false,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  const searchTabBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Search");
  assert.ok(searchTabBtn, "Search tab button must exist");
  searchTabBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));

  assert.ok(calls.some((c) => c.name === "app_search"));
  const text = collectText(fakeDoc._appEl);
  assert.ok(text.includes("Implement OAuth2 PKCE Flow"));
  assert.ok(collectTags(fakeDoc._appEl).includes("MARK"), "Highlight spans must render as <mark> elements");

  const openBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Open in Monitor");
  assert.ok(openBtn);
  openBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));
  assert.ok(
    calls.some((c) => c.name === "app_refresh" && c.arguments?.conversationId === "conv-oauth"),
  );

  controller.dispose();
});

test("Setup tab renders startup footprint KPIs, warnings, and Settings Loop Guard + Alerts", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const calls = [];

  const footprintPayload = {
    view: "footprint",
    workspacePath: "/repo/app",
    summary: {
      estimatedStartupTokens: 2450,
      onDemandSkillsTokens: 5100,
      observedStartupTokens: 3200,
      rulesCount: 1,
      pluginsCount: 1,
      skillsCount: 1,
      builtinSkillsCount: 2,
      mcpServersCount: 1,
      hooksCount: 1,
    },
    builtinSkillsSummary: { count: 2, frontmatterTokens: 120, onDemandTokens: 1800 },
    warnings: [
      {
        code: "duplicate_plugin",
        severity: "warning",
        title: "Duplicate context plugin installed",
        detail: "Run agy plugin uninstall agy-context-guard",
        path: "/home/.gemini/config/plugins/agy-context-guard",
      },
    ],
    rules: [{ name: "GEMINI.md", scope: "workspace", path: "/repo/app/GEMINI.md", bytes: 400, estTokens: 100, alwaysLoaded: true }],
    plugins: [{ id: "agy-lens", name: "agy-lens", version: "2.0.0-beta.1", enabled: true, scope: "global", path: "/home/.gemini/config/plugins/agy-lens", hasMcp: true, hasHooks: true, skillsCount: 1, rulesCount: 0 }],
    skills: [{ name: "lens", description: "Agy Lens cockpit", scope: "plugin", source: "agy-lens", path: "/home/.gemini/config/plugins/agy-lens/skills/lens/SKILL.md", frontmatterTokens: 20, bodyTokens: 300, enabled: true }],
    mcpServers: [{ name: "monitor", scope: "plugin", source: "agy-lens", enabled: true, commandPreview: "python3 server/mcp_server.py", envKeys: ["AGY_LENS_DATA_DIR"], envKeyCount: 1, estTokens: 350, path: "/home/.gemini/config/plugins/agy-lens/mcp_config.json" }],
    hooks: [{ name: "agy-lens", scope: "plugin", enabled: true, events: ["PostToolUse", "Stop"], timeoutSeconds: 5, path: "/home/.gemini/config/plugins/agy-lens/hooks.json" }],
  };

  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities() {
      return { serverTools: { listChanged: false } };
    },
    getHostContext() {
      return { theme: "dark", toolInfo: { tool: { name: "context_status" } } };
    },
    async callServerTool(params) {
      calls.push(params);
      if (params.name === "app_footprint") {
        return { content: [], structuredContent: footprintPayload };
      }
      if (params.name === "app_test_alert") {
        return { content: [], structuredContent: { view: "alert_test", sent: true, reason: "ok" } };
      }
      return {
        content: [],
        structuredContent: {
          view: "status",
          conversationId: "conv-setup",
          status: "ok",
          enabled: true,
          mode: "stop",
          warnPercent: 80,
          model: "gemini-4-hydrogen-high",
          effectiveTokens: 20000,
          inputTokens: 18000,
          cacheReadTokens: 0,
          outputTokens: 2000,
          pendingDeltaTokens: 0,
          cacheHitPercent: 0,
          effectiveThresholdTokens: 300000,
          warnThresholdTokens: 240000,
          baseThresholdTokens: 300000,
          snoozedUntilTokens: 0,
          modelWindowTokens: 1048576,
          thresholdPercent: 6.7,
          windowPercent: 1.9,
          remainingToThreshold: 280000,
          avgDeltaPerTurn: 0,
          estimatedTurnsRemaining: null,
          compactionCount: 0,
          history: [],
          bloatByCategory: { user: 0, model: 0, toolOutputs: 0, system: 0 },
          topBloatSteps: [],
          loopGuard: { enabled: true, consecutiveThreshold: 3, nudgeOncePerStreak: true, autoPauseThreshold: 5 },
          alerts: { soundEnabled: true, desktopNotificationEnabled: true, notifyOnStop: true, notifyOnThreshold: true, notifyOnLoop: true, notifyOnLowQuotaPct: 15, cooldownSeconds: 15 },
        },
      };
    },
    async sendMessage() {
      return {};
    },
    async updateModelContext() {},
  };

  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefresh: false,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });

  const setupTabBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Setup");
  assert.ok(setupTabBtn, "Setup tab button must exist");
  setupTabBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));

  const setupText = collectText(fakeDoc._appEl);
  assert.ok(setupText.includes("Est. Startup"), setupText);
  assert.ok(setupText.includes("Duplicate context plugin installed"), setupText);
  assert.ok(setupText.includes("GEMINI.md"), setupText);
  assert.ok(setupText.includes("python3 server/mcp_server.py"), setupText);

  // Switch back to Monitor, open Settings, test alert and save loopGuard/alerts
  const settingsBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Settings");
  assert.ok(settingsBtn);
  settingsBtn.dispatchEvent({ type: "click" });

  const testAlertBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Test Sound & Alert");
  assert.ok(testAlertBtn, "Test Sound & Alert button must render in Settings");
  testAlertBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));
  assert.ok(calls.some((c) => c.name === "app_test_alert"));

  const saveBtn = collectButtons(fakeDoc._appEl).find((b) => b.textContent === "Save Settings");
  assert.ok(saveBtn);
  saveBtn.dispatchEvent({ type: "click" });
  await new Promise((r) => setTimeout(r, 15));

  const saveCall = calls.find((c) => c.name === "app_save_settings");
  assert.ok(saveCall);
  assert.ok(saveCall.arguments.loopGuard);
  assert.ok(saveCall.arguments.alerts);

  controller.dispose();
});

test("Turn-by-Turn Growth filters out pre-compaction turns from earlier epochs", async () => {
  const mod = await loadMainModule();
  const fakeDoc = createFakeDocument();
  const compactedStatus = {
    view: "status",
    conversationId: "conv-compact",
    status: "ok",
    enabled: true,
    mode: "stop",
    warnPercent: 80,
    model: "gemini-4-hydrogen-high",
    effectiveTokens: 8100,
    inputTokens: 0,
    cacheReadTokens: 0,
    outputTokens: 0,
    pendingDeltaTokens: 8100,
    cacheHitPercent: 0,
    effectiveThresholdTokens: 300000,
    warnThresholdTokens: 240000,
    baseThresholdTokens: 300000,
    snoozedUntilTokens: 0,
    modelWindowTokens: 1048576,
    thresholdPercent: 2.7,
    windowPercent: 0.8,
    remainingToThreshold: 291900,
    avgDeltaPerTurn: 0,
    estimatedTurnsRemaining: null,
    compactionCount: 4,
    activeEpochStep: 1204,
    history: [
      { stepIndex: 1202, inputTokens: 2000, cacheReadTokens: 398000, outputTokens: 1700, totalTokens: 401700, deltaTokens: 5000, model: "gemini-4-hydrogen-high", createdAt: "" },
    ],
    bloatByCategory: { user: 0, model: 0, toolOutputs: 0, system: 8100 },
    topBloatSteps: [],
  };
  const fakeApp = {
    ontoolinput: undefined,
    ontoolresult: undefined,
    ontoolcancelled: undefined,
    onhostcontextchanged: undefined,
    async connect() {},
    getHostCapabilities: () => ({ serverTools: { listChanged: true } }),
    getHostContext: () => ({ theme: "dark", toolInfo: { tool: { name: "context_status" } } }),
    callServerTool: async () => ({ content: [], structuredContent: compactedStatus }),
    sendMessage: async () => ({}),
    updateModelContext: async () => {},
  };
  const controller = await mod.mountDashboard({
    app: fakeApp,
    doc: fakeDoc,
    autoFetchDelayMs: 0,
    autoRefresh: false,
    applyTheme: () => {},
    applyStyles: () => {},
    applyFonts: () => {},
  });
  fakeApp.ontoolresult({ content: [], structuredContent: compactedStatus });

  const renderedText = collectText(fakeDoc._appEl);
  assert.ok(renderedText.includes("Since #1204 · 0 turns"), renderedText);
  assert.ok(renderedText.includes("Compacted at step #1204"), renderedText);
  assert.ok(!renderedText.includes("Peak: 401.7k"), renderedText);
  controller.dispose();
});
