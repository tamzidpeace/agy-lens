import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ENTRY_PATH = path.join(__dirname, "src", "main.ts");
const OUTPUT_PATH = path.resolve(__dirname, "..", "ui", "app.html");
const MAX_BUNDLE_BYTES = 512 * 1024;

const DASHBOARD_CSS = `
:root {
  --cg-bg-primary: #ffffff;
  --cg-bg-secondary: #f8fafc;
  --cg-bg-tertiary: #f1f5f9;
  --cg-bg-info: #eff6ff;
  --cg-bg-success: #ecfdf5;
  --cg-bg-warning: #fffbeb;
  --cg-bg-danger: #fef2f2;
  --cg-text-primary: #0f172a;
  --cg-text-secondary: #475569;
  --cg-text-tertiary: #64748b;
  --cg-text-info: #1d4ed8;
  --cg-text-success: #047857;
  --cg-text-warning: #b45309;
  --cg-text-danger: #b91c1c;
  --cg-border-primary: #e2e8f0;
  --cg-border-secondary: #cbd5e1;
  --cg-accent: #0ea5e9;
  --cg-accent-hover: #0284c7;
  --cg-accent-text: #ffffff;
  --cg-track: #e2e8f0;
  --cg-cat-user: #3b82f6;
  --cg-cat-model: #8b5cf6;
  --cg-cat-tools: #f59e0b;
  --cg-cat-system: #64748b;
  --cg-font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
  --cg-font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --cg-radius-sm: 6px;
  --cg-radius-md: 10px;
  --cg-radius-lg: 12px;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --cg-bg-primary: #0f172a;
    --cg-bg-secondary: #151e30;
    --cg-bg-tertiary: #1e293b;
    --cg-bg-info: rgba(56, 189, 248, 0.14);
    --cg-bg-success: rgba(16, 185, 129, 0.14);
    --cg-bg-warning: rgba(245, 158, 11, 0.15);
    --cg-bg-danger: rgba(239, 68, 68, 0.16);
    --cg-text-primary: #f1f5f9;
    --cg-text-secondary: #cbd5e1;
    --cg-text-tertiary: #94a3b8;
    --cg-text-info: #38bdf8;
    --cg-text-success: #34d399;
    --cg-text-warning: #fbbf24;
    --cg-text-danger: #f87171;
    --cg-border-primary: #263248;
    --cg-border-secondary: #334155;
    --cg-track: #263248;
  }
}

:root[data-theme="dark"] {
  --cg-bg-primary: #0f172a;
  --cg-bg-secondary: #151e30;
  --cg-bg-tertiary: #1e293b;
  --cg-bg-info: rgba(56, 189, 248, 0.14);
  --cg-bg-success: rgba(16, 185, 129, 0.14);
  --cg-bg-warning: rgba(245, 158, 11, 0.15);
  --cg-bg-danger: rgba(239, 68, 68, 0.16);
  --cg-text-primary: #f1f5f9;
  --cg-text-secondary: #cbd5e1;
  --cg-text-tertiary: #94a3b8;
  --cg-text-info: #38bdf8;
  --cg-text-success: #34d399;
  --cg-text-warning: #fbbf24;
  --cg-text-danger: #f87171;
  --cg-border-primary: #263248;
  --cg-border-secondary: #334155;
  --cg-track: #263248;
}

* {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  padding: 0;
  background: var(--color-background-primary, var(--cg-bg-primary));
  color: var(--color-text-primary, var(--cg-text-primary));
  font-family: var(--font-sans, var(--cg-font-sans));
  font-size: 13px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}

button,
input,
select,
textarea {
  font-family: inherit;
  font-size: inherit;
  color: inherit;
}

.cg-shell {
  max-width: 900px;
  margin: 0 auto;
  padding: 10px 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cg-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border-primary, var(--cg-border-primary));
}

.cg-brand {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.cg-logo {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.cg-title {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.cg-header-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.cg-subheader {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.cg-conv-select-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1 1 170px;
}

.cg-select,
.cg-input,
.cg-textarea {
  background: var(--color-background-secondary, var(--cg-bg-secondary));
  color: var(--color-text-primary, var(--cg-text-primary));
  border: 1px solid var(--color-border-secondary, var(--cg-border-secondary));
  border-radius: var(--border-radius-sm, var(--cg-radius-sm));
  padding: 5px 8px;
  font-size: 12px;
  outline: none;
}

.cg-select {
  max-width: 100%;
  min-width: 0;
  width: 100%;
  font-family: var(--font-mono, var(--cg-font-mono));
  text-overflow: ellipsis;
}

.cg-select:focus-visible,
.cg-input:focus-visible,
.cg-textarea:focus-visible,
.cg-btn:focus-visible,
.cg-conv-card:focus-visible {
  outline: 2px solid var(--cg-accent);
  outline-offset: 1px;
}

.cg-badge-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
}

.cg-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: var(--border-radius-full, 999px);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
  border: 1px solid transparent;
}

.cg-pill-ok {
  background: var(--color-background-success, var(--cg-bg-success));
  color: var(--color-text-success, var(--cg-text-success));
}

.cg-pill-warn {
  background: var(--color-background-warning, var(--cg-bg-warning));
  color: var(--color-text-warning, var(--cg-text-warning));
}

.cg-pill-limit {
  background: var(--color-background-danger, var(--cg-bg-danger));
  color: var(--color-text-danger, var(--cg-text-danger));
}

.cg-pill-muted {
  background: var(--color-background-tertiary, var(--cg-bg-tertiary));
  color: var(--color-text-secondary, var(--cg-text-secondary));
  border-color: var(--color-border-primary, var(--cg-border-primary));
}

.cg-pill-info {
  background: var(--color-background-info, var(--cg-bg-info));
  color: var(--color-text-info, var(--cg-text-info));
}

.cg-model-badge {
  max-width: 185px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 7px;
  border-radius: var(--border-radius-sm, var(--cg-radius-sm));
  background: var(--color-background-tertiary, var(--cg-bg-tertiary));
  color: var(--color-text-secondary, var(--cg-text-secondary));
  border: 1px solid var(--color-border-primary, var(--cg-border-primary));
  font-size: 11px;
  font-family: var(--font-mono, var(--cg-font-mono));
}

.cg-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 9px;
  border-radius: var(--border-radius-sm, var(--cg-radius-sm));
  border: 1px solid var(--color-border-secondary, var(--cg-border-secondary));
  background: var(--color-background-secondary, var(--cg-bg-secondary));
  color: var(--color-text-primary, var(--cg-text-primary));
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s ease, border-color 0.12s ease, opacity 0.12s ease;
}

.cg-btn:hover:not(:disabled) {
  background: var(--color-background-tertiary, var(--cg-bg-tertiary));
}

.cg-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.cg-btn-primary {
  background: var(--cg-accent);
  border-color: var(--cg-accent);
  color: var(--cg-accent-text);
}

.cg-btn-primary:hover:not(:disabled) {
  background: var(--cg-accent-hover);
  border-color: var(--cg-accent-hover);
}

.cg-btn-active {
  background: var(--color-background-info, var(--cg-bg-info));
  color: var(--color-text-info, var(--cg-text-info));
  border-color: var(--color-border-info, var(--cg-accent));
}

.cg-btn-sm {
  padding: 3px 7px;
  font-size: 11px;
}

.cg-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.cg-card {
  background: var(--color-background-secondary, var(--cg-bg-secondary));
  border: 1px solid var(--color-border-primary, var(--cg-border-primary));
  border-radius: var(--border-radius-md, var(--cg-radius-md));
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cg-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cg-card-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-text-secondary, var(--cg-text-secondary));
}

.cg-muted {
  color: var(--color-text-tertiary, var(--cg-text-tertiary));
  font-size: 11px;
}

.cg-mono {
  font-family: var(--font-mono, var(--cg-font-mono));
}

.cg-nums {
  font-variant-numeric: tabular-nums;
}

.cg-hero-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
}

.cg-hero-value {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.cg-hero-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-secondary, var(--cg-text-secondary));
}

.cg-bar-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cg-bar-labels {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
}

.cg-track {
  position: relative;
  width: 100%;
  height: 8px;
  border-radius: var(--border-radius-full, 999px);
  background: var(--cg-track);
  overflow: hidden;
}

.cg-track-sm {
  height: 6px;
}

.cg-fill {
  height: 100%;
  border-radius: var(--border-radius-full, 999px);
  transition: width 0.2s ease;
}

.cg-fill-ok {
  background: var(--color-text-success, var(--cg-text-success));
}

.cg-fill-warn {
  background: var(--color-text-warning, var(--cg-text-warning));
}

.cg-fill-limit {
  background: var(--color-text-danger, var(--cg-text-danger));
}

.cg-fill-info {
  background: var(--color-text-info, var(--cg-text-info));
}

.cg-fill-muted {
  background: var(--color-text-tertiary, var(--cg-text-tertiary));
}

.cg-warn-tick {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--color-text-primary, var(--cg-text-primary));
  opacity: 0.35;
}

.cg-metrics-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.cg-metric-card {
  background: var(--color-background-secondary, var(--cg-bg-secondary));
  border: 1px solid var(--color-border-primary, var(--cg-border-primary));
  border-radius: var(--border-radius-md, var(--cg-radius-md));
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cg-metric-label {
  font-size: 11px;
  color: var(--color-text-tertiary, var(--cg-text-tertiary));
}

.cg-metric-val {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.cg-metric-sub {
  font-size: 10px;
  color: var(--color-text-tertiary, var(--cg-text-tertiary));
  font-variant-numeric: tabular-nums;
}

.cg-spark-wrap {
  width: 100%;
  height: 96px;
}

.cg-spark-svg {
  width: 100%;
  height: 96px;
  display: block;
}

.cg-cat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.cg-cat-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 4px 7px;
  border-radius: var(--border-radius-sm, var(--cg-radius-sm));
  background: var(--color-background-tertiary, var(--cg-bg-tertiary));
  font-size: 11px;
}

.cg-cat-name {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-secondary, var(--cg-text-secondary));
}

.cg-cat-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.cg-stacked-bar {
  display: flex;
  width: 100%;
  height: 5px;
  border-radius: var(--border-radius-full, 999px);
  overflow: hidden;
  background: var(--cg-track);
  gap: 1px;
}

.cg-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 11px;
}

.cg-table th {
  text-align: left;
  padding: 5px 4px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--color-text-tertiary, var(--cg-text-tertiary));
  border-bottom: 1px solid var(--color-border-primary, var(--cg-border-primary));
}

.cg-table td {
  padding: 6px 4px;
  border-bottom: 1px solid var(--color-border-primary, var(--cg-border-primary));
  vertical-align: top;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cg-table tr:last-child td {
  border-bottom: none;
}

.cg-col-step { width: 38px; }
.cg-col-tool { width: 92px; }
.cg-col-tok { width: 58px; text-align: right; }

.cg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cg-banner {
  padding: 7px 10px;
  border-radius: var(--border-radius-sm, var(--cg-radius-sm));
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid transparent;
}

.cg-banner-error {
  background: var(--color-background-danger, var(--cg-bg-danger));
  color: var(--color-text-danger, var(--cg-text-danger));
  border-color: var(--color-border-danger, var(--cg-text-danger));
}

.cg-banner-info {
  background: var(--color-background-info, var(--cg-bg-info));
  color: var(--color-text-info, var(--cg-text-info));
  border-color: var(--color-border-info, var(--cg-accent));
}

.cg-banner-success {
  background: var(--color-background-success, var(--cg-bg-success));
  color: var(--color-text-success, var(--cg-text-success));
  border-color: var(--color-border-success, var(--cg-text-success));
}

.cg-form-grid {
  display: grid;
  grid-template-columns: repeat(1, minmax(0, 1fr));
  gap: 9px;
}

.cg-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cg-field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cg-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary, var(--cg-text-secondary));
}

.cg-preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.cg-segmented {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
}

.cg-textarea {
  width: 100%;
  min-height: 150px;
  resize: vertical;
  font-family: var(--font-mono, var(--cg-font-mono));
  font-size: 11px;
  line-height: 1.45;
}

.cg-conv-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.cg-conv-card {
  width: 100%;
  text-align: left;
  background: var(--color-background-secondary, var(--cg-bg-secondary));
  border: 1px solid var(--color-border-primary, var(--cg-border-primary));
  border-radius: var(--border-radius-md, var(--cg-radius-md));
  padding: 9px 11px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}

.cg-conv-card:hover {
  border-color: var(--cg-accent);
  background: var(--color-background-tertiary, var(--cg-bg-tertiary));
}

.cg-conv-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.cg-conv-id {
  font-family: var(--font-mono, var(--cg-font-mono));
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.cg-conv-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 11px;
  color: var(--color-text-tertiary, var(--cg-text-tertiary));
}

.cg-empty {
  padding: 18px 12px;
  text-align: center;
  color: var(--color-text-secondary, var(--cg-text-secondary));
}

@media (min-width: 540px) {
  .cg-metrics-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .cg-cat-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .cg-form-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .cg-col-tool {
    width: 140px;
  }
}
`;

const stripUnusedZodLocales = {
  name: "strip-unused-zod-locales",
  setup(buildApi) {
    // Keep only English locale in the single-file browser bundle (<512 KiB).
    buildApi.onLoad(
      { filter: /zod[/\\]v4[/\\]locales[/\\]index\.js$/ },
      () => ({
        contents: 'export { default as en } from "./en.js";',
        loader: "js",
      }),
    );
  },
};

async function build() {
  const result = await esbuild.build({
    entryPoints: [ENTRY_PATH],
    bundle: true,
    minify: true,
    format: "iife",
    platform: "browser",
    target: ["es2022"],
    legalComments: "none",
    plugins: [stripUnusedZodLocales],
    write: false,
  });

  if (!result.outputFiles || result.outputFiles.length === 0) {
    throw new Error("esbuild produced no output files");
  }

  let bundledJs = result.outputFiles[0].text;
  // Prevent any embedded string literal from prematurely closing <script>.
  bundledJs = bundledJs.replace(/<\/script/gi, "<\\/script");

  const compactCss = DASHBOARD_CSS.replace(/\s+/g, " ").trim();
  const html = [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    "<title>Context Guard</title>",
    `<style>${compactCss}</style>`,
    "</head>",
    "<body>",
    '<div id="app"></div>',
    `<script>${bundledJs}</script>`,
    "</body>",
    "</html>",
  ].join("");

  const byteSize = Buffer.byteLength(html, "utf8");
  if (!/^<!doctype html>/i.test(html)) {
    throw new Error("Bundle must begin with <!doctype html>");
  }
  if (/<script[^>]*\bsrc\s*=/i.test(html) || /<link\b/i.test(html)) {
    throw new Error("CSP violation: external <script src> or <link> detected");
  }
  if (byteSize >= MAX_BUNDLE_BYTES) {
    throw new Error(
      `Bundle size ${byteSize} bytes exceeds 512 KiB (${MAX_BUNDLE_BYTES} bytes)`,
    );
  }

  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, html, "utf8");
  const kib = (byteSize / 1024).toFixed(1);
  console.log(`Built ${OUTPUT_PATH} (${byteSize} bytes / ${kib} KiB)`);
}

build().catch((err) => {
  console.error(err);
  process.exit(1);
});
